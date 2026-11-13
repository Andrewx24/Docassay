"""Draft golden files and the local review page."""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml
from typer.testing import CliRunner

from docassay.cli import app
from docassay.golden import CoreLoader, validate_command
from docassay.label import (
    LabelError,
    draft_yaml,
    label_command,
    render_yaml,
    review_html,
    start_review,
)
from docassay.models.document import Document

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "spec" / "fixtures" / "label"
SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
CONFIG = """\
version: 1
golden: golden
parsers:
  - id: file
    options: {dir: parsed}
"""


def _load_fixture() -> Document:
    return Document.model_validate(json.loads((FIXTURE / "input.json").read_text(encoding="utf-8")))


def _document(**overrides: Any) -> Document:
    payload: dict[str, Any] = {
        "specVersion": "1.0.0",
        "source": {"path": "files/lease.pdf", "sha256": SHA, "mime": "application/pdf"},
        "meta": {"parser": "file", "parserVersion": "file", "adapterVersion": "0.0.0"},
        "pages": [{"number": 1}],
        "blocks": [],
        "tables": [],
    }
    payload.update(overrides)
    return Document.model_validate(payload)


def _block(block_id: str, text: str, page: int = 1, kind: str = "paragraph") -> dict[str, Any]:
    return {"id": block_id, "type": kind, "text": text, "page": page}


