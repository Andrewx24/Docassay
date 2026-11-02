"""Reducto mapping and the upload/parse/extract client, with no network calls."""

import json
import os
from pathlib import Path

import httpx
import pytest

from docassay import __version__
from docassay.adapters.base import AdapterError
from docassay.adapters.reducto import (
    API_KEY,
    ReductoAdapter,
    reducto_cost_usd,
    reducto_to_document,
)
from docassay.adapters.registry import resolve
from docassay.models.document import Document, Source, Type

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures" / "adapters" / "reducto"
SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SOURCE = Source(path="files/sample.pdf", sha256=SHA, mime="application/pdf")
CASES = ["parse/invoice", "parse/edges", "extract/invoice", "extract/nested"]


def _canonical(document: Document) -> str:
    payload = document.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(_integral(payload), indent=2, ensure_ascii=False) + "\n"


def _integral(value: object) -> object:
    if isinstance(value, float) and value.is_integer() and abs(value) <= 2**53:
        return int(value)
    if isinstance(value, dict):
        return {key: _integral(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_integral(item) for item in value]
    return value


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def perf_counter(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _script(
    monkeypatch: pytest.MonkeyPatch, responses: list[tuple[int, object]]
) -> list[tuple[str, str, dict[str, object]]]:
    calls: list[tuple[str, str, dict[str, object]]] = []
    pending = list(responses)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append((method, url, kwargs))
        status, body = pending.pop(0)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    monkeypatch.setattr("docassay.adapters.reducto.httpx.request", request)
    return calls


def _clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr("docassay.adapters.reducto.time.monotonic", clock.monotonic)
    monkeypatch.setattr("docassay.adapters.reducto.time.perf_counter", clock.perf_counter)
    monkeypatch.setattr("docassay.adapters.reducto.time.sleep", clock.sleep)
    return clock


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF")
    return path


def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDUCTO_API_KEY", "secret-key")


def test_recorded_results_match_expected_documents() -> None:
    for name in CASES:
        raw = json.loads((ROOT / f"{name}.raw.json").read_text(encoding="utf-8"))
        document = reducto_to_document(raw, SOURCE, "r-1")
        assert _canonical(document) == (ROOT / f"{name}.doc.json").read_text(encoding="utf-8")
        assert document.meta.parser == "reducto"
        assert document.meta.parser_version == "r-1"
        assert document.meta.adapter_version == __version__


def test_invoice_maps_blocks_tables_and_chunks() -> None:
    raw = json.loads((ROOT / "parse/invoice.raw.json").read_text(encoding="utf-8"))
    document = reducto_to_document(raw, SOURCE, "r-1")
    by_text = {block.text: block for block in document.blocks}
    assert by_text["Invoice"].type is Type.title
    assert by_text["Invoice"].bbox is not None
    assert by_text["Invoice"].bbox.root == [61.2, 39.6, 244.8, 71.28]
    assert by_text["## Terms"].type is Type.heading
    assert by_text["## Terms"].level == 2
    assert by_text["• Net 30"].type is Type.list_item
    assert by_text["Acme Billing"].type is Type.header
    assert by_text["Page 1"].type is Type.footer
    assert by_text["Chart of spend"].type is Type.caption
    assert document.tables[0].header == ["Item", "Qty", "Price"]
    assert document.tables[0].rows == [["Widget", "2", ""], ["Gadget", "1", "1,200.00"]]
    assert document.chunks is not None
    assert document.chunks[0].heading_path is not None
    assert [item.root for item in document.chunks[0].heading_path] == ["Invoice", "## Terms"]
    assert document.chunks[1].heading_path is not None
    assert [item.root for item in document.chunks[1].heading_path] == ["Invoice", "## Late fees"]
    assert "t1" in [item.root for item in document.chunks[0].block_ids]
    assert document.fields is None


def test_edges_scale_span_and_omit_unknown_pages() -> None:
    raw = json.loads((ROOT / "parse/edges.raw.json").read_text(encoding="utf-8"))
    document = reducto_to_document(raw, SOURCE, "r-1")
    scaled = next(block for block in document.blocks if block.text == "Scaled")
    assert scaled.bbox is not None
    assert scaled.bbox.root == [20.0, 80.0, 100.0, 100.0]
    checkbox = next(block for block in document.blocks if block.text == "☑ Agree")
    assert checkbox.type is Type.other
    assert checkbox.bbox is None
    assert all(block.text.strip() for block in document.blocks)
    assert document.tables[0].header == ["H", "Q"]
    assert document.tables[0].rows[0] == ["H", "A & B"]
    assert document.tables[1].header == ["A", "B", ""]
    page_two = next(block for block in document.blocks if block.text == "Page two")
    assert page_two.page == 2
    assert page_two.bbox is None
    assert document.pages[1].width is None


def test_extract_fields_keep_citations() -> None:
    invoice = reducto_to_document(
        json.loads((ROOT / "extract/invoice.raw.json").read_text(encoding="utf-8")),
        SOURCE,
        "r-1",
    )
    assert invoice.fields is not None
    vendor = invoice.fields["vendor"]
    assert vendor.confidence == 0.95
    assert vendor.citations is not None
    assert vendor.citations[0].block_id == "b2"
    assert vendor.citations[0].bbox is not None
    total = invoice.fields["total"]
    assert total.value == 1200
    assert total.citations is not None
    assert total.citations[0].block_id == "t1"
    nested = reducto_to_document(
        json.loads((ROOT / "extract/nested.raw.json").read_text(encoding="utf-8")),
        SOURCE,
        "r-1",
    )
    assert nested.fields is not None
    assert nested.fields["bill_to.name"].citations is not None
    assert nested.fields["bill_to.name"].citations[0].bbox is None
    assert nested.fields["bill_to.name"].citations[0].block_id == "b1"
    assert nested.fields["items.0.qty"].value == 2
    assert nested.fields["paid"].value is False
    assert nested.fields["notes"].value == '{"extra":true}'


def test_cost_table() -> None:
    assert reducto_cost_usd("r-1", 1, False) == 0.01
    assert reducto_cost_usd("legacy", 2, False) == 0.03
    assert reducto_cost_usd("r-1", 1, True) == 0.03
    assert reducto_cost_usd("custom", 4, False) == 0
    assert reducto_cost_usd("custom", 4, True) == 0.08
    assert reducto_cost_usd("r-1", 0, True) == 0
    assert reducto_cost_usd("r-1", True, False) == 0  # type: ignore[arg-type]


def test_available_and_cache_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDUCTO_API_KEY", raising=False)
    adapter = ReductoAdapter(None, {"model": " legacy ", "timeout_s": 5, "api_key": "nope"})
    assert adapter.available() == (False, API_KEY)
    monkeypatch.setenv("REDUCTO_API_KEY", "  token  ")
    assert adapter.available() == (True, "")
    identity = adapter.cache_identity()
    assert identity["model"] == "legacy"
    assert "api_key" not in identity["options"]
    assert "timeout_s" not in identity["options"]
    assert "token" not in json.dumps(identity)
    nested = ReductoAdapter(None, {"settings": {"model": "r-1"}, "extractSchema": "schema.json"})
    assert nested.cache_identity()["model"] == "r-1"
    assert nested.cache_identity()["extract_schema"] == "schema.json"
    assert resolve("reducto").id == "reducto"


def test_fetch_uploads_parses_and_extracts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _key(monkeypatch)
    monkeypatch.setenv("REDUCTO_BASE_URL", "https://example.test/root///")
    schema = tmp_path / "invoice.schema.json"
    schema.write_text(
        '{"type": "object", "properties": {"vendor": {"type": "string"}}}', encoding="utf-8"
    )
    pdf = _pdf(tmp_path)
    calls = _script(
        monkeypatch,
        [
            (200, {"file_id": " reducto://file "}),
            (
                200,
                {
                    "job_id": "job-1",
                    "result": {"type": "full", "chunks": []},
                    "usage": {"num_pages": 2, "credits": 9},
                },
            ),
            (200, {"job_id": "ex-1", "result": {"vendor": {"value": "Acme", "citations": []}}}),
        ],
    )
    clock = _clock(monkeypatch)
    clock.t = 1.5
    raw = ReductoAdapter(
        None,
        {
            "model": "r-1",
            "extract_schema": "invoice.schema.json",
            "page_size": [612, 792],
            "retrieval": {"chunking": {"chunk_mode": "page"}},
            "timeout_s": 30,
        },
    ).fetch(pdf)
    upload, parse, extract = calls
    assert upload[0] == "POST"
    assert upload[1] == "https://example.test/root/upload"
    assert upload[2]["headers"] == {"Authorization": "Bearer secret-key"}
    files = upload[2]["files"]
    assert isinstance(files, dict)
    assert files["file"][0] == "sample.pdf"
    assert parse[1].endswith("/parse")
    body = parse[2]["json"]
    assert isinstance(body, dict)
    assert body["input"] == "reducto://file"
    assert body["settings"]["model"] == "r-1"
    assert body["retrieval"]["chunking"]["chunk_mode"] == "page"
    assert "extract_schema" not in body
    assert "page_size" not in body
    extract_body = extract[2]["json"]
    assert isinstance(extract_body, dict)
    assert extract_body["input"] == "jobid://job-1"
    assert extract_body["settings"]["citations"]["enabled"] is True
    assert isinstance(raw.payload, dict)
    assert raw.payload["pages"] == [{"width": 612, "height": 792}]
    assert raw.parser_version == "r-1"
    assert raw.cost_usd == 0.06
    assert raw.latency_ms == 0


def test_model_settings_and_reported_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _key(monkeypatch)
    monkeypatch.delenv("REDUCTO_BASE_URL", raising=False)
    calls = _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (
                200,
                {
                    "job_id": "job-9",
                    "settings": {"model": "legacy"},
                    "result": {"type": "full", "chunks": []},
                    "usage": {"num_pages": True},
                },
            ),
        ],
    )
    raw = ReductoAdapter(
        None,
        {"baseUrl": "https://platform.reducto.ai/", "settings": {"model": "legacy", "timeout": 1}},
    ).fetch(_pdf(tmp_path))
    body = calls[1][2]["json"]
    assert isinstance(body, dict)
    assert body["settings"]["model"] == "legacy"
    assert body["settings"]["timeout"] == 1
    assert calls[1][1] == "https://platform.reducto.ai/parse"
    assert raw.parser_version == "legacy"
    assert raw.cost_usd == 0


