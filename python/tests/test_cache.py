"""Parser output cache. Python and TypeScript share spec/fixtures/cache/key.json."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from docassay.cache import (
    CacheError,
    cache_path,
    clear_cache,
    file_sha256,
    lookup_cache,
    write_cache,
)
from docassay.cli import app
from docassay.models.document import Document

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "spec" / "examples" / "document.example.json"
KEY = ROOT / "spec" / "fixtures" / "cache" / "key.json"
SHA = "ab" * 32


def _document(parser_version: str = "2026-09-24") -> Document:
    raw = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    raw["meta"]["parserVersion"] = parser_version
    return Document.model_validate(raw)


def test_fixture_paths_match_bytes_and_identity() -> None:
    loaded = json.loads(KEY.read_text(encoding="utf-8"))
    cases = loaded["cases"]
    assert isinstance(cases, list)
    for case in cases:
        digest = file_sha256(case["bytes"].encode("utf-8"))
        got = cache_path(Path(case["cache"]), digest, case["adapterId"], case["identity"])
        assert got.as_posix() == case["path"]


def test_integer_floats_share_the_integer_path(tmp_path: Path) -> None:
    digest = file_sha256(b"hello")
    whole = cache_path(tmp_path, digest, "file", {"n": 1.0})
    exact = cache_path(tmp_path, digest, "file", {"n": 1})
    assert whole == exact


def test_round_trip_keeps_parser_version_latency_and_cost(tmp_path: Path) -> None:
    document = _document()
    identity = {"dir": "out/parsed"}
    write_cache(tmp_path, SHA, "llamaparse:agentic", identity, document)
    found = lookup_cache(tmp_path, SHA, "llamaparse:agentic", identity)
    assert found.document is not None
    assert found.warning is None
    assert found.document.meta.parser_version == "2026-09-24"
    assert found.document.meta.latency_ms == 840
    assert found.document.meta.cost_usd == 0.012


def test_verbose_hit_is_the_dash_v_line(tmp_path: Path, capsys: object) -> None:
    document = _document()
    identity: dict[str, str] = {}
    write_cache(tmp_path, SHA, "file", identity, document)
    quiet = lookup_cache(tmp_path, SHA, "file", identity, verbose=False)
    assert quiet.message is None
    loud = lookup_cache(tmp_path, SHA, "file", identity, verbose=True)
    assert loud.message == f"cache hit file {SHA}"
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert loud.message in captured.err


def test_no_cache_skips_reads_and_still_writes(tmp_path: Path) -> None:
    identity: dict[str, str] = {"dir": "out"}
    write_cache(tmp_path, SHA, "file", identity, _document("first"))
    skipped = lookup_cache(tmp_path, SHA, "file", identity, policy="no-cache")
    assert skipped.document is None
    assert skipped.warning is None
    write_cache(tmp_path, SHA, "file", identity, _document("second"))
    found = lookup_cache(tmp_path, SHA, "file", identity)
    assert found.document is not None
    assert found.document.meta.parser_version == "second"


def test_refresh_overwrites_the_entry(tmp_path: Path) -> None:
    identity: dict[str, str] = {"dir": "out"}
    write_cache(tmp_path, SHA, "file", identity, _document("first"))
    skipped = lookup_cache(tmp_path, SHA, "file", identity, policy="refresh")
    assert skipped.document is None
    write_cache(tmp_path, SHA, "file", identity, _document("second"))
    found = lookup_cache(tmp_path, SHA, "file", identity)
    assert found.document is not None
    assert found.document.meta.parser_version == "second"


def test_corrupt_and_invalid_entries_warn_and_are_replaced(tmp_path: Path, capsys: object) -> None:
    identity: dict[str, str] = {"dir": "out"}
    path = cache_path(tmp_path, SHA, "file", identity)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    corrupt = lookup_cache(tmp_path, SHA, "file", identity)
    assert corrupt.document is None
    assert corrupt.warning is not None
    assert "invalid JSON" in corrupt.warning
    path.write_text('{"specVersion": "1.0.0"}\n', encoding="utf-8")
    invalid = lookup_cache(tmp_path, SHA, "file", identity)
    assert invalid.document is None
    assert invalid.warning is not None
    assert "does not match document.schema.json" in invalid.warning
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "invalid JSON" in captured.err
    assert "does not match document.schema.json" in captured.err
    write_cache(tmp_path, SHA, "file", identity, _document())
    found = lookup_cache(tmp_path, SHA, "file", identity)
    assert found.document is not None
    assert found.warning is None


def test_clear_can_limit_by_parser_id(tmp_path: Path) -> None:
    identity: dict[str, str] = {}
    write_cache(tmp_path, SHA, "file", identity, _document())
    write_cache(tmp_path, SHA, "llamaparse:agentic", identity, _document())
    assert clear_cache(tmp_path, "llamaparse:fast") == 0
    assert clear_cache(tmp_path, "llamaparse") == 1
    assert lookup_cache(tmp_path, SHA, "file", identity).document is not None
    assert lookup_cache(tmp_path, SHA, "llamaparse:agentic", identity).document is None
    assert clear_cache(tmp_path) == 1
    assert list(tmp_path.rglob("*.json")) == []


def test_cache_clear_command(tmp_path: Path) -> None:
    identity: dict[str, str] = {}
    write_cache(tmp_path, SHA, "file", identity, _document())
    result = CliRunner().invoke(
        app, ["cache", "clear", "--parser", "file", "--cache", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == f"removed 1 cache file(s) for file from {tmp_path.as_posix()}"
    assert lookup_cache(tmp_path, SHA, "file", identity).document is None


def test_clear_ignores_a_missing_dir_and_unrelated_json(tmp_path: Path) -> None:
    assert clear_cache(tmp_path / "absent") == 0
    identity: dict[str, str] = {}
    write_cache(tmp_path, SHA, "file", identity, _document())
    stray = tmp_path / SHA[:2] / SHA / "notes.json"
    stray.write_text("{}", encoding="utf-8")
    assert clear_cache(tmp_path) == 1
    assert stray.is_file()
    assert not any(tmp_path.rglob("file__*.json"))


def test_clear_refuses_a_file_and_a_bad_policy(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")
    try:
        clear_cache(target)
    except CacheError as exc:
        assert ".docassay/cache" in str(exc)
    else:
        raise AssertionError("expected CacheError")
    try:
        lookup_cache(tmp_path, SHA, "file", {}, policy="maybe")  # type: ignore[arg-type]
    except CacheError as exc:
        assert "unknown cache policy" in str(exc)
    else:
        raise AssertionError("expected CacheError")


def test_no_cache_does_not_warn_about_corrupt_json(tmp_path: Path, capsys: object) -> None:
    identity: dict[str, str] = {"dir": "out"}
    path = cache_path(tmp_path, SHA, "file", identity)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    skipped = lookup_cache(tmp_path, SHA, "file", identity, policy="no-cache")
    assert skipped.document is None
    assert skipped.warning is None
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""


def test_bad_sha_and_identity_say_how_to_fix() -> None:
    try:
        cache_path(Path("."), "abc", "file", {})
    except CacheError as exc:
        assert "64 lowercase hex" in str(exc)
    else:
        raise AssertionError("expected CacheError")
    try:
        cache_path(Path("."), SHA, "file", {"dir": object()})
    except CacheError as exc:
        assert "JSON objects" in str(exc)
    else:
        raise AssertionError("expected CacheError")
