"""Table scorer fixtures. Python and TypeScript share spec/fixtures/tables-*."""

from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from docassay.models.document import Document
from docassay.models.golden import CellExpect, GoldenFile, TableExpect
from docassay.scoring import ScoreOptions
from docassay.scoring.tables import score_tables

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures"
REQUIRED = {
    "tables-exact",
    "tables-header-in-rows",
    "tables-wrong-page",
    "tables-greedy",
    "tables-header-below",
    "tables-ragged",
    "tables-cell-name",
    "tables-cell-index",
    "tables-cell-out-of-range",
    "tables-numeric",
    "tables-no-header",
}


def _cases() -> list[Path]:
    return sorted(path for path in ROOT.iterdir() if path.name.startswith("tables-"))


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


def _document() -> Document:
    return Document.model_validate(
        {
            "specVersion": "1.0.0",
            "source": {
                "path": "files/note.pdf",
                "sha256": "ab" * 32,
                "mime": "application/pdf",
            },
            "meta": {"parser": "file", "parserVersion": "file", "adapterVersion": "0.0.0"},
            "pages": [{"number": 1}],
            "blocks": [],
            "tables": [
                {"id": "t1", "page": 1, "header": ["A"], "rows": [["x"]]},
                {"id": "t2", "page": 1, "header": ["B"], "rows": [["y"]]},
            ],
        }
    )


def test_fixtures_cover_the_required_cases() -> None:
    names = {path.name for path in _cases()}
    assert REQUIRED <= names
    assert len(names) >= 10


def test_fixture_scores_match_expected_json() -> None:
    for folder in _cases():
        document = Document.model_validate_json(
            (folder / "document.json").read_text(encoding="utf-8")
        )
        loaded = yaml.safe_load((folder / "golden.yaml").read_text(encoding="utf-8"))
        golden = GoldenFile.model_validate(loaded)
        assert golden.expect is not None and golden.expect.tables is not None
        result = score_tables(document, golden.expect.tables, _options(folder))
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        assert isinstance(expected, dict)
        assert _dec4(result.score) == _dec4(float(expected["scores"]["tables"]))
        assert [failure.to_json() for failure in result.failures] == expected["failures"]
        if result.score < 1:
            assert result.failures


def test_empty_expectations_score_one() -> None:
    result = score_tables(_document(), [])
    assert result.score == 1
    assert result.failures == []


def test_omitted_page_matches_any_unused_table() -> None:
    item = TableExpect.model_construct(
        page=None,
        header=["B"],
        rows=None,
        cols=None,
        cells=[CellExpect.model_validate({"row": 0, "col": 0, "value": "y"})],
    )
    result = score_tables(_document(), [item])
    assert result.score == 1
    assert result.failures == []

    missing = TableExpect.model_construct(page=None, header=["Z"], rows=None, cols=None, cells=None)
    empty = _document()
    empty.tables = []
    result = score_tables(empty, [missing])
    assert result.score == 0
    assert result.failures[0].reason == "no table found"
    assert "page" not in result.failures[0].to_json()
