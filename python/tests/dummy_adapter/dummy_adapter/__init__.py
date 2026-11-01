"""Tiny third-party adapter used to test entry-point discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from docassay.adapters.base import RawResult
from docassay.models.document import Document, Meta, Source


class DummyAdapter:
    """In-process adapter. ``options['missing']`` makes ``available()`` fail."""

    id: ClassVar[str] = "dummy"
    variants: ClassVar[tuple[str, ...]] = ()

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None:
        self.variant = variant
        self.options = options

    def available(self) -> tuple[bool, str]:
        if self.options.get("missing"):
            return False, "missing token"
        return True, ""

    def cache_identity(self) -> dict[str, Any]:
        return {"id": self.id, "variant": self.variant, "options": dict(self.options)}

    def fetch(self, path: Path) -> RawResult:
        del path
        cost = self.options.get("cost")
        parsed = float(cost) if isinstance(cost, int | float) else 0.01
        return RawResult(
            payload={"text": "hello"},
            parser_version="dummy-1",
            latency_ms=12.5,
            cost_usd=parsed,
        )

    def to_document(self, raw: RawResult, source: Source) -> Document:
        del raw
        return Document(
            spec_version="1.0.0",
            source=source,
            meta=Meta(parser="placeholder", parser_version="from-adapter", adapter_version="0"),
            pages=[],
            blocks=[],
            tables=[],
        )
