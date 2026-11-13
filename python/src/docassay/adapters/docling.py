"""Docling adapter.

Python calls the ``docling`` library (extra ``docassay[docling]``). Mapping is
pure and shared with the TypeScript client, which posts the same JSON to
docling-serve. ``parserVersion`` is the installed Docling version.

Example:
    from docassay.adapters.docling import DoclingAdapter

    adapter = DoclingAdapter(None, {})
    ok, reason = adapter.available()
"""

from __future__ import annotations

import importlib
import math
import time
from pathlib import Path
from typing import Any, ClassVar

from docassay.adapters.base import AdapterError, RawResult
from docassay.adapters.runner_support import package_version
from docassay.models.document import BBox, Block, Document, Meta, Page, Source, Table, Type

INSTALL = "docling extra is not installed. Install it with: pip install 'docassay[docling]'"

_LABELS: dict[str, Type] = {
    "title": Type.title,
    "section_header": Type.heading,
    "paragraph": Type.paragraph,
    "text": Type.paragraph,
    "list_item": Type.list_item,
    "caption": Type.caption,
    "footnote": Type.footnote,
    "page_header": Type.header,
    "page_footer": Type.footer,
    "code": Type.code,
    "formula": Type.formula,
}

_COLLECTIONS = ("texts", "tables", "groups", "pictures", "key_value_items")


class DoclingAdapter:
    """Parse with Docling and map a DoclingDocument export to a Document.

    ``cache_identity`` is the options object. Conversion uses Docling's defaults;
    options are still part of the cache key so two configs do not share a file.

    Example:
        DoclingAdapter(None, {}).cache_identity()
    """

    id: ClassVar[str] = "docling"
    variants: ClassVar[tuple[str, ...]] = ()

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None:
        self.variant = variant
        self.options = options

    def available(self) -> tuple[bool, str]:
        """Return whether the ``docling`` extra imports.

        Example:
            ok, reason = DoclingAdapter(None, {}).available()
        """
        try:
            importlib.import_module("docling")
        except ImportError:
            return False, INSTALL
        return True, ""

    def cache_identity(self) -> dict[str, Any]:
        """Return the options that identify a cached Docling parse.

        Example:
            DoclingAdapter(None, {"do_ocr": True}).cache_identity()
        """
        identity: dict[str, Any] = {"options": dict(self.options)}
        if self.variant is not None:
            identity["variant"] = self.variant
        return identity

    def fetch(self, path: Path) -> RawResult:
        """Convert ``path`` with ``DocumentConverter`` and return the JSON export.

        Example:
            raw = DoclingAdapter(None, {}).fetch(Path("files/sample.pdf"))
        """
        if not path.is_file():
            raise AdapterError(f"docling cannot read {path}: path not found")
        converter_cls, version = _load_docling()
        started = time.perf_counter()
        try:
            converted = converter_cls().convert(str(path))
        except AdapterError:
            raise
        except Exception as err:
            raise AdapterError(f"docling failed on {path}: {err}") from err
        elapsed = (time.perf_counter() - started) * 1000
        status = getattr(converted, "status", None)
        status_name = getattr(status, "name", None)
        label = status_name if isinstance(status_name, str) else str(status or "")
        if label.upper() == "FAILURE":
            raise AdapterError(f"docling failed on {path}: {_error_text(converted)}")
        document = getattr(converted, "document", None)
        exporter = getattr(document, "export_to_dict", None)
        if not callable(exporter):
            raise AdapterError(
                f"docling result for {path} has no export_to_dict(). " "Upgrade the docling extra."
            )
        payload = exporter()
        if not isinstance(payload, dict):
            raise AdapterError(f"docling export for {path} was not a JSON object")
        return RawResult(payload=payload, parser_version=version, latency_ms=elapsed, cost_usd=0)

    def to_document(self, raw: RawResult, source: Source) -> Document:
        """Map a DoclingDocument export. This method does not call Docling.

        Example:
            document = adapter.to_document(raw, source)
        """
        return docling_to_document(raw.payload, source, raw.parser_version)


