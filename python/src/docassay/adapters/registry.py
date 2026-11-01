"""Built-in adapter ids and third-party adapters from ``docassay.adapters``.

Example:
    from docassay.adapters.registry import resolve

    adapter = resolve("llamaparse:fast")
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any, cast

from docassay.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterError,
    RawResult,
)
from docassay.adapters.file import FileAdapter
from docassay.models.document import Document, Source

# Built-ins in the Adapters doc order. Variants follow the protocol comment.
BUILTINS: dict[str, tuple[str, ...]] = {
    "docling": (),
    "file": (),
    "llamaparse": ("fast", "cost_effective", "agentic", "agentic_plus"),
    "reducto": (),
}

Factory = Callable[[str | None, dict[str, Any]], Adapter]


class _PendingAdapter:
    """Placeholder for a built-in whose module is implemented by a later issue."""

    def __init__(
        self,
        adapter_id: str,
        variants: tuple[str, ...],
        variant: str | None,
        options: dict[str, Any],
    ) -> None:
        self.id = adapter_id
        self.variants = variants
        self.variant = variant
        self.options = options

    def available(self) -> tuple[bool, str]:
        return False, f"{self.id} adapter is not available in this build"

    def cache_identity(self) -> dict[str, Any]:
        return {"id": self.id, "variant": self.variant, "options": dict(self.options)}

    def fetch(self, path: object) -> RawResult:
        raise AdapterError(f"{self.id} adapter is not available in this build; cannot fetch {path}")

    def to_document(self, raw: RawResult, source: Source) -> Document:
        del raw, source
        raise AdapterError(f"{self.id} adapter is not available in this build")


class _Factory:
    def __init__(self, adapter_id: str, variants: tuple[str, ...], build: Factory) -> None:
        self.id = adapter_id
        self.variants = variants
        self._build = build

    def __call__(self, variant: str | None, options: dict[str, Any]) -> Adapter:
        return self._build(variant, options)


def resolve(parser_id: str, options: dict[str, Any] | None = None) -> Adapter:
    """Build the adapter for ``id`` or ``id:variant``.

    Unknown ids and variants raise :class:`AdapterConfigError` (exit 2). The
    message lists every id this process can load.

    Example:
        adapter = resolve("docling", {"mode": "http"})
    """
    adapter_id, variant = _split_id(parser_id)
    factories = _factories()
    factory = factories.get(adapter_id)
    if factory is None:
        raise AdapterConfigError(
            f'unknown parser id "{parser_id}"; valid ids: {_valid_ids(factories)}'
        )
    if variant is None and factory.variants:
        listed = ", ".join(factory.variants)
        raise AdapterConfigError(
            f'missing variant for "{adapter_id}"; valid ids: {_valid_ids(factories)}; '
            f"valid variants: {listed}"
        )
    if variant is not None and variant not in factory.variants:
        detail = f"; valid variants: {', '.join(factory.variants)}" if factory.variants else ""
        raise AdapterConfigError(
            f'unknown variant "{variant}" for "{adapter_id}"; '
            f"valid ids: {_valid_ids(factories)}{detail}"
        )
    return factory(variant, {} if options is None else options)


def valid_ids() -> str:
    """Return the adapter ids a config error should list, in a stable order.

    Example:
        print(valid_ids())
    """
    return _valid_ids(_factories())


def _factories() -> dict[str, _Factory]:
    found: dict[str, _Factory] = {
        adapter_id: _Factory(adapter_id, variants, _builtin_factory(adapter_id, variants))
        for adapter_id, variants in BUILTINS.items()
    }
    for point in entry_points(group="docassay.adapters"):
        loaded = point.load()
        adapter_id = _text_attr(loaded, "id") or point.name
        if adapter_id in BUILTINS:
            continue
        variants = _variants(loaded)
        build = loaded if callable(loaded) else _refuse(adapter_id)
        found[adapter_id] = _Factory(adapter_id, variants, build)
    return found


def _builtin_factory(adapter_id: str, variants: tuple[str, ...]) -> Factory:
    if adapter_id == "file":

        def build_file(variant: str | None, options: dict[str, Any]) -> Adapter:
            return FileAdapter(variant, options)

        return build_file
    return _pending(adapter_id, variants)


def _pending(adapter_id: str, variants: tuple[str, ...]) -> Factory:
    def build(variant: str | None, options: dict[str, Any]) -> Adapter:
        return cast(Adapter, _PendingAdapter(adapter_id, variants, variant, options))

    return build


def _refuse(adapter_id: str) -> Factory:
    def build(variant: str | None, options: dict[str, Any]) -> Adapter:
        del variant, options
        raise AdapterConfigError(
            f'entry point "{adapter_id}" is not callable; '
            "point it at a function (variant, options) -> adapter"
        )

    return build


def _valid_ids(factories: dict[str, _Factory]) -> str:
    extras = sorted(adapter_id for adapter_id in factories if adapter_id not in BUILTINS)
    return ", ".join([*BUILTINS.keys(), *extras])


def _split_id(parser_id: str) -> tuple[str, str | None]:
    if ":" in parser_id:
        adapter_id, variant = parser_id.split(":", 1)
        return adapter_id, variant
    return parser_id, None


def _text_attr(loaded: object, name: str) -> str:
    value = getattr(loaded, name, None)
    if isinstance(value, str) and value:
        return value
    return ""


def _variants(loaded: object) -> tuple[str, ...]:
    value = getattr(loaded, "variants", ())
    if isinstance(value, tuple):
        return tuple(item for item in value if isinstance(item, str))
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()
