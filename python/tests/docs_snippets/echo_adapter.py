"""Minimal adapter: read a text file and return one paragraph block."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from docassay.adapters.base import AdapterError, RawResult
from docassay.models.document import Block, Document, Meta, Source, Type


class EchoAdapter:
    """Map a `{text: ...}` payload to a single paragraph. `fetch` reads the file."""

    id: ClassVar[str] = "echo"
    variants: ClassVar[tuple[str, ...]] = ()

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None:
        self.variant = variant
        self.options = options

    def available(self) -> tuple[bool, str]:
        return True, ""

    def cache_identity(self) -> dict[str, Any]:
        return {"id": self.id, "variant": self.variant, "options": dict(self.options)}

    def fetch(self, path: Path) -> RawResult:
        return RawResult(
            payload={"text": path.read_text(encoding="utf-8")},
            parser_version="echo-1",
            latency_ms=0,
        )

    def to_document(self, raw: RawResult, source: Source) -> Document:
        payload = raw.payload
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise AdapterError("payload.text must be a string")
        return Document(
            spec_version="1.0.0",
            source=source,
            meta=Meta(parser="echo", parser_version=raw.parser_version, adapter_version="0.0.0"),
            pages=[],
            blocks=[Block(id="b1", type=Type.paragraph, text=text, page=1)],
            tables=[],
        )
