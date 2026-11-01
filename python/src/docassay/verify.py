"""Check extracted values against the spans they cite.

Example:
    from docassay import verify

    verify(doc, {"total": {"value": 4120.0, "citations": [{"page": 2, "blockId": "t1"}]}})
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Literal, TypeGuard

from docassay.normalize.dates import parse_date
from docassay.normalize.numbers import parse_number
from docassay.normalize.text import norm_text

Status = Literal["verified", "value_not_found", "citation_invalid", "uncited"]

_UNCITED = "field has no citations. Add a citation with a page and a blockId, bbox, or page."
_NOT_FOUND = "value not found in span"
_NULL_VALUE = "value is null. Pass a string, number, or boolean."
_BAD_VALUE = "value must be a string, number, boolean, or null."
_BAD_FIELDS = "fields must be an object of field names."
_BAD_FIELD = "field must be an object with a value."
_BAD_CITATIONS = "citations must be a list."
_BAD_DOC = "document must be an object with pages, blocks, and tables."
_NEED_PAGE = "citation needs a page number. Set page to an integer >= 1."
_BAD_BBOX = "bbox must be four numbers [x0, y0, x1, y1]."
_BAD_BLOCK_ID = "blockId must be a non-empty string."
_BAD_READ = "document or fields could not be read. Pass a document object and a map of fields."

# Compiled once. [0-9] keeps Python and TypeScript on the same digits.
# The date pattern is every form parse_date accepts; each hit is confirmed with parse_date.
_FULL_MONTHS = "january|february|march|april|june|july|august|september|october|november|december"
_ABBR_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_MONTH = f"(?:{_FULL_MONTHS}|(?:{_ABBR_MONTHS})\\.?)"
_DATE_RE = re.compile(
    rf"{_MONTH} [0-9]{{1,2}},? [0-9]{{4}}"
    rf"|[0-9]{{1,2}} {_MONTH} [0-9]{{4}}"
    rf"|[0-9]{{4}}[-/][0-9]{{1,2}}[-/][0-9]{{1,2}}"
    rf"|[0-9]{{1,2}}[-/.][0-9]{{1,2}}[-/.][0-9]{{4}}"
)
_NUMBER_RE = re.compile(r"[\(-]?[$€£]?[0-9][0-9.,' ]*[0-9]?%?\)?")

_MISSING = object()


class FieldCheck:
    """One field after `verify`.

    Example:
        FieldCheck("total", "value_not_found", reason="value not found in span")
    """

    def __init__(
        self,
        name: str,
        status: Status,
        *,
        weak: bool = False,
        citation: dict[str, object] | None = None,
        reason: str | None = None,
    ) -> None:
        self.name = name
        self.status = status
        self.weak = weak
        self.citation = citation
        self.reason = reason

    def to_json(self) -> dict[str, object]:
        """Return the check in the shared fixture shape.

        Example:
            FieldCheck("note", "uncited").to_json()
        """
        payload: dict[str, object] = {"name": self.name, "status": self.status}
        if self.weak:
            payload["weak"] = True
        if self.citation is not None:
            payload["citation"] = self.citation
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


class VerifyResult:
    """Checks for every field. `ok` is true when each one is verified.

    Example:
        result = verify(doc, fields)
        result.unverified
    """

    def __init__(self, ok: bool, checks: list[FieldCheck], unverified: list[FieldCheck]) -> None:
        self.ok = ok
        self.checks = checks
        self.unverified = unverified


class _Piece:
    def __init__(
        self,
        kind: str,
        ident: str,
        page: int,
        text: str,
        bbox: tuple[float, float, float, float] | None,
    ) -> None:
        self.kind = kind
        self.ident = ident
        self.page = page
        self.text = text
        self.bbox = bbox


class _Index:
    def __init__(self, doc: object) -> None:
        self.blocks: list[_Piece] = []
        self.tables: list[_Piece] = []
        self.by_id: dict[str, _Piece] = {}
        self._page_text: dict[int, str] = {}
        explicit = _explicit_pages(doc)
        self._read_blocks(doc)
        self._read_tables(doc)
        if explicit is None:
            self.pages = {piece.page for piece in self.blocks}
            self.pages.update(piece.page for piece in self.tables)
        else:
            self.pages = explicit

    def _read_blocks(self, doc: object) -> None:
        for item in _sequence(_attr(doc, "blocks")):
            piece = _piece("block", item)
            if piece is None:
                continue
            self.blocks.append(piece)
            if piece.ident not in self.by_id:
                self.by_id[piece.ident] = piece

    def _read_tables(self, doc: object) -> None:
        for item in _sequence(_attr(doc, "tables")):
            piece = _piece("table", item)
            if piece is None:
                continue
            self.tables.append(piece)
            if piece.ident not in self.by_id:
                self.by_id[piece.ident] = piece

    def page_text(self, page: int) -> str:
        cached = self._page_text.get(page)
        if cached is not None:
            return cached
        parts = [piece.text for piece in self.blocks if piece.page == page]
        parts.extend(piece.text for piece in self.tables if piece.page == page)
        text = " ".join(parts)
        self._page_text[page] = text
        return text


class _View:
    def __init__(self, kind: str, number: float | None, date: str | None, text: str | None) -> None:
        self.kind = kind
        self.number = number
        self.date = date
        self.text = text


class _Hit:
    def __init__(
        self,
        status: Status,
        citation: dict[str, object] | None,
        reason: str | None,
        page_only: bool,
    ) -> None:
        self.status = status
        self.citation = citation
        self.reason = reason
        self.page_only = page_only


def verify(doc: object, fields: object) -> VerifyResult:
    """Check each field against the document span its citations name.

    A field is verified when any citation's span contains the value. Numbers
    match `parse_number` tokens, dates match `parse_date` substrings, booleans
    are trusted once the span exists, and strings use `norm_text`. Malformed
    input returns `citation_invalid` instead of raising.

    Example:
        verify(doc, {"total": {"value": 4120.0, "citations": [{"page": 1, "blockId": "b1"}]}})
    """
    try:
        return _verify(doc, fields)
    except Exception:
        return _fail("", "citation_invalid", _BAD_READ)


def _verify(doc: object, fields: object) -> VerifyResult:
    if not _is_document(doc):
        return _fail("", "citation_invalid", _BAD_DOC)
    items = _field_items(fields)
    if items is None:
        return _fail("", "citation_invalid", _BAD_FIELDS)
    index = _Index(doc)
    checks = [_field(index, name, raw) for name, raw in items]
    unverified = [check for check in checks if check.status != "verified"]
    return VerifyResult(not unverified, checks, unverified)


def _fail(name: str, status: Status, reason: str) -> VerifyResult:
    check = FieldCheck(name, status, reason=reason)
    return VerifyResult(False, [check], [check])


def _field(index: _Index, name: str, raw: object) -> FieldCheck:
    field = _as_map(raw)
    if field is None or "value" not in field:
        return FieldCheck(name, "citation_invalid", reason=_BAD_FIELD)
    view = _classify(field["value"])
    citations = field.get("citations", _MISSING)
    if (
        citations is _MISSING
        or citations is None
        or (isinstance(citations, list | tuple) and len(citations) == 0)
    ):
        return FieldCheck(name, "uncited", reason=_UNCITED)
    if not isinstance(citations, (list, tuple)):
        return FieldCheck(name, "citation_invalid", reason=_BAD_CITATIONS)
    first: _Hit | None = None
    searched = False
    for citation in citations:
        hit = _citation(index, view, citation)
        if first is None:
            first = hit
        if hit.status == "verified":
            return _from_hit(name, hit, view, passed=True)
        if hit.status == "value_not_found":
            searched = True
    if first is None:
        return FieldCheck(name, "uncited", reason=_UNCITED)
    if searched:
        return _from_hit(name, first, view, passed=False)
    return FieldCheck(name, "citation_invalid", citation=first.citation, reason=first.reason)


def _from_hit(name: str, hit: _Hit, view: _View, *, passed: bool) -> FieldCheck:
    status: Status = "verified" if passed else "value_not_found"
    if passed:
        reason = None
    elif view.kind == "null":
        reason = _NULL_VALUE
    else:
        reason = _NOT_FOUND
    weak = (view.kind == "bool" and passed) or hit.page_only
    return FieldCheck(name, status, weak=weak, citation=hit.citation, reason=reason)


def _citation(index: _Index, view: _View, citation: object) -> _Hit:
    cited = _as_map(citation)
    if cited is None:
        return _Hit("citation_invalid", None, _NEED_PAGE, False)
    page = _as_page(cited.get("page"))
    if page is None:
        return _Hit("citation_invalid", None, _NEED_PAGE, False)
    block_id = _block_id(cited)
    if block_id is False:
        return _Hit("citation_invalid", _echo(page, None, None), _BAD_BLOCK_ID, False)
    if isinstance(block_id, str):
        return _by_id(index, view, page, block_id)
    bbox = _bbox_arg(cited)
    if bbox is False:
        return _Hit("citation_invalid", _echo(page, None, None), _BAD_BBOX, False)
    if isinstance(bbox, tuple):
        return _by_bbox(index, view, page, bbox)
    if page not in index.pages:
        return _Hit("citation_invalid", _echo(page, None, None), _missing_page(page), True)
    return _search(view, index.page_text(page), _echo(page, None, None), page_only=True)


def _by_id(index: _Index, view: _View, page: int, block_id: str) -> _Hit:
    echo = _echo(page, block_id, None)
    piece = index.by_id.get(block_id)
    if piece is None:
        return _Hit("citation_invalid", echo, _missing_id(block_id), False)
    if piece.page != page:
        reason = _wrong_page(piece.kind, block_id, piece.page, page)
        return _Hit("citation_invalid", echo, reason, False)
    return _search(view, piece.text, echo, page_only=False)


def _by_bbox(
    index: _Index,
    view: _View,
    page: int,
    bbox: tuple[float, float, float, float],
) -> _Hit:
    echo = _echo(page, None, bbox)
    if page not in index.pages:
        return _Hit("citation_invalid", echo, _missing_page(page), False)
    parts: list[str] = []
    for piece in index.blocks:
        if piece.page == page and piece.bbox is not None and _overlaps(piece.bbox, bbox):
            parts.append(piece.text)
    for piece in index.tables:
        if piece.page == page and piece.bbox is not None and _overlaps(piece.bbox, bbox):
            parts.append(piece.text)
    return _search(view, " ".join(parts), echo, page_only=False)


def _search(view: _View, span: str, citation: dict[str, object], *, page_only: bool) -> _Hit:
    if view.kind == "bad":
        return _Hit("citation_invalid", citation, _BAD_VALUE, page_only)
    if view.kind == "null":
        return _Hit("value_not_found", citation, _NULL_VALUE, page_only)
    if view.kind == "bool" or _in_span(view, span):
        return _Hit("verified", citation, None, page_only)
    return _Hit("value_not_found", citation, _NOT_FOUND, page_only)


def _in_span(view: _View, span: str) -> bool:
    if view.kind == "number" and view.number is not None:
        return _span_has_number(span, view.number)
    if view.kind == "date" and view.date is not None:
        return _span_has_date(span, view.date)
    if view.kind == "string" and view.text is not None:
        return norm_text(view.text) in norm_text(span)
    return False


def _span_has_number(span: str, expected: float) -> bool:
    if not math.isfinite(expected):
        return False
    for match in _NUMBER_RE.finditer(span):
        parsed = parse_number(match.group(0))
        if parsed is not None and abs(expected - parsed) <= 1e-6 * max(1.0, abs(parsed)):
            return True
    return False


def _span_has_date(span: str, target: str) -> bool:
    for match in _DATE_RE.finditer(norm_text(span)):
        if parse_date(match.group(0)) == target:
            return True
    return False


def _classify(value: object) -> _View:
    if isinstance(value, bool):
        return _View("bool", None, None, None)
    if isinstance(value, int | float):
        return _View("number", float(value), None, None)
    if isinstance(value, str):
        dated = parse_date(value)
        if dated is not None:
            return _View("date", None, dated, None)
        number = parse_number(value)
        if number is not None:
            return _View("number", number, None, None)
        return _View("string", None, None, value)
    if value is None:
        return _View("null", None, None, None)
    return _View("bad", None, None, None)


def _as_map(value: object) -> Mapping[str, object] | None:
    if _is_str_map(value):
        return value
    return None


def _is_str_map(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping)


def _is_document(doc: object) -> bool:
    if doc is None or isinstance(doc, str | bytes | int | float | bool | list | tuple):
        return False
    return isinstance(doc, Mapping) or any(
        getattr(doc, name, _MISSING) is not _MISSING for name in ("pages", "blocks", "tables")
    )


def _field_items(fields: object) -> list[tuple[str, object]] | None:
    if not isinstance(fields, Mapping):
        return None
    return [(key if isinstance(key, str) else str(key), value) for key, value in fields.items()]


def _explicit_pages(doc: object) -> set[int] | None:
    raw = _attr(doc, "pages")
    if raw is _MISSING:
        return None
    found: set[int] = set()
    for item in _sequence(raw):
        if isinstance(item, Mapping) or not isinstance(
            item, str | int | float | bool | list | tuple
        ):
            number = _attr(item, "number")
        else:
            number = item
        page = _as_page(number)
        if page is not None:
            found.add(page)
    return found


def _piece(kind: str, item: object) -> _Piece | None:
    ident = _attr(item, "id")
    page = _as_page(_attr(item, "page"))
    if not isinstance(ident, str) or ident == "" or page is None:
        return None
    if kind == "table":
        text = _table_text(item)
    else:
        raw_text = _attr(item, "text")
        text = raw_text if isinstance(raw_text, str) else ""
    bbox = _box(_attr(item, "bbox"))
    return _Piece(kind, ident, page, text, bbox)


def _table_text(table: object) -> str:
    cells: list[str] = []
    header = _attr(table, "header")
    if isinstance(header, list | tuple):
        cells.extend(cell if isinstance(cell, str) else "" for cell in header)
    rows = _attr(table, "rows")
    if isinstance(rows, list | tuple):
        for row in rows:
            if isinstance(row, list | tuple):
                cells.extend(cell if isinstance(cell, str) else "" for cell in row)
    return " ".join(cells)


def _sequence(value: object) -> list[object]:
    if isinstance(value, list | tuple):
        return list(value)
    return []


def _attr(obj: object, name: str) -> object:
    if isinstance(obj, Mapping):
        if name not in obj:
            return _MISSING
        value = obj[name]
    else:
        value = getattr(obj, name, _MISSING)
    if value is None:
        return _MISSING
    return value


def _as_page(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value) if value >= 1 else None
    return None


def _block_id(citation: Mapping[str, object]) -> str | None | bool:
    present, raw = _provided(citation, "blockId", "block_id")
    if not present or raw is None:
        return None
    if isinstance(raw, str) and raw != "":
        return raw
    return False


def _bbox_arg(
    citation: Mapping[str, object],
) -> tuple[float, float, float, float] | None | bool:
    present, raw = _provided(citation, "bbox")
    if not present or raw is None:
        return None
    box = _box(raw)
    if box is None:
        return False
    return box


def _provided(mapping: Mapping[str, object], *keys: str) -> tuple[bool, object]:
    for key in keys:
        if key in mapping:
            return True, mapping[key]
    return False, None


def _box(value: object) -> tuple[float, float, float, float] | None:
    if value is _MISSING or value is None:
        return None
    root = getattr(value, "root", _MISSING)
    raw = value if root is _MISSING else root
    if isinstance(raw, str | bytes) or not isinstance(raw, list | tuple) or len(raw) != 4:
        return None
    nums: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        nums.append(number)
    return (nums[0], nums[1], nums[2], nums[3])


def _overlaps(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return min(left[2], right[2]) > max(left[0], right[0]) and min(left[3], right[3]) > max(
        left[1], right[1]
    )


def _echo(
    page: int,
    block_id: str | None,
    bbox: tuple[float, float, float, float] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {"page": page}
    if block_id is not None:
        payload["blockId"] = block_id
    if bbox is not None:
        payload["bbox"] = [_json_number(number) for number in bbox]
    return payload


def _json_number(number: float) -> int | float:
    if number.is_integer() and abs(number) <= 2**53:
        return int(number)
    return number


def _missing_page(page: int) -> str:
    return f"page {page} does not exist. Cite a page listed on the document."


def _missing_id(block_id: str) -> str:
    return f"no block or table with id {block_id}. Cite an id from the document."


def _wrong_page(kind: str, ident: str, actual: int, cited: int) -> str:
    return f"{kind} {ident} is on page {actual}, citation says page {cited}. Use the {kind}'s page."
