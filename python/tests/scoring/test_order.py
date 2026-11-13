"""Reading-order fixtures. Python and TypeScript share spec/fixtures/order-*."""

from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from docassay.models.document import Document
from docassay.models.golden import GoldenFile
from docassay.scoring.order import score_order

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures"
REQUIRED = {
    "order-repeat-first",
    "order-before",
    "order-first-missing",
    "order-second-missing",
    "order-partial",
    "order-once",
}


def _cases() -> list[Path]:
    return sorted(path for path in ROOT.iterdir() if path.name.startswith("order-"))


def _dec4(value: float) -> int:
    return math.floor(value * 10000 + 0.5)


def _document(text: str) -> Document:
    return Document.model_validate(
        {
            "specVersion": "1.0.0",
            "source": {
                "path": "files/note.pdf",
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "mime": "application/pdf",
            },
            "meta": {"parser": "file", "parserVersion": "file", "adapterVersion": "0.0.0"},
            "pages": [{"number": 1}],
            "blocks": [{"id": "b1", "type": "paragraph", "text": text, "page": 1}],
            "tables": [],
        }
    )


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
        assert golden.expect is not None and golden.expect.reading_order is not None
        pairs = [(item.root[0].root, item.root[1].root) for item in golden.expect.reading_order]
        result = score_order(document, pairs)
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        assert isinstance(expected, dict)
        assert _dec4(result.score) == _dec4(float(expected["scores"]["order"]))
        assert [failure.to_json() for failure in result.failures] == expected["failures"]
        if result.score < 1:
            assert result.failures


def test_empty_expectations_score_one() -> None:
    result = score_order(_document("alpha"), [])
    assert result.score == 1
    assert result.failures == []


def test_repeated_word_uses_the_offset_after_the_first_match() -> None:
    once = score_order(_document("alpha"), [("alpha", "alpha")])
    assert once.score == 0
    assert once.failures[0].reason == "out of order"
    twice = score_order(_document("alpha alpha"), [("alpha", "alpha")])
    assert twice.score == 1
    assert twice.failures == []
