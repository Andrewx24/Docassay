"""Adapter protocol, registry, and runner metadata."""

import hashlib
import importlib
import sys
from pathlib import Path

import pytest

import docassay.adapters.registry as registry_module
from docassay import __version__
from docassay.adapters import (
    AdapterConfigError,
    AdapterError,
    AdapterUnavailable,
    ensure_available,
    resolve,
    run_parser,
    valid_ids,
)
from docassay.adapters.runner_support import build_source, detect_mime

DUMMY_ROOT = Path(__file__).resolve().parent / "dummy_adapter"
VENDOR_MODULES = ("docling", "llama_cloud", "llama_parse", "reducto")


def test_import_does_not_load_vendor_sdks() -> None:
    import docassay.adapters.base
    import docassay.adapters.registry
    import docassay.adapters.runner_support

    del docassay.adapters.base, docassay.adapters.registry, docassay.adapters.runner_support
    for name in VENDOR_MODULES:
        assert name not in sys.modules


def test_unknown_id_and_variant_are_config_errors() -> None:
    with pytest.raises(AdapterConfigError) as unknown:
        resolve("nope")
    assert unknown.value.exit_code == 2
    assert 'unknown parser id "nope"' in str(unknown.value)
    assert "valid ids: docling, file, llamaparse, reducto" in str(unknown.value)

    with pytest.raises(AdapterConfigError) as missing:
        resolve("llamaparse")
    assert missing.value.exit_code == 2
    assert "missing variant" in str(missing.value)
    assert "valid variants: fast, cost_effective, agentic, agentic_plus" in str(missing.value)

    with pytest.raises(AdapterConfigError) as bad:
        resolve("llamaparse:turbo")
    assert 'unknown variant "turbo" for "llamaparse"' in str(bad.value)

    with pytest.raises(AdapterConfigError) as plain:
        resolve("docling:fast")
    assert 'unknown variant "fast" for "docling"' in str(plain.value)
    assert "valid variants" not in str(plain.value)


def test_builtin_is_unavailable_until_its_issue_lands() -> None:
    adapter = resolve("reducto", {"mode": "http"})
    ok, reason = adapter.available()
    assert ok is False
    assert reason == "reducto adapter is not available in this build"
    assert adapter.cache_identity()["options"] == {"mode": "http"}
    with pytest.raises(AdapterError):
        adapter.fetch(Path("missing.pdf"))
    with pytest.raises(AdapterError):
        adapter.to_document(object(), object())  # type: ignore[arg-type]
    file_adapter = resolve("file")
    assert file_adapter.available()[0] is False
    assert "options.dir" in file_adapter.available()[1]
    with pytest.raises(AdapterUnavailable) as exc:
        ensure_available([("reducto", adapter), ("file", file_adapter)])
    assert exc.value.exit_code == 3
    assert "reducto: reducto adapter is not available in this build" in str(exc.value)
    assert "options.dir" in str(exc.value)


def test_entry_point_discovers_dummy_package(tmp_path: Path) -> None:
    sys.path.insert(0, str(DUMMY_ROOT))
    importlib.invalidate_caches()
    try:
        adapter = resolve("dummy", {"cost": 1.5})
        assert adapter.available() == (True, "")
        assert "dummy" in valid_ids()
        sample = tmp_path / "note.txt"
        sample.write_text("hello", encoding="utf-8")
        document = run_parser(adapter, sample, "dummy", stored_path="notes/note.txt")
        assert document.source.path == "notes/note.txt"
        assert document.source.sha256 == hashlib.sha256(b"hello").hexdigest()
        assert document.source.mime == "text/plain"
        assert document.meta.parser == "dummy"
        assert document.meta.parser_version == "dummy-1"
        assert document.meta.adapter_version == __version__
        assert document.meta.latency_ms == 12.5
        assert document.meta.cost_usd == 1.5

        blocked = resolve("dummy", {"missing": True})
        with pytest.raises(AdapterUnavailable) as exc:
            ensure_available([("dummy", blocked)])
        assert exc.value.exit_code == 3
        assert "dummy: missing token" in str(exc.value)
    finally:
        sys.path.remove(str(DUMMY_ROOT))
        sys.modules.pop("dummy_adapter", None)
        importlib.invalidate_caches()


def test_mime_uses_magic_then_extension(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.txt"
    pdf.write_bytes(b"%PDF-1.7\n")
    assert detect_mime(pdf.read_bytes(), pdf) == "application/pdf"
    assert detect_mime(b"\x89PNG\r\n\x1a\n", tmp_path / "x.bin") == "image/png"

    office = tmp_path / "memo.docx"
    office.write_bytes(b"PK\x03\x04rest")
    assert detect_mime(office.read_bytes(), office).endswith("wordprocessingml.document")

    webp = tmp_path / "pic.bin"
    webp.write_bytes(b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"data")
    assert detect_mime(webp.read_bytes(), webp) == "image/webp"

    plain = tmp_path / "notes.md"
    plain.write_bytes(b"# hi\n")
    source = build_source(plain, "notes.md")
    assert source.mime == "text/markdown"
    assert source.sha256 == hashlib.sha256(b"# hi\n").hexdigest()
    assert len(source.sha256) == 64

    unknown = tmp_path / "blob"
    unknown.write_bytes(b"\x00\x01")
    assert detect_mime(unknown.read_bytes(), unknown) == "application/octet-stream"

    archive = tmp_path / "bundle.bin"
    archive.write_bytes(b"PK\x03\x04rest")
    assert detect_mime(archive.read_bytes(), archive) == "application/zip"


def test_entry_points_skip_builtins_and_reject_bad_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Listed:
        variants = ["alpha", 1]

        def __init__(self, variant: str | None, options: dict[str, object]) -> None:
            self.variant = variant
            self.options = options

        def available(self) -> tuple[bool, str]:
            return False, ""

        def cache_identity(self) -> dict[str, object]:
            return {}

        def fetch(self, path: Path) -> object:
            del path
            return object()

        def to_document(self, raw: object, source: object) -> object:
            del raw, source
            return object()

    class Point:
        def __init__(self, name: str, loaded: object) -> None:
            self.name = name
            self._loaded = loaded

        def load(self) -> object:
            return self._loaded

    def fake_entry_points(*, group: str) -> list[Point]:
        assert group == "docassay.adapters"
        return [
            Point("docling", type("Builtin", (), {"id": "docling"})),
            Point("plain", object()),
            Point("listed", Listed),
        ]

    monkeypatch.setattr(registry_module, "entry_points", fake_entry_points)
    docling_ok, docling_reason = resolve("docling").available()
    if importlib.util.find_spec("docling") is None:
        assert docling_ok is False
        assert "docling extra is not installed" in docling_reason
    else:
        assert docling_ok is True
    with pytest.raises(AdapterConfigError, match="not callable"):
        resolve("plain")
    listed = resolve("listed:alpha")
    with pytest.raises(AdapterUnavailable, match="listed: unavailable"):
        ensure_available([("listed", listed)])
