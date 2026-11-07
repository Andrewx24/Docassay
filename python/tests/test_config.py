"""Config loader and docassay init."""

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError as ModelError
from typer.testing import CliRunner

from docassay.cli import app
from docassay.config import (
    CONFIG_TEMPLATE,
    GOLDEN_TEMPLATE,
    ConfigError,
    canonical_config_json,
    init_command,
    load_config,
)

ROOT = Path(__file__).resolve().parents[2]
VALID = ROOT / "spec" / "fixtures" / "config" / "valid"
INVALID = ROOT / "spec" / "fixtures" / "config" / "invalid"


def test_valid_fixtures_match_canonical_json() -> None:
    expected = json.loads((VALID / "expected.json").read_text(encoding="utf-8"))
    assert isinstance(expected, dict)
    for name, payload in expected.items():
        text = canonical_config_json(load_config(VALID / str(name)))
        assert text == json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        assert text.endswith("\n")


def test_invalid_fixtures_report_every_issue() -> None:
    expected = json.loads((INVALID / "expected.json").read_text(encoding="utf-8"))
    assert isinstance(expected, dict)
    for name, wanted in expected.items():
        assert isinstance(wanted, list)
        with pytest.raises(ConfigError) as exc:
            load_config(INVALID / str(name))
        assert exc.value.exit_code == 2
        assert len(exc.value.issues) == len(wanted)
        rendered = str(exc.value)
        for issue, want in zip(exc.value.issues, wanted, strict=True):
            assert isinstance(want, dict)
            assert issue.line == want["line"]
            assert issue.column == want["column"]
            assert issue.path.endswith(str(name))
            message = want.get("message")
            prefix = want.get("messagePrefix")
            if isinstance(message, str):
                assert issue.message == message
            else:
                assert isinstance(prefix, str)
                assert issue.message.startswith(prefix)
            assert str(issue) in rendered


def test_missing_directory_and_utf8(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigError) as missing_exc:
        load_config(missing)
    assert missing_exc.value.issues[0].message == "path not found"

    with pytest.raises(ConfigError) as dir_exc:
        load_config(tmp_path)
    assert dir_exc.value.issues[0].message == "path is not a file"

    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"\xff")
    with pytest.raises(ConfigError) as utf_exc:
        load_config(bad)
    assert utf_exc.value.issues[0].message == "file is not valid UTF-8"


def test_unreadable_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "docassay.yaml"
    path.write_text("version: 1\ngolden: golden\nparsers:\n  - id: docling\n", encoding="utf-8")
    load_config(path)

    def boom(self: Path, encoding: str = "utf-8", errors: str | None = None) -> str:
        del self, encoding, errors
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert exc.value.issues[0].message == "Permission denied"


def test_model_rejection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "docassay.yaml"
    path.write_text("version: 1\ngolden: golden\nparsers:\n  - id: docling\n", encoding="utf-8")

    def boom(_data: object) -> object:
        raise ModelError.from_exception_data(
            "Config",
            [{"type": "missing", "loc": ("version",), "input": {}, "msg": "rejected by model"}],
        )

    monkeypatch.setattr("docassay.config.Config.model_validate", staticmethod(boom))
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert exc.value.issues[0].message == "Field required"


def test_absolute_paths_and_yaml_numbers(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    golden = tmp_path / "sets"
    cache = tmp_path / "cache"
    baseline = tmp_path / "base.json"
    parsed = tmp_path / "parsed"
    config = root / "docassay.yaml"
    config.write_text(
        "\n".join(
            [
                "version: 1",
                f"golden: {golden}",
                f"cache: {cache}",
                f"baseline: {baseline}",
                "parsers:",
                "  - id: file",
                "    options:",
                f"      dir: {parsed}",
                "  - id: docling",
                "    options:",
                "      inf: .inf",
                "      nan: .nan",
                "      ninf: -.inf",
                "      dir: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_config(config)
    assert loaded.golden == "../sets"
    assert loaded.cache == "../cache"
    assert loaded.baseline == "../base.json"
    assert loaded.parsers[0].options is not None
    assert loaded.parsers[0].options["dir"] == "../parsed"
    assert loaded.parsers[1].options is not None
    assert loaded.parsers[1].options["inf"] == math.inf
    assert math.isnan(loaded.parsers[1].options["nan"])
    assert loaded.parsers[1].options["ninf"] == -math.inf
    assert loaded.parsers[1].options["dir"] == 1

    dotted = tmp_path / "here.yaml"
    dotted.write_text(
        "version: 1\ngolden: .\nparsers:\n  - id: file\n    options: {dir: ''}\n",
        encoding="utf-8",
    )
    empty_dir = load_config(dotted)
    assert empty_dir.golden == "."
    assert empty_dir.parsers[0].options is not None
    assert empty_dir.parsers[0].options["dir"] == ""


def test_init_writes_templates_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert init_command() == 0
    assert (tmp_path / "docassay.yaml").read_text(encoding="utf-8") == CONFIG_TEMPLATE
    assert (tmp_path / "golden" / "example.yaml").read_text(encoding="utf-8") == GOLDEN_TEMPLATE
    loaded = load_config(tmp_path / "docassay.yaml")
    assert loaded.parsers[0].id == "file"
    assert loaded.parsers[0].name == "my-pipeline"
    assert init_command() == 2
    assert init_command(force=True) == 0


def test_init_refuses_when_only_one_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "example.yaml").write_text("keep\n", encoding="utf-8")
    assert init_command() == 2
    assert (
        "golden/example.yaml:1:1: file already exists; rerun with --force"
        in capsys.readouterr().out
    )
    assert not (tmp_path / "docassay.yaml").exists()
    assert (golden / "example.yaml").read_text(encoding="utf-8") == "keep\n"


def test_init_on_a_file_returns_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("x", encoding="utf-8")
    assert init_command(blocked) == 2
    assert ":1:1:" in capsys.readouterr().out


def test_cli_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    # Rich styles each hyphen when CI is set, so "--force" is not contiguous.
    help_result = runner.invoke(app, ["init", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert help_result.exit_code == 0
    assert "--force" in help_result.stdout
    created = runner.invoke(app, ["init"])
    assert created.exit_code == 0
    assert "wrote docassay.yaml" in created.stdout
    again = runner.invoke(app, ["init"])
    assert again.exit_code == 2
    forced = runner.invoke(app, ["init", "--force"])
    assert forced.exit_code == 0