def docling_to_document(payload: object, source: Source, parser_version: str) -> Document:
    """Map one DoclingDocument JSON object to a Document.

    Reading order is the ``body`` tree, then ``furniture`` items that were not
    already visited. Table captions are emitted just before their table. Merged
    cells are repeated across every cell they span. Bottom-left boxes are
    flipped using the page height; 0–1 boxes are scaled by the page size.

    Example:
        document = docling_to_document({"schema_name": "DoclingDocument"}, source, "2.87.0")
    """
    if not isinstance(payload, dict) or payload.get("schema_name") != "DoclingDocument":
        raise AdapterError(
            "docling payload must be a DoclingDocument JSON object "
            '(schema_name "DoclingDocument")'
        )
    if not parser_version:
        raise AdapterError("docling parserVersion is empty. Record the Docling version that ran.")
    pages = _pages(payload)
    state = _State(pages)
    _walk_children(payload, _record(payload.get("body")).get("children"), state)
    _walk_children(payload, _record(payload.get("furniture")).get("children"), state)
    for number in state.used_pages:
        pages.setdefault(number, (None, None))
    ordered = [
        Page(number=number, width=size[0], height=size[1]) for number, size in sorted(pages.items())
    ]
    return Document(
        spec_version="1.0.0",
        source=source,
        meta=Meta(
            parser="docling",
            parser_version=parser_version,
            adapter_version=package_version(),
        ),
        pages=ordered,
        blocks=state.blocks,
        tables=state.tables,
    )


class _State:
    def __init__(self, pages: dict[int, tuple[float | None, float | None]]) -> None:
        self.pages = pages
        self.blocks: list[Block] = []
        self.tables: list[Table] = []
        self.seen: set[str] = set()
        self.used_pages: set[int] = set()


def _load_docling() -> tuple[Any, str]:
    try:
        docling = importlib.import_module("docling")
        module = importlib.import_module("docling.document_converter")
    except ImportError as err:
        raise AdapterError(INSTALL) from err
    converter = getattr(module, "DocumentConverter", None)
    if converter is None:
        raise AdapterError(
            "docling is installed but DocumentConverter is missing. "
            "Reinstall with: pip install 'docassay[docling]'"
        )
    version = getattr(docling, "__version__", None)
    if not isinstance(version, str) or not version:
        version = "unknown"
    return converter, version


def _error_text(converted: object) -> str:
    errors = getattr(converted, "errors", None)
    if isinstance(errors, list) and errors:
        return "; ".join(str(item) for item in errors)
    return "conversion failed"


def _walk_children(payload: dict[str, Any], children: object, state: _State) -> None:
    if not isinstance(children, list):
        return
    for child in children:
        _walk(payload, _ref_of(child), state)


def _walk(payload: dict[str, Any], ref: str | None, state: _State) -> None:
    if ref is None or ref in state.seen:
        return
    pointer = _pointer(ref)
    if pointer is None:
        return
    kind, index = pointer
    item = _item(payload, kind, index)
    if item is None:
        return
    state.seen.add(ref)
    if kind == "groups":
        _walk_children(payload, item.get("children"), state)
        return
    if kind == "tables":
        _walk_children(payload, item.get("captions"), state)
        state.tables.append(_table(item, len(state.tables) + 1, state))
        _walk_children(payload, item.get("children"), state)
        _walk_children(payload, item.get("footnotes"), state)
        return
    if kind in {"pictures", "key_value_items"}:
        _walk_children(payload, item.get("children"), state)
        _emit_text(item, state)
        return
    _emit_text(item, state)


def _emit_text(item: dict[str, Any], state: _State) -> None:
    text = item.get("text")
    if not isinstance(text, str) or text == "":
        return
    label = item.get("label")
    block_type = _LABELS.get(label, Type.other) if isinstance(label, str) else Type.other
    page, bbox = _placement(item, state)
    level: int | None = None
    if block_type is Type.heading:
        raw_level = item.get("level")
        level = raw_level if isinstance(raw_level, int) and raw_level >= 1 else 1
    state.blocks.append(
        Block(
            id=f"b{len(state.blocks) + 1}",
            type=block_type,
            text=text,
            page=page,
            level=level,
            bbox=bbox,
        )
    )


def _table(item: dict[str, Any], number: int, state: _State) -> Table:
    page, bbox = _placement(item, state)
    header, rows = _table_rows(_record(item.get("data")))
    return Table(id=f"t{number}", page=page, bbox=bbox, header=header, rows=rows)


def _table_rows(data: dict[str, Any]) -> tuple[list[str] | None, list[list[str]]]:
    grid = _grid(data)
    width = max((len(row) for row in grid), default=0)
    header_count = 0
    for row in grid:
        if row and all(cell.get("column_header") is True for cell in row):
            header_count += 1
        else:
            break
    header: list[str] | None = None
    if header_count:
        header = []
        for col in range(width):
            parts: list[str] = []
            for row in grid[:header_count]:
                text = _cell_text(row, col)
                if text and (not parts or parts[-1] != text):
                    parts.append(text)
            header.append(" ".join(parts))
    rows = [[_cell_text(row, col) for col in range(width)] for row in grid[header_count:]]
    return header, rows


