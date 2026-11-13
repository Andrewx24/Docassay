"""LlamaParse mapping and the upload/poll client, with no network calls."""

import json
import os
from pathlib import Path

import httpx
import pytest

from docassay import __version__
from docassay.adapters.base import AdapterError, RawResult
from docassay.adapters.llamaparse import (
    API_KEY,
    LlamaParseAdapter,
    llamaparse_cost_usd,
    llamaparse_to_document,
)
from docassay.adapters.registry import resolve
from docassay.models.document import Document, Source, Type

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures" / "adapters" / "llamaparse"
SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SOURCE = Source(path="files/sample.pdf", sha256=SHA, mime="application/pdf")
CASES = [("agentic/invoice", "agentic", "2026-09-29"), ("fast/edges", "fast", "2026-06-15")]


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

    monkeypatch.setattr("docassay.adapters.llamaparse.httpx.request", request)
    return calls


def test_recorded_results_match_expected_documents() -> None:
    for name, variant, version in CASES:
        raw = json.loads((ROOT / f"{name}.raw.json").read_text(encoding="utf-8"))
        document = llamaparse_to_document(raw, SOURCE, version, variant)
        expected = (ROOT / f"{name}.doc.json").read_text(encoding="utf-8")
        assert _canonical(document) == expected
        assert document.meta.parser == f"llamaparse:{variant}"
        assert document.meta.parser_version == version
        assert document.meta.adapter_version == __version__


def test_invoice_table_header_and_footer_box() -> None:
    raw = json.loads((ROOT / "agentic/invoice.raw.json").read_text(encoding="utf-8"))
    document = llamaparse_to_document(raw, SOURCE, "2026-09-29", "agentic")
    assert [block.type for block in document.blocks] == [
        Type.header,
        Type.heading,
        Type.paragraph,
        Type.list_item,
        Type.list_item,
        Type.footer,
    ]
    assert document.blocks[0].bbox is not None
    assert document.blocks[0].bbox.root == [72, 36, 192, 48]
    assert document.blocks[1].level == 1
    table = document.tables[0]
    assert table.header == ["Item", "Qty", "Price"]
    assert table.rows[0] == ["Widget", "2", ""]
    assert table.rows[1][2] == "1,200.00"
    assert table.markdown is not None and table.markdown.startswith("| Item")


def test_edges_scale_spans_and_failed_page() -> None:
    raw = json.loads((ROOT / "fast/edges.raw.json").read_text(encoding="utf-8"))
    document = llamaparse_to_document(raw, SOURCE, "2026-06-15", "fast")
    assert document.blocks[0].text == "Running"
    assert document.blocks[1].bbox is not None
    assert document.blocks[1].bbox.root == [20, 80, 100, 100]
    assert document.blocks[2].bbox is not None
    assert document.blocks[2].bbox.root == [1, 0, 11, 6]
    assert document.blocks[3].bbox is None
    assert document.tables[0].header == ["H", "Q"]
    assert document.tables[0].rows == [["H", "A & B"]]
    assert document.blocks[-1].type is Type.footer
    assert document.blocks[-1].text == "End"
    assert document.blocks[-1].page == 2
    assert [page.number for page in document.pages] == [1, 2]
    assert document.pages[1].width is None


