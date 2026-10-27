"""Fixture tests for the normalization library."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docassay.normalize import norm_text, parse_date, parse_number, round4, values_equal

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "spec" / "fixtures" / "normalize"
SOURCE = ROOT / "python" / "src" / "docassay" / "normalize"


def _load(name: str) -> list[dict[str, Any]]:
    data = json.loads((FIXTURES / name).read_text())
    assert isinstance(data, list)
    return data


def _close(actual: float, expected: float) -> None:
    assert abs(actual - expected) <= 1e-9


def test_at_least_sixty_fixture_cases() -> None:
    """The shared corpus covers the cases named in ALI-339."""
    names = ("text.json", "numbers.json", "dates.json", "values_equal.json", "round4.json")
    assert sum(len(_load(name)) for name in names) >= 60


@pytest.mark.parametrize("case", _load("text.json"), ids=lambda case: str(case["name"]))
def test_norm_text(case: dict[str, Any]) -> None:
    assert norm_text(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", _load("numbers.json"), ids=lambda case: str(case["name"]))
def test_parse_number(case: dict[str, Any]) -> None:
    parsed = parse_number(case["input"], case["decimal"])
    if case["expected"] is None:
        assert parsed is None
    else:
        assert parsed is not None
        _close(parsed, float(case["expected"]))


@pytest.mark.parametrize("case", _load("dates.json"), ids=lambda case: str(case["name"]))
def test_parse_date(case: dict[str, Any]) -> None:
    assert parse_date(case["input"], case["order"]) == case["expected"]


@pytest.mark.parametrize("case", _load("values_equal.json"), ids=lambda case: str(case["name"]))
def test_values_equal(case: dict[str, Any]) -> None:
    opts = {"decimal": case["decimal"], "date_order": case["date_order"]}
    assert values_equal(case["expected"], case["actual"], opts) is case["equal"]


@pytest.mark.parametrize("case", _load("round4.json"), ids=lambda case: str(case["name"]))
def test_round4(case: dict[str, Any]) -> None:
    _close(round4(float(case["input"])), float(case["expected"]))


def test_normalize_sources_avoid_ambiguous_regex() -> None:
    """Scoring code must not use classes whose meaning differs by language."""
    needles = [chr(92) + name for name in ("s", "w", "b", "d")]
    needles.append("isspace")
    for path in SOURCE.glob("*.py"):
        text = path.read_text()
        for needle in needles:
            assert needle not in text, f"{path.name} contains {needle}"