def _grid(data: dict[str, Any]) -> list[list[dict[str, Any]]]:
    raw = data.get("grid")
    if isinstance(raw, list) and raw and all(isinstance(row, list) for row in raw):
        return [[cell if isinstance(cell, dict) else {"text": ""} for cell in row] for row in raw]
    cells = data.get("table_cells")
    if not isinstance(cells, list):
        return []
    row_count = _index(data.get("num_rows")) or 0
    col_count = _index(data.get("num_cols")) or 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        end_row = _index(cell.get("end_row_offset_idx"))
        end_col = _index(cell.get("end_col_offset_idx"))
        if end_row is not None:
            row_count = max(row_count, end_row)
        if end_col is not None:
            col_count = max(col_count, end_col)
    blank: dict[str, Any] = {"text": "", "column_header": False}
    grid = [[dict(blank) for _ in range(col_count)] for _ in range(row_count)]
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        start_row = _index(cell.get("start_row_offset_idx"))
        end_row = _index(cell.get("end_row_offset_idx"))
        start_col = _index(cell.get("start_col_offset_idx"))
        end_col = _index(cell.get("end_col_offset_idx"))
        if None in (start_row, end_row, start_col, end_col):
            continue
        assert start_row is not None and end_row is not None
        assert start_col is not None and end_col is not None
        text = cell.get("text") if isinstance(cell.get("text"), str) else ""
        header = cell.get("column_header") is True
        for row in range(start_row, end_row):
            for col in range(start_col, end_col):
                if row < row_count and col < col_count:
                    grid[row][col] = {"text": text, "column_header": header}
    return grid


def _cell_text(row: list[dict[str, Any]], col: int) -> str:
    if col >= len(row):
        return ""
    text = row[col].get("text")
    return text if isinstance(text, str) else ""


def _placement(item: dict[str, Any], state: _State) -> tuple[int, BBox | None]:
    prov = item.get("prov")
    first = prov[0] if isinstance(prov, list) and prov and isinstance(prov[0], dict) else {}
    page_no = first.get("page_no")
    page = page_no if isinstance(page_no, int) and page_no >= 1 else 1
    state.used_pages.add(page)
    width, height = state.pages.get(page, (None, None))
    box = first.get("bbox")
    return page, _bbox(box if isinstance(box, dict) else None, width, height)


def _bbox(
    box: dict[str, Any] | None,
    width: float | None,
    height: float | None,
) -> BBox | None:
    if box is None:
        return None
    left, top, right, bottom = (_number(box.get(name)) for name in ("l", "t", "r", "b"))
    if None in (left, top, right, bottom):
        return None
    assert left is not None and top is not None and right is not None and bottom is not None
    origin = box.get("coord_origin")
    if (
        width is not None
        and height is not None
        and width > 1
        and height > 1
        and max(abs(left), abs(top), abs(right), abs(bottom)) <= 1
    ):
        left, right = left * width, right * width
        top, bottom = top * height, bottom * height
    if origin == "BOTTOMLEFT":
        if height is None or height <= 0:
            return None
        top, bottom = height - top, height - bottom
    x0, x1 = (left, right) if left <= right else (right, left)
    y0, y1 = (top, bottom) if top <= bottom else (bottom, top)
    return BBox(root=[_round4(x0), _round4(y0), _round4(x1), _round4(y1)])


def _pages(payload: dict[str, Any]) -> dict[int, tuple[float | None, float | None]]:
    raw = payload.get("pages")
    found: dict[int, tuple[float | None, float | None]] = {}
    items: list[tuple[object, object]] = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(None, item) for item in raw]
    for key, value in items:
        if not isinstance(value, dict):
            continue
        number = value.get("page_no")
        if not isinstance(number, int) or number < 1:
            number = _digits(key)
        if number is None:
            continue
        size = _record(value.get("size"))
        found[number] = (_positive(size.get("width")), _positive(size.get("height")))
    return found


def _item(payload: dict[str, Any], kind: str, index: int) -> dict[str, Any] | None:
    if kind not in _COLLECTIONS:
        return None
    collection = payload.get(kind)
    if not isinstance(collection, list) or index < 0 or index >= len(collection):
        return None
    item = collection[index]
    return item if isinstance(item, dict) else None


def _pointer(ref: str) -> tuple[str, int] | None:
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    if len(parts) != 2:
        return None
    kind, index = parts
    if not index or any(char not in "0123456789" for char in index):
        return None
    return kind, int(index)


def _ref_of(node: object) -> str | None:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            return ref
    return None


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _round4(value: float) -> float:
    """Half-up to 4 decimal places so both languages emit the same points."""
    return math.floor(value * 10000 + 0.5) / 10000


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _positive(value: object) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _index(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _digits(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    if isinstance(value, str) and value and all(char in "0123456789" for char in value):
        number = int(value)
        return number if number >= 1 else None
    return None
