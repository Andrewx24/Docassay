"""File adapter: pre-parsed Document JSON and a small Markdown subset."""

import hashlib
import json
from pathlib import Path

import pytest

from docassay import __version__
from docassay.adapters.base import AdapterError, RawResult
from docassay.adapters.file import FileAdapter, markdown_to_document
from docassay.adapters.runner_support import run_parser
from docassay.models.document import Document, Source

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures" / "file-adapter"
SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SOURCE = Source(path="files/invoice.pdf", sha256=SHA, mime="application/pdf")
DIR_REASON = "file adapter requires options.dir, a folder of <golden id>.json or .md files"


def _canonical(document: Document) -> str:
    payload = document.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _stale_document() -> dict[str, object]:
    return {
        "specVersion": "1.0.0",
        "source": {"path": "old/path.pdf", "sha256": "ab" * 32, "mime": "application/pdf"},
        "meta": {
            "parser": "llamaparse:agentic",
            "parserVersion": "2026-09-24",
            "adapterVersion": "9.9.9",
            "latencyMs": 840,
            "costUsd": 0.012,
            "createdAt": "2026-10-26T12:00:00Z",
            "extra": {"jobId": "job_123"},
        },
        "pages": [{"number": 1}],
        "blocks": [{"id": "b1", "type": "paragraph", "text": "Kept", "page": 1}],
        "tables": [],
        "warnings": ["check total"],
        "fields": {"vendor": {"value": "Acme"}},
    }


def test_markdown_fixtures_match_expected_documents() -> None:
    cases = sorted(path for path in ROOT.iterdir() if path.is_dir())
    assert [path.name for path in cases] == ["edges", "empty", "mixed"]
    for case_dir in cases:
        text = (case_dir / "input.md").read_text(encoding="utf-8")
        document = markdown_to_document(text, SOURCE)
        expected = (case_dir / "expected-document.json").read_text(encoding="utf-8")
        assert _canonical(document) == expected
        crlf = markdown_to_document(text.replace("\n", "\r\n"), SOURCE)
        assert _canonical(crlf) == expected


def test_missing_dir_is_unavailable() -> None:
    adapter = FileAdapter(None, {})
    assert adapter.cacheable is False
    assert adapter.cache_identity() == {"dir": ""}
    assert adapter.available() == (False, DIR_REASON)
    numbered = FileAdapter(None, {"dir": 3})
    assert numbered.available() == (False, DIR_REASON)
    with pytest.raises(AdapterError) as exc:
        adapter.fetch(Path("files/invoice.pdf"))
    assert str(exc.value) == DIR_REASON


def test_cache_identity_is_the_directory() -> None:
    adapter = FileAdapter("ignored", {"dir": "out/parsed"})
    assert adapter.id == "file"
    assert adapter.variants == ()
    assert adapter.available() == (True, "")
    assert adapter.cache_identity() == {"dir": "out/parsed"}
    assert adapter.cacheable is False


def test_json_is_preferred_and_runner_overwrites_meta(tmp_path: Path) -> None:
    folder = tmp_path / "parsed"
    folder.mkdir()
    (folder / "invoice.json").write_text(json.dumps(_stale_document()), encoding="utf-8")
    (folder / "invoice.md").write_text("# Ignored\n", encoding="utf-8")
    sample = tmp_path / "invoice.pdf"
    sample.write_bytes(b"%PDF-1.4\n")
    adapter = FileAdapter(None, {"dir": folder.as_posix()})
    document = run_parser(adapter, sample, "file", stored_path="files/invoice.pdf")
    assert document.source.path == "files/invoice.pdf"
    assert document.source.sha256 == hashlib.sha256(b"%PDF-1.4\n").hexdigest()
    assert document.source.mime == "application/pdf"
    assert document.meta.parser == "file"
    assert document.meta.parser_version == "file"
    assert document.meta.adapter_version == __version__
    assert document.meta.latency_ms == 0
    assert document.meta.cost_usd == 0
    assert document.meta.extra == {"jobId": "job_123"}
    assert document.meta.created_at is not None
    assert document.meta.created_at.isoformat().startswith("2026-10-26T12:00:00")
    assert document.blocks[0].text == "Kept"
    assert document.warnings == ["check total"]
    assert document.fields is not None
    assert document.fields["vendor"].value == "Acme"


def test_markdown_fallback_and_missing_output(tmp_path: Path) -> None:
    folder = tmp_path / "parsed"
    folder.mkdir()
    text = (ROOT / "mixed" / "input.md").read_text(encoding="utf-8")
    (folder / "invoice.final.md").write_text(text, encoding="utf-8")
    adapter = FileAdapter(None, {"dir": folder.as_posix()})
    raw = adapter.fetch(tmp_path / "invoice.final.pdf")
    assert raw.parser_version == "file"
    assert raw.latency_ms == 0
    assert raw.cost_usd == 0
    document = adapter.to_document(raw, SOURCE)
    expected = (ROOT / "mixed" / "expected-document.json").read_text(encoding="utf-8")
    assert _canonical(document) == expected
    with pytest.raises(AdapterError) as exc:
        adapter.fetch(tmp_path / "gone.pdf")
    assert str(exc.value) == f"no output for gone in {folder.as_posix()}"


def test_invalid_json_does_not_fall_through_to_markdown(tmp_path: Path) -> None:
    folder = tmp_path / "parsed"
    folder.mkdir()
    (folder / "bad.json").write_text("{", encoding="utf-8")
    (folder / "bad.md").write_text("# Hello\n", encoding="utf-8")
    (folder / "list.json").write_text("[]", encoding="utf-8")
    (folder / "partial.json").write_text('{"specVersion": "1.0.0"}', encoding="utf-8")
    (folder / "binary.json").write_bytes(b"\xff")
    adapter = FileAdapter(None, {"dir": folder.as_posix()})
    with pytest.raises(AdapterError, match="is not valid JSON"):
        adapter.fetch(tmp_path / "bad.pdf")
    with pytest.raises(AdapterError, match="must be object"):
        adapter.fetch(tmp_path / "list.pdf")
    with pytest.raises(AdapterError, match="does not match document.schema.json"):
        adapter.fetch(tmp_path / "partial.pdf")
    with pytest.raises(AdapterError, match="not valid UTF-8"):
        adapter.fetch(tmp_path / "binary.pdf")


def test_payload_must_be_a_document_or_markdown() -> None:
    adapter = FileAdapter(None, {"dir": "out"})
    raw = RawResult(payload={"markdown": 1}, parser_version="file", latency_ms=0)
    with pytest.raises(AdapterError, match="must be a Document or markdown text"):
        adapter.to_document(raw, SOURCE)
    stale = _stale_document()
    assert isinstance(stale["meta"], dict)
    stale["meta"]["createdAt"] = "not-a-date"
    dated = RawResult(payload=stale, parser_version="file", latency_ms=0, cost_usd=0)
    with pytest.raises(AdapterError, match="does not match document.schema.json"):
        adapter.to_document(dated, SOURCE)
