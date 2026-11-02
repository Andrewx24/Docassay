"""Cross-language fixture scoring. The shell script diffs this against TypeScript."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from docassay._parity import (
    ParityError,
    diff_json,
    main,
    render,
    resolve_case,
    score_case,
)
from docassay.normalize import round4

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "spec" / "fixtures"
METRICS = ("tables", "text", "order", "fields", "chunks")


def _cases() -> list[Path]:
    return sorted(path for path in FIXTURES.iterdir() if (path / "document.json").is_file())


def _same4(left: float, right: float) -> bool:
    return math.floor(left * 10000 + 0.5) == math.floor(right * 10000 + 0.5)


def _fold(value: object) -> object:
    payload = json.loads(render({"value": value}))
    return payload["value"]


def test_every_fixture_matches_expected_to_4_decimals() -> None:
    folders = _cases()
    assert len(folders) >= 20
    for folder in folders:
        payload = score_case(folder)
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        assert isinstance(expected, dict)
        scores = payload["scores"]
        assert isinstance(scores, dict)
        expected_scores = expected["scores"]
        assert isinstance(expected_scores, dict)
        for name, value in expected_scores.items():
            assert isinstance(value, int | float)
            got = scores[name]
            assert isinstance(got, int | float)
            assert _same4(float(got), float(value)), folder.name
        present = [float(expected_scores[name]) for name in METRICS if name in expected_scores]
        overall = scores["overall"]
        assert isinstance(overall, int | float)
        assert _same4(float(overall), round4(sum(present) / len(present))), folder.name
        failures = payload["failures"]
        assert isinstance(failures, list)
        assert _fold(failures) == _fold(expected["failures"])
        for failure in expected["failures"]:
            assert isinstance(failure, dict)
            assert {"metric", "check", "reason"} <= set(failure)
        if "extra" in expected:
            assert _fold(payload["extra"]) == _fold(expected["extra"])
        else:
            assert "extra" not in payload


def test_main_prints_a_case(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tables-exact"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["scores"]["tables"] == 1
    assert body["failures"] == []


def test_main_reports_usage_and_unknown_cases(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert "usage:" in capsys.readouterr().err
    assert main(["does-not-exist"]) == 1
    assert "unknown fixture" in capsys.readouterr().err
    with pytest.raises(ParityError, match="unknown fixture"):
        resolve_case("does-not-exist")


def test_resolve_case_accepts_a_directory() -> None:
    folder = FIXTURES / "text-exact"
    assert resolve_case(str(folder)) == folder.resolve()


def test_diff_prints_case_path_and_both_values(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    py_path = tmp_path / "py.json"
    js_path = tmp_path / "js.json"
    py_path.write_text(json.dumps({"scores": {"tables": 1}, "failures": []}), encoding="utf-8")
    js_path.write_text(
        json.dumps({"scores": {"tables": 0}, "failures": [{"metric": "x"}]}), encoding="utf-8"
    )
    assert main(["--diff", "tables-exact", str(py_path), str(js_path)]) == 1
    text = capsys.readouterr().out
    assert "case: tables-exact" in text
    assert "path: scores.tables" in text
    assert "py: 1" in text
    assert "js: 0" in text
    assert "path: failures[0]" in text
    assert "<missing>" in text


def test_numbers_within_4_decimals_match_and_the_next_digit_does_not() -> None:
    assert diff_json("n", {"score": 1}, {"score": 1.00004}) == []
    mismatch = diff_json("n", {"score": 1}, {"score": 1.00005})
    assert mismatch and "path: score" in mismatch[0]
    assert diff_json("n", {"flag": True}, {"flag": 1})
    assert diff_json("clock", {"runId": "a", "ok": 1}, {"runId": "b", "ok": 1}, ("runId",)) == []


def test_diff_scorecard_ignores_run_id_and_created_at(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    py_path = tmp_path / "py.json"
    js_path = tmp_path / "js.json"
    py_path.write_text(
        json.dumps({"runId": "run_a", "createdAt": "2020-01-01T00:00:00Z", "parsers": []}),
        encoding="utf-8",
    )
    js_path.write_text(
        json.dumps({"runId": "run_b", "createdAt": "2026-11-02T00:00:00Z", "parsers": []}),
        encoding="utf-8",
    )
    assert main(["--diff-scorecard", str(py_path), str(js_path)]) == 0
    assert capsys.readouterr().out == ""


def test_diff_commands_reject_bad_arguments(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["--diff", "only-one"]) == 1
    assert "usage:" in capsys.readouterr().err
    assert main(["--diff-scorecard"]) == 1
    assert "usage:" in capsys.readouterr().err
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert main(["--diff", "case", str(broken), str(broken)]) == 1
    assert "invalid JSON" in capsys.readouterr().err
    assert main(["--diff", "case", str(tmp_path), str(tmp_path)]) == 1
    assert "could not read JSON" in capsys.readouterr().err


def test_fixture_errors_name_the_file(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "document.json").write_text("{", encoding="utf-8")
    (case / "golden.yaml").write_text("file: files/a.pdf\n", encoding="utf-8")
    with pytest.raises(ParityError, match="document.json"):
        score_case(case)
    (case / "document.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ParityError, match="document.json"):
        score_case(case)
    (case / "document.json").unlink()
    (case / "document.json").mkdir()
    with pytest.raises(ParityError, match="could not read the document"):
        score_case(case)
    (case / "document.json").rmdir()
    document = {
        "specVersion": "1.0.0",
        "source": {
            "path": "files/a.pdf",
            "sha256": "ab" * 32,
            "mime": "application/pdf",
        },
        "meta": {"parser": "file", "parserVersion": "file", "adapterVersion": "0.0.0"},
        "pages": [{"number": 1}],
        "blocks": [],
        "tables": [],
    }
    (case / "document.json").write_text(json.dumps(document), encoding="utf-8")
    (case / "golden.yaml").write_text("[\n", encoding="utf-8")
    with pytest.raises(ParityError, match="invalid YAML"):
        score_case(case)
    (case / "golden.yaml").write_text("- just-a-list\n", encoding="utf-8")
    with pytest.raises(ParityError, match="must be a mapping"):
        score_case(case)
    (case / "golden.yaml").write_text("id: only\n", encoding="utf-8")
    with pytest.raises(ParityError, match="golden.yaml"):
        score_case(case)
    (case / "golden.yaml").write_text("file: files/a.pdf\nexpect: {}\n", encoding="utf-8")
    assert score_case(case) == {"scores": {}, "failures": []}
    (case / "options.json").write_text("{", encoding="utf-8")
    with pytest.raises(ParityError, match="options.json"):
        score_case(case)
    (case / "options.json").unlink()
    (case / "options.json").mkdir()
    with pytest.raises(ParityError, match="could not read options"):
        score_case(case)
    (case / "options.json").rmdir()
    (case / "options.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ParityError, match="JSON object"):
        score_case(case)
    (case / "options.json").write_text(json.dumps({"decimal": "x"}), encoding="utf-8")
    with pytest.raises(ParityError, match="decimal"):
        score_case(case)
    (case / "options.json").write_text(json.dumps({"dateOrder": "ymd"}), encoding="utf-8")
    with pytest.raises(ParityError, match="dateOrder"):
        score_case(case)
    (case / "options.json").write_text(json.dumps({"tokenizer": ""}), encoding="utf-8")
    with pytest.raises(ParityError, match="tokenizer"):
        score_case(case)
    (case / "options.json").write_text(
        json.dumps({"decimal": ",", "date_order": "dmy", "tokenizer": "words"}),
        encoding="utf-8",
    )
    scored = score_case(case)
    assert scored["scores"] == {}


def test_show_falls_back_when_a_value_is_not_json() -> None:
    rows = diff_json("odd", {"value": {1}}, {"value": {2}})
    assert rows and "py:" in rows[0] and "js:" in rows[0]
