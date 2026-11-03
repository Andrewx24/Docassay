"""Baseline files and `docassay check`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docassay.cli import app
from docassay.config import load_config
from docassay.gate import GateError, baseline_command, check_command, compare, make_baseline
from docassay.models.config import Threshold, Thresholds

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "spec" / "fixtures" / "gate"
PINNED = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def _load(case: Path, name: str) -> dict[str, object]:
    loaded = json.loads((case / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_gate_fixtures_match_in_both_exit_code_and_text() -> None:
    for case in sorted(path for path in FIXTURES.iterdir() if path.is_dir()):
        scorecard = _load(case, "scorecard.json")
        baseline = _load(case, "baseline.json")
        expected = _load(case, "expected.json")
        result = compare(scorecard, baseline, load_config(case / "config.yaml").thresholds)
        assert result.exit_code == expected["exitCode"], case.name
        assert result.text == expected["text"], case.name

    over = FIXTURES / "doc-over"
    skipped = compare(
        _load(over, "scorecard.json"),
        _load(over, "baseline.json"),
        load_config(over / "config.yaml").thresholds,
        summary_only=True,
    )
    assert skipped.exit_code == 0
    assert skipped.text == (
        "0 regressions · 0 warnings · run `docassay baseline` after an intended change\n"
    )


def test_make_baseline_drops_failures_and_sorts_keys() -> None:
    text = make_baseline(_load(FIXTURES / "doc-over", "scorecard.json"), pinned_at=PINNED)
    assert "failures" not in text
    assert text.index('"adapter"') < text.index('"documents"')
    assert '"pinnedAt": "2026-11-03T12:00:00Z"' in text
    frozen = (FIXTURES / "no-change" / "baseline-out.json").read_text(encoding="utf-8")
    assert (
        make_baseline(_load(FIXTURES / "no-change", "scorecard.json"), pinned_at=PINNED) == frozen
    )


def test_floor_zero_drop_and_invalid_inputs(tmp_path: Path) -> None:
    scorecard = _load(FIXTURES / "no-change", "scorecard.json")
    baseline = _load(FIXTURES / "no-change", "baseline.json")
    parsers = scorecard["parsers"]
    assert isinstance(parsers, list)
    parser = parsers[0]
    assert isinstance(parser, dict)
    parser["summary"] = {"tables": 0.5, "text": 0.4}
    documents = parser["documents"]
    assert isinstance(documents, list)
    document = documents[0]
    assert isinstance(document, dict)
    document["scores"] = {"tables": 0.5}
    document["failures"] = [{"metric": "tables", "check": "tables[0]", "reason": "too low"}]
    thresholds = Thresholds(default=Threshold(drop=0.0, min=0.8))
    result = compare(scorecard, baseline, thresholds)
    assert result.exit_code == 1
    assert "below floor 0.8000" in result.text
    assert "allowed 0.0000" in result.text
    assert "tables[0]: too low" in result.text
    assert compare(scorecard, baseline, None).exit_code == 1
    with pytest.raises(GateError):
        compare({"specVersion": "1.0.0"}, baseline, None)
    broken = json.loads(json.dumps(scorecard))
    broken["createdAt"] = "yesterday"
    with pytest.raises(GateError):
        make_baseline(broken, pinned_at=PINNED)
    broken["createdAt"] = 5
    with pytest.raises(GateError):
        make_baseline(broken, pinned_at=PINNED)
    parser["costUsd"] = 1.5
    parser["latencyMsP50"] = 10
    parser["latencyMsP95"] = 20
    assert '"costUsd": 1.5' in make_baseline(scorecard, pinned_at=PINNED)
    config = tmp_path / "docassay.yaml"
    config.write_text("version: 1\ngolden: golden\nparsers:\n  - id: file\n", encoding="utf-8")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert check_command(config, baseline=tmp_path / "bad.json") == 2
    (tmp_path / "list.json").write_text("[]\n", encoding="utf-8")
    assert check_command(config, baseline=tmp_path / "list.json") == 2
    (tmp_path / "broken.yaml").write_text("[]\n", encoding="utf-8")
    assert baseline_command(tmp_path / "broken.yaml") == 2


def test_document_floor_keeps_its_detail_and_commands_report_io_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scorecard = _load(FIXTURES / "no-change", "scorecard.json")
    baseline = _load(FIXTURES / "no-change", "baseline.json")
    parsers = scorecard["parsers"]
    assert isinstance(parsers, list)
    parser = parsers[0]
    assert isinstance(parser, dict)
    documents = parser["documents"]
    assert isinstance(documents, list)
    document = documents[0]
    assert isinstance(document, dict)
    document["scores"] = {"tables": 0.5}
    document["failures"] = [
        {
            "metric": "tables",
            "check": "tables[0]",
            "expected": "1",
            "actual": "0",
            "reason": "too low",
        }
    ]
    reference = baseline["parsers"]
    assert isinstance(reference, list)
    earlier = reference[0]
    assert isinstance(earlier, dict)
    earlier_docs = earlier["documents"]
    assert isinstance(earlier_docs, list)
    earlier_doc = earlier_docs[0]
    assert isinstance(earlier_doc, dict)
    earlier_doc["scores"] = {"tables": 0.5}
    result = compare(scorecard, baseline, Thresholds(default=Threshold(min=0.8)))
    assert result.exit_code == 1
    assert result.text == (
        "FAIL  docling · invoice-acme · tables 0.5000 → 0.5000 (below floor 0.8000)\n"
        '      tables[0]: expected "1", got "0"\n'
        "\n"
        "1 regression · 0 warnings · run `docassay baseline` after an intended change\n"
    )
    with pytest.raises(GateError, match="baseline.schema.json"):
        compare(scorecard, {"specVersion": "1.0.0"}, None)
    with pytest.raises(GateError, match="expected a scorecard object"):
        make_baseline(object(), pinned_at=PINNED)  # type: ignore[arg-type]

    scorecard["createdAt"] = datetime(2026, 11, 1, tzinfo=timezone.utc)
    document["error"] = "parser timed out"
    written = make_baseline(scorecard, pinned_at=PINNED)
    assert '"createdAt": "2026-11-01T00:00:00Z"' in written
    assert '"error": "parser timed out"' in written

    config = tmp_path / "docassay.yaml"
    config.write_text(
        "version: 1\ngolden: golden\nbaseline: nested/base.json\nparsers:\n  - id: file\n",
        encoding="utf-8",
    )
    assert check_command(config) == 2
    assert (
        capsys.readouterr().out == f"{(tmp_path / 'nested' / 'base.json').as_posix()}:1:1: "
        "baseline not found. Run `docassay baseline` to create it.\n"
    )
    broken = tmp_path / "broken.yaml"
    broken.write_text("[]\n", encoding="utf-8")
    assert check_command(broken) == 2
    assert capsys.readouterr().out
    assert (
        baseline_command(ROOT / "golden" / "docassay.yaml", baseline=tmp_path, pinned_at=PINNED)
        == 2
    )
    assert capsys.readouterr().out.startswith("could not write the baseline:")


def test_check_reports_a_missing_baseline(tmp_path: Path) -> None:
    config = tmp_path / "docassay.yaml"
    config.write_text(
        "version: 1\ngolden: golden\nparsers:\n  - id: file\n",
        encoding="utf-8",
    )
    assert check_command(config, baseline=tmp_path / "missing.json") == 2


def test_cli_baseline_then_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    target = tmp_path / "baseline.json"
    assert (
        baseline_command(ROOT / "golden" / "docassay.yaml", baseline=target, pinned_at=PINNED) == 0
    )
    written = target.read_text(encoding="utf-8")
    assert '"pinnedAt": "2026-11-03T12:00:00Z"' in written
    assert "failures" not in written
    assert check_command(ROOT / "golden" / "docassay.yaml", baseline=target) == 0
    runner = CliRunner()
    checked = runner.invoke(
        app,
        ["check", "-c", "golden/docassay.yaml", "--baseline", target.as_posix(), "--summary-only"],
    )
    assert checked.exit_code == 0
    assert "0 regressions" in checked.stdout
