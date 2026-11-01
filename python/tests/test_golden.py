"""Golden-set loader and `docassay validate`."""

import math
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docassay.cli import app
from docassay.golden import (
    GoldenError,
    canonical_golden_json,
    load_golden,
    validate_command,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "spec" / "fixtures" / "golden"


def test_valid_set_matches_canonical_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(FIXTURES)
    files = load_golden("valid")
    assert [item.id for item in files] == ["invoice-acme", "partial"]
    assert [item.file for item in files] == ["files/doc.pdf", "files/other.pdf"]
    fields = files[0].expect
    assert fields is not None and fields.fields is not None
    assert fields.fields["due_date"] == "2026-11-01"
    assert fields.fields["answer"] == "yes"
    assert fields.fields["declined"] == "no"
    assert fields.fields["flag"] == "on"
    assert fields.fields["mode"] == 15
    assert fields.fields["legacy"] == 77
    assert fields.fields["note"] is None
    expected = (FIXTURES / "expected.json").read_text(encoding="utf-8")
    assert canonical_golden_json(files) == expected


def test_single_file_defaults_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(FIXTURES)
    files = load_golden("valid/partial.yml")
    assert files[0].id == "partial"
    assert files[0].file == "files/other.pdf"


def test_invalid_files_report_every_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(FIXTURES)
    expected = {
        "missing-file.yaml": ["invalid/missing-file.yaml:1:7: file not found: files/no-such.pdf"],
        "bad-col.yaml": ['invalid/bad-col.yaml:8:25: column "Missing" is not in the header'],
        "col-index.yaml": ["invalid/col-index.yaml:8:25: column index 3 is outside the header"],
        "no-header.yaml": ['invalid/no-header.yaml:7:25: column "Price" is not in the header'],
        "same-pair.yaml": [
            "invalid/same-pair.yaml:5:7: reading_order pair must contain two different strings"
        ],
        "schema.yaml": ['invalid/schema.yaml:3:1: unknown property "nope"'],
        "not-a-map.yaml": ["invalid/not-a-map.yaml:1:1: golden file must be a mapping"],
        "dir-file.yaml": ["invalid/dir-file.yaml:1:7: file is not readable: files"],
        "syntax.yaml": [
            "invalid/syntax.yaml:2:1: invalid YAML: expected the node content, but found '<stream end>'"
        ],
    }
    for name, messages in expected.items():
        with pytest.raises(GoldenError) as caught:
            load_golden(Path("invalid") / name)
        assert [str(item) for item in caught.value.issues] == messages

    with pytest.raises(GoldenError) as caught:
        load_golden("invalid/multi.yaml")
    assert [str(item) for item in caught.value.issues] == [
        "invalid/multi.yaml:1:7: file not found: files/missing.pdf",
        'invalid/multi.yaml:2:1: unknown property "extra"',
        'invalid/multi.yaml:9:25: column "Price" is not in the header',
    ]


def test_duplicate_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(FIXTURES)
    with pytest.raises(GoldenError) as caught:
        load_golden("dup")
    assert [str(item) for item in caught.value.issues] == [
        'dup/b.yaml:1:5: duplicate id "same" (also in dup/a.yaml)'
    ]


def test_validate_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(FIXTURES)
    assert validate_command("valid") == 0
    assert capsys.readouterr().out == ""
    assert validate_command("todo") == 0
    assert capsys.readouterr().out == ""
    assert validate_command("todo", strict=True) == 1
    assert capsys.readouterr().out == "todo/todo.yaml:2:1: unresolved TODO verify\n"
    assert validate_command("invalid", strict=True) == 2
    invalid_out = capsys.readouterr().out
    assert "unresolved TODO verify" not in invalid_out
    assert 'unknown property "extra"' in invalid_out
    assert validate_command("missing") == 2
    assert capsys.readouterr().out == "missing:1:1: path not found\n"


def test_cli_validate_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(FIXTURES)
    runner = CliRunner()
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert version.stdout.strip() == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    strict = runner.invoke(app, ["validate", "--strict", "todo"])
    assert strict.exit_code == 1
    assert "todo/todo.yaml:2:1: unresolved TODO verify" in strict.stdout
    plain = runner.invoke(app, ["validate", "todo"])
    assert plain.exit_code == 0
    assert plain.stdout == ""


def test_schema_messages_and_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    files = tmp_path / "files"
    files.mkdir()
    (files / "doc.pdf").write_bytes(b"")
    cases = {
        "missing.yaml": ("id: only\n", ['missing.yaml:1:1: missing property "file"']),
        "empty.yaml": ('file: ""\n', ["empty.yaml:1:7: must not be empty"]),
        "type.yaml": ("file: 1\n", ["type.yaml:1:7: must be string"]),
        "page.yaml": (
            "file: files/doc.pdf\nexpect:\n  tables:\n    - page: 0\n",
            ["page.yaml:4:13: must be >= 1"],
        ),
        "items.yaml": (
            "file: files/doc.pdf\nexpect:\n  reading_order:\n    - [A, B, C]\n",
            ["items.yaml:4:7: must have at most 2 items"],
        ),
        "short.yaml": (
            "file: files/doc.pdf\nexpect:\n  reading_order:\n    - [only]\n",
            ["short.yaml:4:7: must have at least 2 items"],
        ),
        "col.yaml": (
            "file: files/doc.pdf\nexpect:\n  tables:\n    - page: 1\n      cells:\n        - {row: 0, col: true, value: x}\n",
            ["col.yaml:6:25: must be a column index or a header name"],
        ),
        "dupkey.yaml": (
            "file: files/doc.pdf\nid: one\nid: two\n",
            ['dupkey.yaml:3:1: duplicate key "id"'],
        ),
        "field.yaml": (
            "file: files/doc.pdf\nexpect:\n  fields:\n    total: {bad: 1}\n",
            ["field.yaml:4:12: must be string, number, boolean, or null"],
        ),
    }
    for name, (text, messages) in cases.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        with pytest.raises(GoldenError) as caught:
            load_golden(name)
        assert [str(item) for item in caught.value.issues] == messages
    (tmp_path / "bad.yaml").write_bytes(b"file: \xff\n")
    with pytest.raises(GoldenError) as caught:
        load_golden("bad.yaml")
    assert [str(item) for item in caught.value.issues] == ["bad.yaml:1:1: file is not valid UTF-8"]
    (tmp_path / "note.txt").write_text("hi\n", encoding="utf-8")
    with pytest.raises(GoldenError) as caught:
        load_golden("note.txt")
    assert str(caught.value.issues[0]) == "note.txt:1:1: expected a .yaml or .yml file"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_golden(empty) == []


def test_empty_directory_is_success(tmp_path: Path) -> None:
    assert validate_command(tmp_path) == 0
    assert load_golden(tmp_path) == []


def test_core_scalars_blank_files_and_crlf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "doc.pdf").write_bytes(b"")
    (tmp_path / "scalars.yaml").write_text(
        "file: files/doc.pdf\nexpect:\n  fields:\n"
        "    signed: -3\n    plus: +4\n    hex: 0x10\n    nan: .nan\n    ninf: -.inf\n    pinf: +.inf\n",
        encoding="utf-8",
    )
    fields = load_golden("scalars.yaml")[0].expect
    assert fields is not None and fields.fields is not None
    assert fields.fields["signed"] == -3
    assert fields.fields["plus"] == 4
    assert fields.fields["hex"] == 16
    assert math.isnan(fields.fields["nan"])
    assert fields.fields["ninf"] == -math.inf
    assert fields.fields["pinf"] == math.inf
    (tmp_path / "blank.yaml").write_text("", encoding="utf-8")
    with pytest.raises(GoldenError) as caught:
        load_golden("blank.yaml")
    assert str(caught.value.issues[0]) == "blank.yaml:1:1: golden file must be a mapping"
    (tmp_path / "crlf.yaml").write_bytes(b"file: files/doc.pdf\r\n# TODO verify\r\n")
    assert validate_command("crlf.yaml", strict=True) == 1


def test_unreadable_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "doc.pdf").write_bytes(b"x")
    (tmp_path / "locked.yaml").write_text("file: files/doc.pdf\n", encoding="utf-8")
    real_open = Path.open

    def deny_pdf(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "doc.pdf":
            raise OSError("denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_pdf)
    with pytest.raises(GoldenError) as caught:
        load_golden("locked.yaml")
    assert "file is not readable: files/doc.pdf" in str(caught.value.issues[0])
    monkeypatch.setattr(Path, "open", real_open)

    blocked = tmp_path / "blocked"
    blocked.mkdir()

    def deny_dir(self: Path) -> object:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "iterdir", deny_dir)
    with pytest.raises(GoldenError) as caught:
        load_golden(blocked)
    assert "Permission denied" in str(caught.value.issues[0])