def test_odd_items_and_html_only_tables() -> None:
    payload = {
        "items": {
            "pages": [
                "skip",
                {
                    "page_number": 2.0,
                    "page_width": 0,
                    "page_height": 10,
                    "items": [
                        {
                            "type": "table",
                            "html": "<table><tr><td>a</td><td colspan='2'>b</td></tr><tr><td>&#38;</td><td/><td>c &nope;</td></tr></table>",
                        },
                        {"type": "table", "rows": [["a"], ["b", "c"]], "md": ""},
                        {"type": "table", "rows": [["a"], "nope"]},
                        {"type": "header", "md": "  Top  "},
                        {
                            "type": "header",
                            "items": [{"type": "heading", "level": 3, "value": "In"}],
                        },
                        {"type": "link", "text": "site", "url": "https://example.com"},
                        {"type": "heading", "level": 2, "value": "H2"},
                        {"type": "list", "items": [{"type": "code", "value": "z = 1"}]},
                        "nope",
                        {
                            "type": "text",
                            "value": "nan",
                            "bbox": [{"x": float("nan"), "y": 1, "w": 1, "h": 1}],
                        },
                        {
                            "type": "text",
                            "value": "flip",
                            "bbox": [{"x": 5, "y": 4, "w": -3, "h": -1}],
                        },
                        {
                            "type": "text",
                            "value": "unit",
                            "bbox": [{"x": 0.2, "y": 0.2, "w": 0.2, "h": 0.2}],
                        },
                        {"type": "table", "rows": [[1.5, 2.0, float("nan"), True, {"n": 1}]]},
                        {"type": "table", "html": "<p>no table</p>"},
                        {
                            "type": "table",
                            "html": (
                                "<table><tr><td xcolspan=9 colspan=2>"
                                "<b>z</b> &ampersandtoolong; & x</td><td>no-close</tr></table>"
                            ),
                        },
                        {
                            "type": "table",
                            "rows": [["kept"]],
                            "html": "<tricky</tr>"
                            + "".join('<tr><td colspan="1">a</td></tr>' for _ in range(70)),
                        },
                        {
                            "type": "table",
                            "html": (
                                "<table><tr>hello</tr>"
                                "<tr><tdx>no</tdx><td colspan = 2>q</td>"
                                "<th rowspan=2>r</th><td colspan>s</td><td"
                                "</tr><tr><td>a < b &amp</td></tr></table>"
                            ),
                        },
                        {"type": 1, "md": "misc"},
                        {
                            "type": "header",
                            "items": [
                                {"type": "image", "caption": "Cap"},
                                {"type": "image", "caption": "", "md": "pic"},
                                {"type": "link", "text": "", "md": "L"},
                                {"type": "formula"},
                            ],
                        },
                        {
                            "type": "text",
                            "value": "partial",
                            "bbox": ["no", {"x": 1, "y": 2, "w": 3, "h": 4}],
                        },
                    ],
                },
                {"page_number": "2", "items": "nope"},
                {"page_number": True},
                {"page_number": 9},
            ]
        },
        "markdown": {"pages": ["no", {"page_number": 9, "header": "Nine", "footer": 1}]},
    }
    document = llamaparse_to_document(payload, SOURCE, "2026-09-28", None)
    assert document.meta.parser == "llamaparse"
    sized = next(page for page in document.pages if page.number == 2)
    assert sized.width is None
    assert sized.height == 10
    assert document.tables[0].header == ["a", "b", "b"]
    assert document.tables[0].rows == [["&", "", "c &nope;"]]
    assert document.tables[1].header == ["a", ""]
    assert document.tables[1].rows == [["b", "c"]]
    assert ["1.5", "2", "", "", ""] in [table.header for table in document.tables]
    assert ["kept"] in [table.header for table in document.tables]
    by_text = {block.text: block for block in document.blocks}
    assert by_text["Top"].type is Type.header
    assert by_text["In"].level is None
    assert by_text["site"].type is Type.other
    assert by_text["H2"].level == 2
    assert by_text["z = 1"].type is Type.code
    assert by_text["nan"].bbox is None
    assert by_text["flip"].bbox is not None
    assert by_text["flip"].bbox.root == [2, 3, 5, 4]
    assert by_text["unit"].bbox is None
    assert by_text["misc"].type is Type.other
    assert by_text["Cap"].type is Type.header
    assert by_text["L"].text == "L"
    assert by_text["partial"].bbox is not None
    assert by_text["partial"].bbox.root == [1, 2, 4, 6]
    assert by_text["Nine"].type is Type.header
    assert by_text["pic"].type is Type.header