def test_url_result_and_poll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _key(monkeypatch)
    calls = _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (200, {"job_id": "job 1", "status": "Pending"}),
            (200, {"job_id": "job 1", "status": "Running"}),
            (
                200,
                {
                    "job_id": "job 1",
                    "status": "In Progress",
                    "result": {"type": "url", "url": "https://files.test/chunks"},
                },
            ),
            (200, [{"content": "remote", "blocks": [{"type": "Text", "content": "Hi"}]}]),
        ],
    )
    clock = _clock(monkeypatch)
    raw = ReductoAdapter(None, {"timeoutS": 9, "pageSize": [100, 100]}).fetch(_pdf(tmp_path))
    assert calls[2][0] == "GET"
    assert calls[2][1] == "https://platform.reducto.ai/job/job%201"
    assert calls[4][1] == "https://files.test/chunks"
    assert clock.slept == [1.0]
    assert isinstance(raw.payload, dict)
    chunks = raw.payload["parse"]["result"]["chunks"]
    assert chunks[0]["content"] == "remote"
    assert raw.cost_usd == 0.01


def test_poll_timeout_failure_and_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _key(monkeypatch)
    pdf = _pdf(tmp_path)
    _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (200, {"job_id": "slow", "status": "Completing"}),
            (200, {"job_id": "slow", "status": "Completing"}),
            (200, {"job_id": "slow", "status": "Completing"}),
        ],
    )
    clock = _clock(monkeypatch)
    with pytest.raises(AdapterError, match="timed out after 1.0s"):
        ReductoAdapter(None, {"timeout_s": 1}).fetch(pdf)
    assert clock.slept == [1.0]

    _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (200, {"job_id": "bad", "status": "Failed", "error": "boom"}),
        ],
    )
    with pytest.raises(AdapterError, match="job bad FAILED on"):
        ReductoAdapter(None, {}).fetch(pdf)

    _script(monkeypatch, [(503, "no\npe")])
    with pytest.raises(AdapterError, match="HTTP 503") as http:
        ReductoAdapter(None, {}).fetch(pdf)
    assert "no pe" in str(http.value)

    def explode(method: str, url: str, **kwargs: object) -> httpx.Response:
        del method, url, kwargs
        raise httpx.ConnectError("down")

    monkeypatch.setattr("docassay.adapters.reducto.httpx.request", explode)
    with pytest.raises(AdapterError, match="reducto failed on"):
        ReductoAdapter(None, {}).fetch(pdf)


