"""Field scorer fixtures. Python and TypeScript share spec/fixtures/fields-*."""

from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from docassay.models.document import Document, FieldModel
from docassay.models.golden import GoldenFile
from docassay.scoring import ScoreOptions
from docassay.scoring.fields import score_fields

ROOT = Path(__file__).resolve().parents[3] / "spec" / "fixtures"
REQUIRED = {
    "fields-missing-doc",
    "fields-null",
    "fields-boolean-yes",
    "fields-number",
    "fields-date",
    "fields-missing-key",
}


def _cases() -> list[Path]:
    return sorted(path for path in ROOT.iterdir() if path.name.startswith("fields-"))


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


def _document(fields: object | None) -> Document:
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
        "tables": [],
    }
    if fields is not None:
        payload["fields"] = fields
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
        assert golden.expect is not None and golden.expect.fields is not None
        result = score_fields(document, golden.expect.fields, _options(folder))
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        assert isinstance(expected, dict)
        assert _dec4(result.score) == _dec4(float(expected["scores"]["fields"]))
        assert [failure.to_json() for failure in result.failures] == expected["failures"]
        if result.score < 1:
            assert result.failures


def test_empty_expectations_score_one() -> None:
    result = score_fields(_document({}), {})
    assert result.score == 1
    assert result.failures == []


def test_absent_fields_fail_a_null_expectation() -> None:
    result = score_fields(_document(None), {"note": None, "total": 10})
    assert result.score == 0
    assert [failure.reason for failure in result.failures] == [
        "parser returned no fields",
        "parser returned no fields",
    ]


def test_numeric_actual_matches_a_formatted_string() -> None:
    document = _document({"total": {"value": 4120}})
    result = score_fields(document, {"total": "4,120.00"})
    assert result.score == 1
    assert result.failures == []


def test_field_comparison_edges() -> None:
    document = _document(
        {
            "flag": {"value": 1},
            "label": {"value": None},
            "count": {"value": None},
            "total": {"value": 10},
            "vendor": {"value": "n/a"},
            "paid": {"value": False},
        }
    )
    result = score_fields(
        document,
        {"flag": True, "label": "Acme", "count": 5, "total": 10, "vendor": 3, "paid": 0},
    )
    assert [failure.reason for failure in result.failures] == ["value does not match"] * 5
    assert _dec4(result.score) == _dec4(1 / 6)


def test_non_scalar_actual_fails_a_number() -> None:
    document = _document({"total": {"value": 1}})
    assert document.fields is not None
    document.fields["total"] = FieldModel.model_construct(value=object())
    result = score_fields(document, {"total": 1})
    assert result.score == 0
    assert result.failures[0].reason == "value does not match"


def test_boolean_actual_matches_without_a_string() -> None:
    document = _document({"paid": {"value": True}, "closed": {"value": False}})
    result = score_fields(document, {"paid": True, "closed": False})
    assert result.score == 1