def test_bad_payloads() -> None:
    with pytest.raises(AdapterError, match="JSON object"):
        llamaparse_to_document(["nope"], SOURCE, "2026-09-29", "fast")
    with pytest.raises(AdapterError, match="parserVersion is empty"):
        llamaparse_to_document({"items": {"pages": []}}, SOURCE, "", "fast")
    with pytest.raises(AdapterError, match="expand=items"):
        llamaparse_to_document({"items": {}}, SOURCE, "2026-09-29", "fast")


def test_cost_table() -> None:
    assert llamaparse_cost_usd("fast", 2) == 2 * 1.25 / 1000
    assert llamaparse_cost_usd("cost_effective", 1) == 3 * 1.25 / 1000
    assert llamaparse_cost_usd("agentic", 1) == 10 * 1.25 / 1000
    assert llamaparse_cost_usd("agentic_plus", 2) == 90 * 1.25 / 1000
    assert llamaparse_cost_usd("fast", 0) == 0
    assert llamaparse_cost_usd("other", 3) == 0
    assert llamaparse_cost_usd("fast", True) == 0  # type: ignore[arg-type]


def test_available_requires_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)
    adapter = resolve("llamaparse:agentic")
    assert isinstance(adapter, LlamaParseAdapter)
    ok, reason = adapter.available()
    assert ok is False
    assert reason == API_KEY
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "  ")
    assert adapter.available() == (False, API_KEY)
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "secret")
    assert adapter.available() == (True, "")


def test_cache_identity_omits_secrets() -> None:
    adapter = LlamaParseAdapter(
        "agentic",
        {
            "version": " 2026-09-29 ",
            "lang": "en",
            "apiKey": "nope",
            "timeout_s": 5,
            "baseUrl": "http://x",
        },
    )
    assert adapter.cache_identity() == {
        "variant": "agentic",
        "version": "2026-09-29",
        "options": {"lang": "en"},
    }


def test_fetch_polls_prices_and_records_the_reported_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "secret")
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF")
    invoice = json.loads((ROOT / "agentic/invoice.raw.json").read_text(encoding="utf-8"))
    calls = _script(
        monkeypatch,
        [
            (200, {"id": "pjb 1", "status": "PENDING"}),
            (200, {"job": {"status": " running "}}),
            (200, {"job": {"status": "RUNNING"}}),
            (200, invoice),
        ],
    )
    clock = _Clock()
    monkeypatch.setattr("docassay.adapters.llamaparse.time.monotonic", clock.monotonic)
    monkeypatch.setattr("docassay.adapters.llamaparse.time.sleep", clock.sleep)
    adapter = LlamaParseAdapter(
        "agentic",
        {
            "version": " latest ",
            "lang": "en",
            "apiKey": "nope",
            "timeout_s": 30,
            "baseUrl": "https://example.test/",
        },
    )
    raw = adapter.fetch(pdf)
    assert raw.parser_version == "2026-09-29"
    assert raw.cost_usd == llamaparse_cost_usd("agentic", 1)
    assert raw.payload == invoice
    assert calls[0][0] == "POST"
    assert calls[0][1] == "https://example.test/api/v2/parse/upload"
    sent = calls[0][2]
    assert sent["headers"] == {"Authorization": "Bearer secret"}
    data = sent["data"]
    assert isinstance(data, dict)
    assert json.loads(str(data["configuration"])) == {
        "tier": "agentic",
        "version": "latest",
        "lang": "en",
    }
    files = sent["files"]
    assert isinstance(files, dict)
    assert files["file"][0] == "sample.pdf"
    assert (
        calls[1][1]
        == "https://example.test/api/v2/parse/pjb%201?expand=items,markdown,job_metadata"
    )
    assert clock.slept == [1.0, 2.0]
    document = adapter.to_document(raw, SOURCE)
    assert document.meta.parser == "llamaparse:agentic"


