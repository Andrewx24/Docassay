"""The packaged 20-document golden set and --golden builtin."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from docassay.builtin import BuiltinError, builtin_config_path
from docassay.cli import app
from docassay.golden import validate_command

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "golden"


def test_unknown_golden_value_exits_2() -> None:
    result = CliRunner().invoke(app, ["run", "--golden", "nope"])
    assert result.exit_code == 2
    assert 'unknown --golden value "nope"' in result.stdout


def test_missing_set_explains_how_to_fix(tmp_path: Path) -> None:
    origin = tmp_path / "empty" / "builtin.py"
    origin.parent.mkdir()
    try:
        builtin_config_path(origin)
    except BuiltinError as err:
        assert "golden/docassay.yaml" in str(err)
    else:
        raise AssertionError("expected BuiltinError")


def test_checkout_fallback_finds_golden_docassay_yaml(tmp_path: Path) -> None:
    origin = tmp_path / "pkg" / "builtin.py"
    origin.parent.mkdir()
    config = tmp_path / "golden" / "docassay.yaml"
    config.parent.mkdir()
    config.write_text("version: 1\n", encoding="utf-8")
    assert builtin_config_path(origin) == config.resolve()


def test_packaged_copy_wins_over_a_checkout(tmp_path: Path) -> None:
    origin = tmp_path / "docassay" / "builtin.py"
    origin.parent.mkdir()
    packaged = origin.parent / "builtin_golden" / "docassay.yaml"
    packaged.parent.mkdir()
    packaged.write_text("version: 1\n", encoding="utf-8")
    checkout = tmp_path / "golden" / "docassay.yaml"
    checkout.parent.mkdir()
    checkout.write_text("version: 1\n", encoding="utf-8")
    assert builtin_config_path(origin) == packaged.resolve()


def test_generated_set_validates_and_builtin_matches_results() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "golden" / "generate" / "build.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert validate_command(GOLDEN / "sets", strict=True) == 0
    yaml_files = list((GOLDEN / "sets").glob("*.yaml"))
    assert len(yaml_files) == 20
    suffixes = {path.suffix for path in (GOLDEN / "files").iterdir() if path.is_file()}
    assert {".pdf", ".docx", ".xlsx"} <= suffixes
    sources = (GOLDEN / "SOURCES.md").read_text(encoding="utf-8")
    assert sources.count("| ") >= 22
    result = CliRunner().invoke(app, ["run", "--golden", "builtin"])
    validated = CliRunner().invoke(app, ["validate", "--golden", "builtin", "--strict"])
    assert validated.exit_code == 0, validated.stdout
    assert result.exit_code == 0, result.stdout
    assert "20 documents" in result.stdout
    assert result.stdout in (GOLDEN / "RESULTS.md").read_text(encoding="utf-8")
    packaged = ROOT / "python" / "src" / "docassay" / "builtin_golden" / "sets"
    assert len(list(packaged.glob("*.yaml"))) == 20
