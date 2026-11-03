"""Docling mapping against recorded DoclingDocument exports."""

import json
import os
from pathlib import Path

import pytest

from docassay import __version__
from docassay.adapters.base import AdapterError, RawResult
from docassay.adapters.docling import INSTALL, DoclingAdapter, docling_to_document
from docassay.models.document import Document, Source, Type

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures" / "adapters" / "docling"
SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SOURCE = Source(path="files/sample.pdf", sha256=SHA, mime="application/pdf")
CASES = ["digital-table", "scanned-page", "spans", "edges"]


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


def test_recorded_exports_match_expected_documents() -> None:
    for name in CASES:
        raw = json.loads((ROOT / f"{name}.raw.json").read_text(encoding="utf-8"))
        document = docling_to_document(raw, SOURCE, "2.87.0")
        expected = (ROOT / f"{name}.doc.json").read_text(encoding="utf-8")
        assert _canonical(document) == expected
        assert document.meta.parser == "docling"
        assert document.meta.parser_version == "2.87.0"
        assert document.meta.adapter_version == __version__


def test_digital_table_keeps_headers_caption_and_spans() -> None:
    raw = json.loads((ROOT / "digital-table.raw.json").read_text(encoding="utf-8"))
    document = docling_to_document(raw, SOURCE, "2.87.0")
    assert [block.type for block in document.blocks[:2]] == [Type.header, Type.header]
    assert document.blocks[0].text.startswith("Optimized Table Tokenization")
    assert document.blocks[0].bbox is not None
    assert document.blocks[0].bbox.root == [194.48, 93.66, 447.54, 102.04]
    assert any(
        block.type is Type.caption and block.text.startswith("Table 1.")
        for block in document.blocks
    )
    table = document.tables[0]
    assert table.header == [
        "# enc-layers",
        "# dec-layers",
        "Language",
        "TEDs simple",
        "TEDs complex",
        "TEDs all",
        "mAP (0.75)",
        "Inference time (secs)",
    ]
    assert table.rows[0][0] == "6"
    assert table.rows[0][3] == "0.965 0.969"
    assert table.bbox is not None
    assert table.bbox.root[1] == 337
    assert document.pages[0].width == 612
    assert document.pages[0].height == 792


def test_edges_scale_normalized_boxes_and_keep_unknown_labels() -> None:
    raw = json.loads((ROOT / "edges.raw.json").read_text(encoding="utf-8"))
    document = docling_to_document(raw, SOURCE, "2.87.0")
    assert [block.type for block in document.blocks] == [
        Type.other,
        Type.formula,
        Type.code,
        Type.footer,
    ]
    assert document.blocks[0].bbox is not None
    assert document.blocks[0].bbox.root == [20, 80, 100, 100]
    assert document.blocks[1].bbox is None
    assert document.blocks[1].page == 2
    assert document.blocks[3].bbox is not None
    assert document.blocks[3].bbox.root == [1, 380, 30, 390]
    assert [page.number for page in document.pages] == [1, 2]


def test_spans_repeat_a_merged_header() -> None:
    raw = json.loads((ROOT / "spans.raw.json").read_text(encoding="utf-8"))
    document = docling_to_document(raw, SOURCE, "2.87.0")
    assert document.tables[0].header == ["H", "H"]
    assert document.tables[0].rows == [["a", "b"]]
    assert document.tables[0].bbox is not None
    assert document.tables[0].bbox.root == [1, 2, 3, 4]


def test_bad_payload_and_missing_library(tmp_path: Path) -> None:
    adapter = DoclingAdapter(None, {"do_ocr": True})
    assert adapter.id == "docling"
    assert adapter.variants == ()
    assert adapter.cache_identity() == {"options": {"do_ocr": True}}
    with pytest.raises(AdapterError, match="DoclingDocument"):
        adapter.to_document(
            RawResult(payload={"text": "no"}, parser_version="2.87.0", latency_ms=0), SOURCE
        )
    with pytest.raises(AdapterError, match="parserVersion"):
        docling_to_document({"schema_name": "DoclingDocument"}, SOURCE, "")
    sample = tmp_path / "note.pdf"
    sample.write_bytes(b"%PDF-1.4\n")
    if adapter.available()[0]:
        return
    assert adapter.available() == (False, INSTALL)
    with pytest.raises(AdapterError, match="docassay\\[docling\\]"):
        adapter.fetch(sample)
    missing = tmp_path / "gone.pdf"
    with pytest.raises(AdapterError, match="path not found"):
        adapter.fetch(missing)