def test_config_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = _pdf(tmp_path)
    monkeypatch.delenv("REDUCTO_API_KEY", raising=False)
    with pytest.raises(AdapterError, match="no variants"):
        ReductoAdapter("fast", {}).fetch(pdf)
    with pytest.raises(AdapterError, match="path not found"):
        ReductoAdapter(None, {}).fetch(tmp_path / "missing.pdf")
    with pytest.raises(AdapterError, match="REDUCTO_API_KEY"):
        ReductoAdapter(None, {}).fetch(pdf)
    _key(monkeypatch)
    with pytest.raises(AdapterError, match="timeout_s"):
        ReductoAdapter(None, {"timeout_s": 0}).fetch(pdf)
    with pytest.raises(AdapterError, match="timeout_s"):
        ReductoAdapter(None, {"timeout_s": True}).fetch(pdf)
    with pytest.raises(AdapterError, match="extract_schema"):
        ReductoAdapter(None, {"extract_schema": "   "}).fetch(pdf)
    with pytest.raises(AdapterError, match="Looked in"):
        ReductoAdapter(None, {"extract_schema": "missing.schema.json"}).fetch(pdf)
    with pytest.raises(AdapterError, match="path not found"):
        ReductoAdapter(None, {"extract_schema": str(tmp_path / "no.json")}).fetch(pdf)
    bad = tmp_path / "bad.json"
    bad.write_text("[1]", encoding="utf-8")
    with pytest.raises(AdapterError, match="JSON object"):
        ReductoAdapter(None, {"extract_schema": str(bad)}).fetch(pdf)
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(AdapterError, match="cannot read extract schema"):
        ReductoAdapter(None, {"extract_schema": str(broken)}).fetch(pdf)
    _script(monkeypatch, [(200, {"nope": True})])
    with pytest.raises(AdapterError, match="file_id"):
        ReductoAdapter(None, {}).fetch(pdf)
    _script(monkeypatch, [(200, {"file_id": "reducto://f"}), (200, "[]")])
    with pytest.raises(AdapterError, match="non-object"):
        ReductoAdapter(None, {}).fetch(pdf)
    _script(monkeypatch, [(200, {"file_id": "reducto://f"}), (200, "nope")])
    with pytest.raises(AdapterError, match="non-JSON"):
        ReductoAdapter(None, {}).fetch(pdf)
    _script(
        monkeypatch,
        [(200, {"file_id": "reducto://f"}), (200, {"job_id": "j", "status": "Completed"})],
    )
    with pytest.raises(AdapterError, match="did not return a result"):
        ReductoAdapter(None, {}).fetch(pdf)


