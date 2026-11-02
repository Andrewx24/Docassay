"""Text scorer fixtures. Python and TypeScript share spec/fixtures/text-*."""

from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from docassay.models.document import Document
from docassay.models.golden import GoldenFile
from docassay.scoring import ScoreOptions
from docassay.scoring.text import document_text, score_text

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures"
REQUIRED = {
    "text-hyphen",
    "text-table-cell",
    "text-missing",
    "text-partial",
    "text-across-blocks",
    "text-markdown-ignored",
}
MISSING_TEXT = "Alpha\n\n\n\nBeta\n\nItem | Qty\nWidget | 2\n\nOnly"


def _cases() -> list[Path]:
    return sorted(path for path in ROOT.iterdir() if path.name.startswith("text-"))


def _dec4(value: float) -> int:
    return math.floor(value * 10000 + 0.5)


def _options(folder: Path) -> ScoreOptions:
    path = folder / "options.json"
    if not path.is_file():
        return ScoreOptions()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    decimal = raw.get("decimal", ".")
    order = raw.get("dateOrder", raw.get("date_order", "mdy"))
    assert decimal in (".", ",")
    assert order in ("mdy", "dmy")
    return ScoreOptions(decimal=decimal, date_order=order)


def _document(**overrides: object) -> Document:
    payload: dict[str, object] = {
        "specVersion": "1.0.0",
        "source": {
            "path": "files/invoice.pdf",
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "mime": "application/pdf",
        },
        "meta": {"parser": "file", "parserVersion": "file", "adapterVersion": "0.0.0"},
        "pages": [{"number": 1}],
        "blocks": [{"id": "b1", "type": "paragraph", "text": "Hello", "page": 1}],
        "tables": [],
    }
    payload.update(overrides)
    return Document.model_validate(payload)


def test_fixtures_cover_the_required_cases() -> None:
    names = {path.name for path in _cases()}
    assert REQUIRED <= names
    assert len(names) >= 6


def test_fixture_scores_match_expected_json() -> None:
    for folder in _cases():
        document = Document.model_validate_json(
            (folder / "document.json").read_text(encoding="utf-8")
        )
        loaded = yaml.safe_load((folder / "golden.yaml").read_text(encoding="utf-8"))
        golden = GoldenFile.model_validate(loaded)
        assert golden.expect is not None and golden.expect.text_contains is not None
        strings = [item.root for item in golden.expect.text_contains]
        result = score_text(document, strings, _options(folder))
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        assert isinstance(expected, dict)
        assert _dec4(result.score) == _dec4(float(expected["scores"]["text"]))
        assert [failure.to_json() for failure in result.failures] == expected["failures"]
        if result.score < 1:
            assert result.failures


def test_missing_fixture_locks_the_document_text_join() -> None:
    folder = ROOT / "text-missing"
    document = Document.model_validate_json((folder / "document.json").read_text(encoding="utf-8"))
    assert document_text(document) == MISSING_TEXT
    result = score_text(document, ["nowhere"])
    assert result.failures[0].actual == MISSING_TEXT
    assert result.failures[0].reason == "text not found"


def test_empty_expectations_score_one() -> None:
    result = score_text(_document(), [])
    assert result.score == 1
    assert result.failures == []


def test_document_text_keeps_an_empty_header_and_drops_markdown() -> None:
    document = _document(
        blocks=[
            {"id": "b1", "type": "paragraph", "text": "", "page": 1},
            {"id": "b2", "type": "paragraph", "text": "", "page": 1},
        ],
        tables=[
            {"id": "t1", "page": 1, "header": [], "rows": [["A"]]},
            {"id": "t2", "page": 1, "rows": [["B"]], "markdown": "SKIP"},
            {"id": "t3", "page": 1, "rows": []},
        ],
    )
    assert document_text(document) == "\n\n\n\n\nA\n\nB\n\n"


def test_tables_alone_have_no_leading_separator() -> None:
    document = _document(
        blocks=[],
        tables=[{"id": "t1", "page": 1, "header": ["Item"], "rows": [["Widget"]]}],
    )
    assert document_text(document) == "Item\nWidget"
