"""Chunk scorer fixtures. Python and TypeScript share spec/fixtures/chunks-*."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import yaml

from docassay.models.document import Document
from docassay.models.golden import ChunkExpect, GoldenFile, HeadingPath
from docassay.scoring.chunks import score_chunks

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures"
REQUIRED = {
    "chunks-absent",
    "chunks-split",
    "chunks-split-repeated",
    "chunks-min-last",
    "chunks-heading-mismatch",
    "chunks-tokens-set",
}


def _cases() -> list[Path]:
    return sorted(path for path in ROOT.iterdir() if path.name.startswith("chunks-"))


def _dec4(value: float) -> int:
    return math.floor(value * 10000 + 0.5)


def _document(chunks: object | None, tables: object | None = None) -> Document:
    payload: dict[str, object] = {
        "specVersion": "1.0.0",
        "source": {
            "path": "files/invoice.pdf",
            "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "mime": "application/pdf",
        },
        "meta": {"parser": "file", "parserVersion": "file", "adapterVersion": "0.0.0"},
        "pages": [{"number": 1}],
        "blocks": [],
        "tables": [] if tables is None else tables,
    }
    if chunks is not None:
        payload["chunks"] = chunks
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
        assert golden.expect is not None and golden.expect.chunks is not None
        result = score_chunks(document, golden.expect.chunks)
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        assert isinstance(expected, dict)
        assert _dec4(result.score) == _dec4(float(expected["scores"]["chunks"]))
        assert [failure.to_json() for failure in result.failures] == expected["failures"]
        assert result.extra == expected.get("extra")
        if result.score < 1:
            assert result.failures


def test_rules_that_are_off_add_no_checks() -> None:
    document = _document(
        [
            {"id": "c1", "text": "body", "blockIds": ["t1"], "pages": [1], "tokens": 1},
            {"id": "c2", "text": "more", "blockIds": ["t1"], "pages": [1], "tokens": 1},
        ],
        [{"id": "t1", "page": 1, "header": ["Item"], "rows": [["A"]]}],
    )
    result = score_chunks(document, ChunkExpect(no_split_tables=False, max_tokens=5))
    assert result.score == 1
    assert result.failures == []
    assert result.extra == {"tokens": {"min": 1, "median": 1.0, "max": 1}}


def test_an_empty_table_has_no_header_cells_to_repeat() -> None:
    document = _document(
        [
            {"id": "c1", "text": "a", "blockIds": ["t1"], "pages": [1]},
            {"id": "c2", "text": "b", "blockIds": ["t1"], "pages": [1]},
        ],
        [{"id": "t1", "page": 1, "rows": []}],
    )
    result = score_chunks(document, ChunkExpect(no_split_tables=True))
    assert result.score == 1
    assert result.failures == []


def test_constructed_heading_strings_match_after_normalization() -> None:
    document = _document(
        [
            {
                "id": "c1",
                "text": "Late fees",
                "blockIds": [],
                "pages": [1],
                "headingPath": ["Terms"],
            }
        ]
    )
    item = HeadingPath.model_construct(text="Late fees", path=["terms"])
    result = score_chunks(document, ChunkExpect.model_construct(heading_paths=[item]))
    assert result.score == 1

    bad = HeadingPath.model_construct(text="Late fees", path=[1])
    with pytest.raises(TypeError, match="heading path"):
        score_chunks(document, ChunkExpect.model_construct(heading_paths=[bad]))


def test_no_rules_still_reports_the_token_distribution() -> None:
    document = _document([{"id": "c1", "text": "one two", "blockIds": [], "pages": [1]}])
    result = score_chunks(document, ChunkExpect())
    assert result.score == 1
    assert result.failures == []
    assert result.extra == {"tokens": {"min": 2, "median": 2, "max": 2}}