def test_schema_beside_the_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _key(monkeypatch)
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "docs"
    folder.mkdir()
    pdf = folder / "sample.pdf"
    pdf.write_bytes(b"%PDF")
    (folder / "fields.json").write_text('{"type":"object"}', encoding="utf-8")
    calls = _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (
                200,
                {
                    "job_id": "j",
                    "model": " r-1 ",
                    "result": {
                        "chunks": [
                            {"blocks": [{"type": "Text", "content": "Hi", "bbox": {"page": 1}}]}
                        ]
                    },
                    "usage": {"num_pages": 1.5},
                },
            ),
            (200, {"result": []}),
        ],
    )
    raw = ReductoAdapter(None, {"extract_schema": "fields.json"}).fetch(pdf)
    body = calls[2][2]["json"]
    assert isinstance(body, dict)
    assert body["instructions"]["schema"] == {"type": "object"}
    assert raw.parser_version == "r-1"
    assert raw.cost_usd == 0.03


def test_mapping_edges() -> None:
    wide = "<table><tr>" + "".join("<td>x</td>" for _ in range(65)) + "</tr></table>"
    html = (
        "<table><tr><td colspan=2>A</td><td xcolspan='3'>B</td><td/></tr>"
        "<tr><td><b>Z</b> &#38; &unknown;</td></tr><tr></tr><track></track>"
    )
    payload = {
        "parse": {
            "result": {
                "chunks": [
                    {
                        "content": "odd",
                        "blocks": [
                            {"type": "Table", "content": html},
                            {"type": "Table", "content": wide},
                            {"type": "Table", "content": "not a table"},
                            {"type": "Figure", "content": "  "},
                            {"type": "Section Header", "content": "# Top"},
                            {"type": "Section Header", "content": "### Deep"},
                            {"type": "Title", "content": "######"},
                            {"type": "Title", "content": "Replaced"},
                            {"type": 4, "content": "mystery"},
                            {
                                "type": "Text",
                                "content": "flip",
                                "bbox": {
                                    "left": 0.2,
                                    "top": 0.4,
                                    "width": -0.1,
                                    "height": 0.1,
                                    "original_page": 4,
                                    "page": 1,
                                },
                            },
                            "skip",
                        ],
                    },
                    "skip-chunk",
                    {"content": "tail", "blocks": [{"type": "Text", "content": "after"}]},
                ]
            },
        },
        "pages": [{"page": 4, "width": 100, "height": 200}, {"width": 10, "height": 10}],
        "extract": {
            "result": [
                {
                    "tags": [
                        {
                            "value": "a",
                            "citations": [
                                {
                                    "content": "after",
                                    "granular_confidence": {"extract_confidence": 0},
                                }
                            ],
                        },
                        {"value": "b", "citations": []},
                    ],
                    "flag": {
                        "value": True,
                        "citations": [{"granular_confidence": {"parse_confidence": 1}}],
                    },
                },
                {
                    "again": {
                        "value": None,
                        "citations": [{"granular_confidence": {"extract_confidence": True}}],
                    }
                },
            ]
        },
    }
    document = reducto_to_document(payload, SOURCE, "r-1")
    assert document.tables[0].header == ["A", "A", "B", ""]
    assert document.tables[0].rows[0][0] == "Z & &unknown;"
    assert document.tables[1].header is None
    assert document.tables[1].rows == [[wide]]
    assert document.tables[2].rows == [["not a table"]]
    titles = [block.text for block in document.blocks if block.type is Type.title]
    assert titles == ["######", "Replaced"]
    deep = next(block for block in document.blocks if block.text == "### Deep")
    assert deep.level == 3
    flipped = next(block for block in document.blocks if block.text == "flip")
    assert flipped.page == 4
    assert flipped.bbox is not None
    assert flipped.bbox.root[0] <= flipped.bbox.root[2]
    assert document.chunks is not None
    assert document.chunks[0].heading_path is not None
    assert [item.root for item in document.chunks[0].heading_path] == ["Replaced"]
    assert document.fields is not None
    assert document.fields["0.tags"].value == '["a","b"]'
    assert document.fields["0.flag"].confidence == 1
    assert document.fields["1.again"].value is None
    with pytest.raises(AdapterError, match="URL"):
        reducto_to_document({"result": {"type": "url", "url": "https://x"}}, SOURCE, "r-1")
    with pytest.raises(AdapterError, match="parserVersion"):
        reducto_to_document({"result": {"chunks": []}}, SOURCE, "")
    with pytest.raises(AdapterError, match="JSON object"):
        reducto_to_document([], SOURCE, "r-1")
    with pytest.raises(AdapterError, match="result.chunks"):
        reducto_to_document({"result": {}}, SOURCE, "r-1")