def _prepare(tmp_path: Path) -> None:
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "lease.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "parsed").mkdir()
    (tmp_path / "parsed" / "lease.json").write_text(
        (FIXTURE / "input.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "docassay.yaml").write_text(CONFIG, encoding="utf-8")


def test_fixture_matches_expected_and_is_stable() -> None:
    document = _load_fixture()
    drafted = draft_yaml(document, file_ref="../files/lease.pdf", doc_id="lease")
    assert drafted == (FIXTURE / "expected.yaml").read_text(encoding="utf-8")
    assert drafted == draft_yaml(document, file_ref="../files/lease.pdf", doc_id="lease")


def test_snippet_table_and_order_rules() -> None:
    long_word = "z" * 90
    spaced = "short " + ("y" * 90)
    cut = _document(
        blocks=[
            _block("b1", spaced, kind="footnote"),
            _block("b2", long_word),
        ]
    )
    cut_yaml = draft_yaml(cut, file_ref="a.pdf", doc_id="a")
    assert f'"{long_word[:80]}"' in cut_yaml
    assert "short" in cut_yaml
    assert "y" * 10 not in cut_yaml

    tables = [
        {"id": "empty", "page": 1, "rows": []},
        {"id": "small", "page": 1, "header": ["S"], "rows": [["1"]]},
        {"id": "mid", "page": 1, "header": ["M", "N"], "rows": [["1", "2"], ["3", "4"]]},
        {"id": "big", "page": 1, "header": ["A", "B", "C"], "rows": [["1", "2", "3"]] * 3},
        {
            "id": "huge",
            "page": 1,
            "header": ["W", "X", "Y", "Z"],
            "rows": [["1", "2", "3", "4"]] * 4,
        },
    ]
    kept = draft_yaml(_document(tables=tables), file_ref="a.pdf", doc_id="a")
    assert kept.index('"W"') < kept.index('"A"') < kept.index('"M"')
    assert '"S"' not in kept

    bare = draft_yaml(
        _document(tables=[{"id": "h", "page": 1, "header": ["Only"], "rows": []}]),
        file_ref="a.pdf",
        doc_id="a",
    )
    assert "cells:" not in bare
    assert "rows: 0 # TODO verify" in bare

    dup = draft_yaml(
        _document(
            tables=[{"id": "d", "page": 1, "header": ["A", "A", ""], "rows": [["l", "r", "e"]]}]
        ),
        file_ref="a.pdf",
        doc_id="a",
    )
    assert "col: 0 # TODO verify" in dup
    assert "col: 1 # TODO verify" in dup
    assert 'col: "A"' not in dup

    single = draft_yaml(
        _document(
            blocks=[_block("b1", "one"), _block("b2", "two"), _block("b3", "three")],
        ),
        file_ref="a.pdf",
        doc_id="a",
    )
    assert '- - "one" # TODO verify\n      - "three" # TODO verify' in single
    assert '- - "one" # TODO verify\n      - "two" # TODO verify' in single

    spanned = draft_yaml(
        _document(
            pages=[{"number": 1}, {"number": 2}, {"number": 3}],
            blocks=[
                _block("b1", "End one", 1),
                _block("b2", "Start two", 2),
                _block("b3", "End two", 2),
                _block("b4", "Start three", 3),
                _block("b5", "End three", 3),
            ],
        ),
        file_ref="a.pdf",
        doc_id="a",
    )
    assert '- - "End one" # TODO verify\n      - "Start two" # TODO verify' in spanned
    assert '- - "End two" # TODO verify\n      - "Start three" # TODO verify' in spanned

    repeated = draft_yaml(
        _document(blocks=[_block("b1", "Same."), _block("b2", "Same.")]),
        file_ref="a.pdf",
        doc_id="a",
    )
    assert "reading_order:" not in repeated
    assert repeated.count('"Same."') == 1


def test_quotes_unusual_keys_and_confirmed_yaml() -> None:
    drafted = draft_yaml(
        _document(
            fields={
                "say": {"value": 'He said "hi"\nnext'},
                "a-b": {"value": "x"},
                "café": {"value": "oui"},
                "rate": {"value": 1.5},
                "ok": {"value": True},
            }
        ),
        file_ref="a.pdf",
        doc_id="a",
    )
    assert 'say: "He said \\"hi\\"\\nnext" # TODO verify' in drafted
    assert '"a-b": "x" # TODO verify' in drafted
    assert '"café": "oui" # TODO verify' in drafted
    assert "rate: 1.5 # TODO verify" in drafted
    assert "ok: true # TODO verify" in drafted
    confirmed = render_yaml({"file": "a.pdf"}, todo=False)
    assert confirmed == 'file: "a.pdf"\n'
    with pytest.raises(LabelError):
        render_yaml({"id": "a"}, todo=False)


def test_strict_validate_passes_after_todos_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden = tmp_path / "golden"
    golden.mkdir()
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "lease.pdf").write_bytes(b"%PDF-1.4\n")
    target = golden / "lease.yaml"
    target.write_text((FIXTURE / "expected.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert validate_command(target, strict=True) == 1
    loaded = yaml.load(target.read_text(encoding="utf-8"), Loader=CoreLoader)
    assert isinstance(loaded, dict)
    target.write_text(render_yaml(loaded, todo=False), encoding="utf-8")
    assert "# TODO verify" not in target.read_text(encoding="utf-8")
    assert validate_command(target, strict=True) == 0


def test_review_page_saves_without_todos(tmp_path: Path) -> None:
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (tmp_path / "files").mkdir()
    pdf = b"%PDF-1.4\n"
    (tmp_path / "files" / "lease.pdf").write_bytes(pdf)
    target = golden_dir / "lease.yaml"
    target.write_text((FIXTURE / "expected.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    server = start_review(target, open_browser=False)
    try:
        with urlopen(server.url + "/") as response:
            page = response.read()
        assert b"Widget" in page
        assert b'iframe src="/source"' in page
        assert b'fetch("/save"' in page
        lowered = page.lower()
        assert b"cdnjs" not in lowered
        assert b"unpkg" not in lowered
        assert b"jsdelivr" not in lowered
        with pytest.raises(HTTPError) as missing:
            urlopen(server.url + "/missing")
        assert missing.value.code == 404
        with urlopen(server.url + "/source") as response:
            assert response.headers["Content-Type"] == "application/pdf"
            assert response.read() == pdf
        bad = Request(
            server.url + "/save",
            data=b'{"nope": 1}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as rejected:
            urlopen(bad)
        assert rejected.value.code == 400
        body = json.dumps(
            {
                "file": "../files/lease.pdf",
                "id": "lease",
                "expect": {"text_contains": ["Kept sentence."]},
            }
        ).encode()
        saved = Request(
            server.url + "/save",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(saved) as response:
            assert response.read() == b"saved\n"
        server.wait(timeout=2)
        text = target.read_text(encoding="utf-8")
        assert "# TODO verify" not in text
        assert "Kept sentence." in text
    finally:
        server.close()


def test_review_rejects_bad_golden_and_missing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "absent.yaml"
    with pytest.raises(LabelError) as not_found:
        start_review(missing, open_browser=False)
    assert not_found.value.exit_code == 2
    broken = tmp_path / "broken.yaml"
    broken.write_text("[\n", encoding="utf-8")
    with pytest.raises(LabelError):
        start_review(broken, open_browser=False)
    listing = tmp_path / "list.yaml"
    listing.write_text("[]\n", encoding="utf-8")
    with pytest.raises(LabelError):
        start_review(listing, open_browser=False)
    plain = tmp_path / "plain.yaml"
    plain.write_text('file: "missing.pdf"\nid: "plain"\n', encoding="utf-8")
    server = start_review(plain, open_browser=False)
    try:
        with pytest.raises(HTTPError) as missing_source:
            urlopen(server.url + "/source")
        assert missing_source.value.code == 404
        with urlopen(server.url + "/shutdown") as response:
            assert response.read() == b"bye\n"
        server.wait(timeout=2)
    finally:
        server.close()
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    again = start_review(plain, open_browser=True)
    try:
        deadline = time.time() + 2
        while not opened and time.time() < deadline:
            time.sleep(0.01)
        assert opened == [again.url]
    finally:
        again.close()
    assert "<html" in review_html({"file": "a.pdf", "id": "a"}).lower()


def test_label_command_drafts_with_the_file_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _prepare(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert label_command(Path("files/lease.pdf"), parser="file", config=Path("docassay.yaml")) == 0
    assert (tmp_path / "golden" / "lease.yaml").read_text(encoding="utf-8") == (
        FIXTURE / "expected.yaml"
    ).read_text(encoding="utf-8")
    assert capsys.readouterr().out == "wrote golden/lease.yaml\n"
    assert (
        label_command(
            Path("files/lease.pdf"),
            parser="file",
            out=Path("nested/out.yaml"),
            config=Path("docassay.yaml"),
        )
        == 0
    )
    nested = (tmp_path / "nested" / "out.yaml").read_text(encoding="utf-8")
    assert nested.startswith('file: "../files/lease.pdf"\n')
    assert label_command(Path("missing.pdf"), parser="file") == 2
    assert label_command(Path("files/lease.pdf"), parser="file", review=True) == 2
    assert label_command(Path("files/lease.pdf")) == 2
    assert label_command(Path("files/lease.pdf"), parser="no-such-parser") == 2
    (tmp_path / "parsed" / "lease.json").unlink()
    assert label_command(Path("files/lease.pdf"), parser="file", config=Path("docassay.yaml")) == 3
    (tmp_path / "docassay.yaml").write_text(
        "version: 1\ngolden: golden\nparsers: [\n", encoding="utf-8"
    )
    assert label_command(Path("files/lease.pdf"), parser="file", config=Path("docassay.yaml")) == 2
    assert label_command(Path("missing.yaml"), review=True, open_browser=False) == 2


def test_label_review_command_prints_the_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "plain.yaml"
    target.write_text('file: "missing.pdf"\nid: "plain"\n', encoding="utf-8")
    holder: dict[str, Any] = {}
    real = start_review

    def wrapped(path: Path, *, open_browser: bool = True) -> Any:
        del open_browser
        server = real(path, open_browser=False)
        holder["server"] = server
        return server

    monkeypatch.setattr("docassay.label.start_review", wrapped)

    def run() -> None:
        holder["code"] = label_command(target, review=True, open_browser=False)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.time() + 2
    while "server" not in holder and time.time() < deadline:
        time.sleep(0.01)
    urlopen(holder["server"].url + "/shutdown").read()
    thread.join(2)
    assert holder["code"] == 0
    output = capsys.readouterr().out
    assert "review at http://127.0.0.1:" in output
    assert f"wrote {target.as_posix()}\n" in output


def test_cli_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    usage = runner.invoke(app, ["label", "files/lease.pdf"])
    assert usage.exit_code == 2
    drafted = runner.invoke(
        app,
        ["label", "files/lease.pdf", "--from", "file", "-c", "docassay.yaml"],
    )
    assert drafted.exit_code == 0
    assert "wrote golden/lease.yaml" in drafted.stdout
    assert (tmp_path / "golden" / "lease.yaml").is_file()