def test_odd_shapes_and_a_stand_in_converter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {
        "schema_name": "DoclingDocument",
        "pages": [
            "skip",
            {"page_no": 0, "size": {"width": 0, "height": -1}},
            {"size": {"width": True}},
            {"page_no": 4, "size": {"width": 0, "height": -1}},
            {"page_no": 3, "size": {"width": 50, "height": 80}},
        ],
        "body": {
            "children": [
                "not-a-ref",
                {"$ref": 1},
                {"$ref": "#/nope"},
                {"$ref": "#/nope/0"},
                {"$ref": "#/texts/9"},
                {"$ref": "#/texts/x"},
                {"$ref": "#/texts/1"},
                {"$ref": "#/texts/2"},
                {"$ref": "#/texts/0"},
                {"$ref": "#/texts/0"},
                {"$ref": "#/groups/0"},
                {"$ref": "#/pictures/0"},
                {"$ref": "#/tables/0"},
                {"$ref": "#/tables/1"},
                {"$ref": "#/tables/2"},
            ]
        },
        "groups": [{"children": [{"$ref": "#/texts/3"}]}],
        "texts": [
            {"text": "", "label": "text"},
            {
                "text": "Title",
                "label": "section_header",
                "prov": [
                    {
                        "page_no": 3,
                        "bbox": {"l": 8, "t": 1, "r": 2, "b": 4, "coord_origin": "TOPLEFT"},
                    }
                ],
            },
            {"text": "bad", "label": 1, "prov": [{"bbox": {"l": True, "t": 1, "r": 2, "b": 3}}]},
            {"text": "item", "label": "list_item"},
        ],
        "pictures": [
            {"text": "figure", "label": "picture", "children": [{"$ref": "#/texts/3"}]},
        ],
        "tables": [
            {"data": {"grid": [[{"text": "a"}], [{"text": "b"}, "cell"]]}},
            {"data": {"num_rows": True, "table_cells": ["nope", {"text": 1}]}},
            {"data": {}},
        ],
    }
    payload["texts"][2]["prov"][0]["bbox"]["t"] = float("nan")
    document = docling_to_document(payload, SOURCE, "2.87.0")
    assert [block.text for block in document.blocks] == ["Title", "bad", "item", "figure"]
    assert document.blocks[0].level == 1
    assert document.blocks[0].bbox is not None
    assert document.blocks[0].bbox.root == [2, 1, 8, 4]
    assert document.blocks[1].bbox is None
    assert document.tables[0].rows == [["a", ""], ["b", ""]]
    assert document.tables[0].header is None
    assert document.tables[1].rows == []
    assert document.pages[-1].number == 4

    class Status:
        name = "SUCCESS"

    class Exported:
        def export_to_dict(self) -> dict[str, str]:
            return {"schema_name": "DoclingDocument"}

    class Result:
        status = Status()
        document = Exported()

    class Converter:
        def convert(self, file_path: str) -> Result:
            assert file_path.endswith("note.pdf")
            return Result()

    class Module:
        DocumentConverter = Converter
        __version__ = "2.87.0"

    def fake_import(name: str) -> object:
        if name in {"docling", "docling.document_converter"}:
            return Module()
        raise ImportError(name)

    monkeypatch.setattr("docassay.adapters.docling.importlib.import_module", fake_import)
    sample = tmp_path / "note.pdf"
    sample.write_bytes(b"%PDF")
    adapter = DoclingAdapter("ignored", {})
    assert adapter.available() == (True, "")
    assert adapter.cache_identity()["variant"] == "ignored"
    raw = adapter.fetch(sample)
    assert raw.parser_version == "2.87.0"
    assert raw.cost_usd == 0

    class RaisesAdapter:
        def convert(self, file_path: str) -> None:
            del file_path
            raise AdapterError("already mapped")

    Module.DocumentConverter = RaisesAdapter  # type: ignore[misc]
    with pytest.raises(AdapterError, match="already mapped"):
        adapter.fetch(sample)

    class Broken:
        def convert(self, file_path: str) -> None:
            del file_path
            raise RuntimeError("boom")

    Module.DocumentConverter = Broken  # type: ignore[misc]
    with pytest.raises(AdapterError, match="boom"):
        adapter.fetch(sample)

    class Failed:
        name = "FAILURE"

    class FailedResult:
        status = Failed()
        errors = None
        document = Exported()

    class Failing:
        def convert(self, file_path: str) -> FailedResult:
            del file_path
            return FailedResult()

    Module.DocumentConverter = Failing  # type: ignore[misc]
    with pytest.raises(AdapterError, match="conversion failed"):
        adapter.fetch(sample)
    FailedResult.errors = ["ocr crashed"]  # type: ignore[misc]
    with pytest.raises(AdapterError, match="ocr crashed"):
        adapter.fetch(sample)

    class NoExport:
        status = Status()
        document = object()

    class Bare:
        def convert(self, file_path: str) -> NoExport:
            del file_path
            return NoExport()

    Module.DocumentConverter = Bare  # type: ignore[misc]
    with pytest.raises(AdapterError, match="export_to_dict"):
        adapter.fetch(sample)

    class BadExport:
        status = Status()

        class Document:
            def export_to_dict(self) -> str:
                return "nope"

        document = Document()

    class Weird:
        def convert(self, file_path: str) -> BadExport:
            del file_path
            return BadExport()

    Module.DocumentConverter = Weird  # type: ignore[misc]
    with pytest.raises(AdapterError, match="not a JSON object"):
        adapter.fetch(sample)

    Module.DocumentConverter = None  # type: ignore[misc]
    with pytest.raises(AdapterError, match="DocumentConverter is missing"):
        adapter.fetch(sample)

    Module.__version__ = ""  # type: ignore[misc]
    Module.DocumentConverter = Converter  # type: ignore[misc]
    assert adapter.fetch(sample).parser_version == "unknown"

    numbered = {
        "schema_name": "DoclingDocument",
        "pages": {
            2: {"size": {"width": 10, "height": 20}},
            "0": {"size": {"width": 10, "height": 20}},
        },
        "body": {"children": []},
    }
    numbered_doc = docling_to_document(numbered, SOURCE, "2.87.0")
    assert numbered_doc.pages[0].number == 2
    assert numbered_doc.pages[0].width == 10


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 to parse with Docling")
def test_live_parses_one_pdf(tmp_path: Path) -> None:
    pytest.importorskip("docling")
    sample = tmp_path / "note.pdf"
    sample.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    raw = DoclingAdapter(None, {}).fetch(sample)
    document = docling_to_document(raw.payload, SOURCE, raw.parser_version)
    assert document.meta.parser_version
    assert raw.latency_ms >= 0