def test_unclosed_html_and_bad_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    document = reducto_to_document(
        {
            "result": {
                "chunks": [
                    {
                        "blocks": [
                            {"type": "Table", "content": "<table><tr><td>open"},
                            {
                                "type": "Table",
                                "content": "<table><tr><td colspan='no'>x</td></tr></table>",
                            },
                        ]
                    }
                ]
            }
        },
        SOURCE,
        "r-1",
    )
    assert document.tables[0].rows == [["<table><tr><td>open"]]
    assert document.tables[1].header == ["x"]
    _key(monkeypatch)
    _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (200, {"job_id": "j", "result": {"type": "url"}}),
        ],
    )
    with pytest.raises(AdapterError, match="no url"):
        ReductoAdapter(None, {}).fetch(_pdf(tmp_path))
    _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (200, {"job_id": "j", "result": {"type": "url", "url": "https://files.test/c"}}),
            (200, {"nope": 1}),
        ],
    )
    with pytest.raises(AdapterError, match="did not contain chunks"):
        ReductoAdapter(None, {}).fetch(_pdf(tmp_path))
    schema = tmp_path / "ok.json"
    schema.write_text("{}", encoding="utf-8")
    _script(
        monkeypatch,
        [
            (200, {"file_id": "reducto://f"}),
            (200, {"result": {"type": "full", "chunks": []}}),
        ],
    )
    with pytest.raises(AdapterError, match="job_id"):
        ReductoAdapter(None, {"extract_schema": str(schema)}).fetch(_pdf(tmp_path))


@pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 to call Reducto")
def test_live_parses_one_pdf(tmp_path: Path) -> None:
    if not os.environ.get("REDUCTO_API_KEY"):
        pytest.skip("REDUCTO_API_KEY is not set")
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    raw = ReductoAdapter(None, {"timeout_s": 120}).fetch(pdf)
    assert raw.parser_version
    document = ReductoAdapter(None, {}).to_document(raw, SOURCE)
    assert document.meta.parser == "reducto"