def test_version_fallbacks_and_terminal_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "secret")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    clock = _Clock()
    monkeypatch.setattr("docassay.adapters.llamaparse.time.monotonic", clock.monotonic)
    monkeypatch.setattr("docassay.adapters.llamaparse.time.sleep", clock.sleep)

    def run(body: dict[str, object], version: str = "latest") -> RawResult:
        _script(monkeypatch, [(200, {"id": "job-1"}), (200, body)])
        return LlamaParseAdapter("fast", {"version": version, "timeout_s": 10}).fetch(pdf)

    pinned = run({"job": {"status": "COMPLETED"}, "markdown": {"pages": [{}, {}]}})
    assert pinned.parser_version == "unknown"
    assert pinned.cost_usd == llamaparse_cost_usd("fast", 2)
    dated = run(
        {
            "job": {"status": "COMPLETED", "parameters": {"version": " 2026-09-28 "}},
            "items": {"pages": []},
        },
        version="latest",
    )
    assert dated.parser_version == "2026-09-28"
    assert dated.cost_usd == 0
    meta = run(
        {
            "job": {"status": "COMPLETED"},
            "job_metadata": {"parameters": {"version": "2026-06-15"}},
            "items": {"pages": [{}]},
        }
    )
    assert meta.parser_version == "2026-06-15"
    raw_params = run(
        {
            "job": {"status": "COMPLETED"},
            "raw_parameters": {"parameters": {"version": "2026-09-28"}},
            "items": {"pages": [{}]},
        }
    )
    assert raw_params.parser_version == "2026-09-28"
    direct = run(
        {
            "job": {"status": "COMPLETED"},
            "raw_parameters": {"version": "2026-06-15"},
            "items": {"pages": [{}]},
        }
    )
    assert direct.parser_version == "2026-06-15"
    meta_version = run(
        {
            "job": {"status": "COMPLETED"},
            "job_metadata": {"version": "2026-09-29"},
            "items": {"pages": [{}]},
        }
    )
    assert meta_version.parser_version == "2026-09-29"
    requested = run(
        {"job": {"status": "COMPLETED"}, "items": {"pages": [{}]}}, version="2026-01-08"
    )
    assert requested.parser_version == "2026-01-08"
    nested = _script(
        monkeypatch,
        [
            (200, {"job": {"id": "nested"}}),
            (200, {"job": {"status": "COMPLETED"}, "items": {"pages": [{}]}}),
        ],
    )
    assert LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf).parser_version == "unknown"
    assert "nested" in nested[1][1]
    with pytest.raises(AdapterError, match="FAILED on"):
        _script(
            monkeypatch,
            [
                (200, {"id": "job-1"}),
                (200, {"job": {"status": "FAILED", "error_message": " ocr crashed "}}),
            ],
        )
        LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)
    with pytest.raises(AdapterError, match="conversion failed"):
        _script(monkeypatch, [(200, {"id": "job-1"}), (200, {"job": {"status": "CANCELLED"}})])
        LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)


