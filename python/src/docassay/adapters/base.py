"""Adapter protocol, raw fetch result, and adapter errors.

The Adapters doc splits every adapter into ``fetch`` (I/O) and ``to_document``
(pure mapping). Vendor SDKs are imported inside ``fetch``, never at module load.

Example:
    from docassay.adapters.base import AdapterError

    raise AdapterError("vendor returned 503")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from docassay.models.document import Document, Source

__all__ = [
    "Adapter",
    "AdapterConfigError",
    "AdapterError",
    "AdapterUnavailable",
    "Document",
    "RawResult",
    "Source",
]


@dataclass(frozen=True)
class RawResult:
    """Vendor payload plus the facts the runner copies onto ``Document.meta``.

    Example:
        RawResult(payload={"text": "hello"}, parser_version="2026-09-24", latency_ms=10)
    """

    payload: Any
    parser_version: str
    latency_ms: float
    cost_usd: float | None = None


class Adapter(Protocol):
    """One parser. ``id`` is the name before any ``:variant``.

    Example:
        class FileAdapter:
            id = "file"
            variants = ()
    """

    id: ClassVar[str]
    variants: ClassVar[tuple[str, ...]]

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None:
        """Store ``variant`` and the options from ``docassay.yaml``."""

    def available(self) -> tuple[bool, str]:
        """Return whether a run can start, and a reason when it cannot.

        Example:
            ok, reason = adapter.available()
        """

    def cache_identity(self) -> dict[str, Any]:
        """Return the variant and options that change parser output.

        Example:
            identity = adapter.cache_identity()
        """

    def fetch(self, path: Path) -> RawResult:
        """Call the parser. This is the only method that may touch the network.

        Example:
            raw = adapter.fetch(Path("files/invoice.pdf"))
        """

    def to_document(self, raw: RawResult, source: Source) -> Document:
        """Map a recorded payload to a Document. This method is pure.

        Example:
            document = adapter.to_document(raw, source)
        """


class AdapterError(Exception):
    """A vendor or network failure while fetching one document.

    The runner records the message and continues with the next document.

    Example:
        raise AdapterError("LlamaParse returned HTTP 503")
    """


class AdapterUnavailable(Exception):
    """``available()`` failed for one or more parsers. CLI commands exit 3.

    Example:
        raise AdapterUnavailable("docling: DOCLING_BASE_URL is not set")
    """

    exit_code: ClassVar[int] = 3


class AdapterConfigError(Exception):
    """Unknown adapter id or variant. CLI commands exit 2.

    Example:
        raise AdapterConfigError('unknown parser id "nope"; valid ids: docling, file')
    """

    exit_code: ClassVar[int] = 2
