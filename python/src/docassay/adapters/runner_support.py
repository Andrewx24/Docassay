"""Source hashing and the meta fields the runner, not the adapter, fills.

Example:
    from docassay.adapters.runner_support import run_parser

    document = run_parser(adapter, path, "file")
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from docassay.adapters.base import Adapter, AdapterUnavailable, RawResult
from docassay.models.document import Document, Source

_MAGICS: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

_OFFICE: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".doc": "application/msword",
    **_OFFICE,
}

_ZIP = b"PK\x03\x04"
_OCTET = "application/octet-stream"


def package_version() -> str:
    """Return the installed docassay version, used as ``adapterVersion``.

    Example:
        print(package_version())
    """
    return version("docassay")


def detect_mime(data: bytes, file_path: Path) -> str:
    """Return a mime type from magic bytes, then the file extension.

    Example:
        detect_mime(b"%PDF-1.7", Path("scan.pdf"))
    """
    extension = file_path.suffix.lower()
    if data.startswith(_ZIP) and extension in _OFFICE:
        return _OFFICE[extension]
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _MAGICS:
        if data.startswith(magic):
            return mime
    if data.startswith(_ZIP):
        return "application/zip"
    return _EXTENSIONS.get(extension, _OCTET)


def build_source(file_path: Path, stored_path: str | None = None) -> Source:
    """Hash ``file_path`` and detect its mime type.

    ``stored_path`` is the path written on the Document (relative to the config
    file). When it is omitted, the filesystem path is stored.

    Example:
        source = build_source(Path("files/invoice.pdf"), "files/invoice.pdf")
    """
    data = file_path.read_bytes()
    path = file_path.as_posix() if stored_path is None else stored_path
    return Source(
        path=path, sha256=hashlib.sha256(data).hexdigest(), mime=detect_mime(data, file_path)
    )


def apply_run_meta(
    document: Document,
    raw: RawResult,
    *,
    parser_id: str,
    adapter_version: str,
) -> Document:
    """Copy parser id, parser version, latency, cost, and adapter version onto ``document``.

    Example:
        apply_run_meta(document, raw, parser_id="file", adapter_version="0.0.0")
    """
    document.meta.parser = parser_id
    document.meta.parser_version = raw.parser_version
    document.meta.adapter_version = adapter_version
    document.meta.latency_ms = raw.latency_ms
    if raw.cost_usd is not None:
        document.meta.cost_usd = raw.cost_usd
    return document


def run_parser(
    adapter: Adapter,
    file_path: Path,
    parser_id: str,
    *,
    stored_path: str | None = None,
    adapter_version: str | None = None,
) -> Document:
    """Fetch one file and return a Document with runner-owned meta filled in.

    Example:
        document = run_parser(adapter, Path("files/a.pdf"), "dummy")
    """
    raw = adapter.fetch(file_path)
    source = build_source(file_path, stored_path)
    document = adapter.to_document(raw, source)
    version_text = package_version() if adapter_version is None else adapter_version
    return apply_run_meta(document, raw, parser_id=parser_id, adapter_version=version_text)


def ensure_available(adapters: Sequence[tuple[str, Adapter]]) -> None:
    """Call ``available()`` on every parser. Raise when any of them cannot run.

    The exception's ``exit_code`` is 3 and the message includes each reason.

    Example:
        ensure_available([("docling", resolve("docling"))])
    """
    failures: list[str] = []
    for parser_id, adapter in adapters:
        ok, reason = adapter.available()
        if not ok:
            detail = reason if reason else "unavailable"
            failures.append(f"{parser_id}: {detail}")
    if failures:
        raise AdapterUnavailable("\n".join(failures))