def test_timeout_http_errors_and_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "secret")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    clock = _Clock()
    monkeypatch.setattr("docassay.adapters.llamaparse.time.monotonic", clock.monotonic)
    monkeypatch.setattr("docassay.adapters.llamaparse.time.sleep", clock.sleep)
    _script(
        monkeypatch,
        [
            (200, {"id": "job-1"}),
            (200, {"job": {"status": "PENDING"}}),
            (200, {"job": {"status": "PENDING"}}),
            (200, {"job": {"status": "PENDING"}}),
        ],
    )
    with pytest.raises(AdapterError, match="timed out after 1.5s"):
        LlamaParseAdapter("fast", {"timeout_s": 1.5}).fetch(pdf)
    assert clock.slept == [1.0, 0.5]
    long = "x" * 250
    _script(monkeypatch, [(500, long)])
    with pytest.raises(AdapterError, match="HTTP 500") as exc:
        LlamaParseAdapter("cost_effective", {"timeoutS": 3}).fetch(pdf)
    assert "x" * 200 in str(exc.value)
    _script(monkeypatch, [(400, "no\npe")])
    with pytest.raises(AdapterError, match="no pe"):
        LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)
    _script(
        monkeypatch,
        [
            (200, {"id": "job-1"}),
            (200, {"job": {"status": 1}}),
            (200, {"job": {"status": "COMPLETED"}, "items": {"pages": [{}]}}),
        ],
    )
    assert LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf).parser_version == "unknown"
    _script(monkeypatch, [(200, "not-json")])
    with pytest.raises(AdapterError, match="non-JSON"):
        LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)
    _script(monkeypatch, [(200, [1])])
    with pytest.raises(AdapterError, match="non-object"):
        LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)
    _script(monkeypatch, [(200, {"status": "PENDING"})])
    with pytest.raises(AdapterError, match="did not return a job id"):
        LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)

    def explode(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("docassay.adapters.llamaparse.httpx.request", explode)
    with pytest.raises(AdapterError, match="refused"):
        LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)
    missing = tmp_path / "gone.pdf"
    with pytest.raises(AdapterError, match="path not found"):
        LlamaParseAdapter("fast", {}).fetch(missing)
    with pytest.raises(AdapterError, match="tier variant"):
        LlamaParseAdapter(None, {}).fetch(pdf)
    for bad in ("no", 0, True, float("inf")):
        with pytest.raises(AdapterError, match="timeout_s"):
            LlamaParseAdapter("fast", {"timeout_s": bad}).fetch(pdf)
    monkeypatch.delenv("LLAMA_CLOUD_API_KEY", raising=False)
    with pytest.raises(AdapterError, match="LLAMA_CLOUD_API_KEY"):
        LlamaParseAdapter("agentic_plus", {}).fetch(pdf)


def test_base_url_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "secret")
    monkeypatch.setenv("LLAMA_CLOUD_BASE_URL", "https://eu.example.test/")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    calls = _script(
        monkeypatch,
        [
            (200, {"id": "job-1"}),
            (
                200,
                {"job": {"status": "COMPLETED", "version": "2026-06-15"}, "items": {"pages": [{}]}},
            ),
        ],
    )
    clock = _Clock()
    monkeypatch.setattr("docassay.adapters.llamaparse.time.sleep", clock.sleep)
    monkeypatch.setattr("docassay.adapters.llamaparse.time.monotonic", clock.monotonic)
    raw = LlamaParseAdapter("agentic_plus", {"base_url": ""}).fetch(pdf)
    assert calls[0][1] == "https://eu.example.test/api/v2/parse/upload"
    assert raw.parser_version == "2026-06-15"
    assert raw.cost_usd == llamaparse_cost_usd("agentic_plus", 1)
    monkeypatch.delenv("LLAMA_CLOUD_BASE_URL", raising=False)
    default_calls = _script(
        monkeypatch,
        [
            (200, {"id": "job-1"}),
            (200, {"job": {"status": "COMPLETED"}, "items": {"pages": [{}]}}),
        ],
    )
    LlamaParseAdapter("fast", {"timeout_s": 5}).fetch(pdf)
    assert default_calls[0][1].startswith("https://api.cloud.llamaindex.ai/api/v2/parse/upload")


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 to call LlamaParse")
def test_live_parses_one_pdf(tmp_path: Path) -> None:
    if not os.environ.get("LLAMA_CLOUD_API_KEY"):
        pytest.skip("LLAMA_CLOUD_API_KEY is not set")
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    raw = LlamaParseAdapter("fast", {"timeout_s": 120}).fetch(pdf)
    assert raw.parser_version
    document = LlamaParseAdapter("fast", {}).to_document(raw, SOURCE)
    assert document.meta.parser == "llamaparse:fast"
