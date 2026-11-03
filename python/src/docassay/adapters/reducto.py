"""Reducto adapter.

Both languages call the Reducto REST API (upload, parse, optional extract).
Mapping is pure. ``parserVersion`` is the model id, default ``r-1``.

Example:
    from docassay.adapters.reducto import ReductoAdapter

    adapter = ReductoAdapter(None, {"model": "r-1"})
    ok, reason = adapter.available()
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, ClassVar, TypeGuard
from urllib.parse import quote

import httpx

from docassay.adapters.base import AdapterError, RawResult
from docassay.adapters.runner_support import package_version
from docassay.models.document import (
    BBox,
    Block,
    BlockId,
    Chunk,
    Citation,
    Document,
    FieldModel,
    HeadingPathItem,
    Meta,
    Page,
    Page1,
    Source,
    Table,
    Type,
)

# List prices effective 2026-09-01.
# Source: https://docs.reducto.ai/reference/credit-usage
# Read 2026-11-02. ``usage.credits`` is not dollars.
# Legacy complex pages are $30/1,000, but the response does not mark them,
# so legacy parse uses the standard-page rate of $15/1,000.
PARSE_USD_PER_THOUSAND: dict[str, float] = {"r-1": 10.0, "legacy": 15.0}
EXTRACT_USD_PER_THOUSAND = 20.0
DEFAULT_MODEL = "r-1"

API_KEY = "REDUCTO_API_KEY is not set. Create a key at https://studio.reducto.ai and export it."
_DEFAULT_BASE = "https://platform.reducto.ai"
_RESERVED = {
    "apiKey",
    "api_key",
    "baseUrl",
    "base_url",
    "extractSchema",
    "extract_schema",
    "model",
    "pageSize",
    "page_size",
    "pages",
    "timeoutS",
    "timeout_s",
}
_SPACE = " \t\r\n\u00a0"
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}
_DONE = {"COMPLETED"}
_FAIL = {"FAILED", "CANCELLED", "CANCELED"}


class ReductoAdapter:
    """Upload a file to Reducto and map parse (and optional extract) to a Document.

    ``options.model`` defaults to ``r-1`` and is sent as ``settings.model``.
    ``options.extract_schema`` is a path to a JSON Schema. When it is set, fetch
    also calls ``/extract`` and mapping fills ``fields``.

    Example:
        ReductoAdapter(None, {}).cache_identity()
    """

    id: ClassVar[str] = "reducto"
    variants: ClassVar[tuple[str, ...]] = ()

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None:
        self.variant = variant
        self.options = options

    def available(self) -> tuple[bool, str]:
        """Return whether ``REDUCTO_API_KEY`` is set.

        Example:
            ok, reason = ReductoAdapter(None, {}).available()
        """
        if not _api_key():
            return False, API_KEY
        return True, ""

    def cache_identity(self) -> dict[str, Any]:
        """Return the model, schema path, page sizes, and options that change output.

        The API key, base URL, and timeout are omitted. The schema path is the
        path string, not a hash of the file. Edit the schema and pass ``--refresh``.

        Example:
            ReductoAdapter(None, {"model": "legacy"}).cache_identity()
        """
        return {
            "model": _model(self.options),
            "extract_schema": _schema_option(self.options),
            "page_size": self.options.get("page_size", self.options.get("pageSize")),
            "pages": self.options.get("pages"),
            "options": _public_options(self.options),
        }

    def fetch(self, path: Path) -> RawResult:
        """Upload ``path``, parse it, and optionally extract fields.

        Example:
            raw = ReductoAdapter(None, {}).fetch(Path("files/sample.pdf"))
        """
        if self.variant is not None:
            raise AdapterError('reducto has no variants. Use the id "reducto".')
        if not path.is_file():
            raise AdapterError(f"reducto cannot read {path}: path not found")
        key = _api_key()
        if not key:
            raise AdapterError(API_KEY)
        timeout_s = _timeout_s(self.options)
        model = _model(self.options)
        base = _base_url(self.options)
        schema = _load_schema(self.options, path)
        started = time.perf_counter()
        file_id = _upload(path, key, base)
        parsed = _parse(path, key, base, file_id, self.options, model, timeout_s)
        extracted = None
        if schema is not None:
            job_id = _job_id(parsed)
            if not job_id:
                raise AdapterError(
                    f"reducto parse for {path} did not return a job_id, so extract cannot run"
                )
            extracted = _extract(path, key, base, job_id, schema, timeout_s)
        elapsed = (time.perf_counter() - started) * 1000
        payload: dict[str, Any] = {"parse": parsed, "pages": _page_specs(self.options)}
        if extracted is not None:
            payload["extract"] = extracted
        return RawResult(
            payload=payload,
            parser_version=_reported_model(parsed, model),
            latency_ms=elapsed,
            cost_usd=reducto_cost_usd(model, _billed_pages(parsed), extracted is not None),
        )

    def to_document(self, raw: RawResult, source: Source) -> Document:
        """Map a recorded Reducto result. This method does not call the API.

        Example:
            document = adapter.to_document(raw, source)
        """
        return reducto_to_document(raw.payload, source, raw.parser_version)


def reducto_cost_usd(model: str, page_count: int, extracted: bool) -> float:
    """Return USD for ``page_count`` pages.

    ``r-1`` parse is $10 per 1,000 pages. Legacy parse is $15 per 1,000 pages.
    Extract adds $20 per 1,000 pages. An unknown model has no parse price.
    ``usage.credits`` is not a dollar amount.

    Example:
        reducto_cost_usd("r-1", 1, False)
    """
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        return 0.0
    per_thousand = PARSE_USD_PER_THOUSAND.get(model, 0.0)
    if extracted:
        per_thousand += EXTRACT_USD_PER_THOUSAND
    return page_count * per_thousand / 1000


def reducto_to_document(payload: object, source: Source, parser_version: str) -> Document:
    """Map one Reducto parse response, plus an optional extract response, to a Document.

    Boxes are normalized 0–1 ``{left, top, width, height}``. They are scaled by
    the page size in the payload's ``pages`` list (or a default ``{width, height}``
    entry) and omitted when that size is missing. The first table row is the
    header. HTML ``colspan`` and ``rowspan`` repeat the cell text. Extract
    values become ``fields`` with citations.

    Example:
        document = reducto_to_document({"result": {"chunks": []}}, source, "r-1")
    """
    if not isinstance(payload, dict):
        raise AdapterError("reducto payload must be a JSON object from POST /parse")
    if not parser_version:
        raise AdapterError("reducto parserVersion is empty. Record the model id, for example r-1.")
    parsed, extracted, page_spec = _split_payload(payload)
    result = parsed.get("result")
    if isinstance(result, dict) and result.get("type") == "url":
        raise AdapterError("reducto result is a URL. Fetch result.url before mapping.")
    chunks_raw = _chunks(result)
    if chunks_raw is None:
        raise AdapterError(
            "reducto payload needs result.chunks. Fetch the parse result before mapping."
        )
    explicit, default_size = _page_table(page_spec)
    state = _State()
    headings: list[str] = []
    for chunk in chunks_raw:
        if not isinstance(chunk, dict):
            continue
        headings = _one_chunk(chunk, state, headings, explicit, default_size)
    for number in state.used_pages:
        explicit.setdefault(number, default_size)
    ordered = [
        Page(number=number, width=size[0], height=size[1])
        for number, size in sorted(explicit.items())
    ]
    fields = None
    if extracted is not None:
        fields = _fields(_extract_result(extracted), explicit, default_size, state.emitted)
    return Document(
        spec_version="1.0.0",
        source=source,
        meta=Meta(
            parser="reducto", parser_version=parser_version, adapter_version=package_version()
        ),
        pages=ordered,
        blocks=state.blocks,
        tables=state.tables,
        chunks=state.chunks,
        fields=fields,
    )


class _State:
    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.tables: list[Table] = []
        self.chunks: list[Chunk] = []
        self.emitted: list[_Hit] = []
        self.used_pages: set[int] = set()


class _Hit:
    def __init__(
        self,
        item_id: str,
        page: int,
        text: str,
        raw: tuple[float, float, float, float] | None,
    ) -> None:
        self.id = item_id
        self.page = page
        self.text = text
        self.raw = raw


def _one_chunk(
    chunk: dict[str, Any],
    state: _State,
    headings: list[str],
    explicit: dict[int, tuple[float | None, float | None]],
    default_size: tuple[float | None, float | None],
) -> list[str]:
    content = chunk.get("content")
    text = content if isinstance(content, str) else ""
    ids: list[str] = []
    pages: set[int] = set()
    stack = list(headings)
    blocks = chunk.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            emitted = _emit(block, state, explicit, default_size)
            if emitted is None:
                continue
            item_id, page, kind, block_text = emitted
            ids.append(item_id)
            pages.add(page)
            if kind in {"title", "section header"}:
                stack = _apply_heading(stack, kind, block_text)
    state.chunks.append(
        Chunk(
            id=f"c{len(state.chunks) + 1}",
            text=text,
            block_ids=[BlockId(root=item) for item in ids],
            pages=[Page1(root=number) for number in sorted(pages)],
            heading_path=[HeadingPathItem(root=item) for item in stack] if stack else None,
        )
    )
    return stack


def _emit(
    block: dict[str, Any],
    state: _State,
    explicit: dict[int, tuple[float | None, float | None]],
    default_size: tuple[float | None, float | None],
) -> tuple[str, int, str, str] | None:
    content = block.get("content")
    text = content if isinstance(content, str) else ""
    if not _trim(text):
        return None
    raw_box = block.get("bbox")
    page = _page_of(raw_box)
    width, height = _size_for(page, explicit, default_size)
    box = _bbox(raw_box, width, height)
    raw = _raw_box(raw_box)
    kind = _kind(block.get("type"))
    if kind == "table":
        item_id = f"t{len(state.tables) + 1}"
        header, rows = _table_matrix(text)
        state.tables.append(
            Table(id=item_id, page=page, bbox=box, header=header, rows=rows, markdown=text)
        )
        state.used_pages.add(page)
        state.emitted.append(_Hit(item_id, page, text, raw))
        return item_id, page, kind, text
    block_type, level = _classify(kind, text)
    item_id = f"b{len(state.blocks) + 1}"
    state.blocks.append(
        Block(id=item_id, type=block_type, text=text, page=page, level=level, bbox=box)
    )
    state.used_pages.add(page)
    state.emitted.append(_Hit(item_id, page, text, raw))
    return item_id, page, kind, text


def _apply_heading(stack: list[str], kind: str, text: str) -> list[str]:
    level = 1 if kind == "title" else _hash_level(text)
    return [*stack[: level - 1], text]


def _classify(kind: str, text: str) -> tuple[Type, int | None]:
    if kind == "title":
        return Type.title, None
    if kind == "section header":
        return Type.heading, _hash_level(text)
    if kind == "header":
        return Type.header, None
    if kind == "footer":
        return Type.footer, None
    if kind == "text":
        return Type.paragraph, None
    if kind == "list item":
        return Type.list_item, None
    if kind == "figure":
        return Type.caption, None
    return Type.other, None


def _kind(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(part for part in value.strip().lower().split(" ") if part)


def _table_matrix(content: str) -> tuple[list[str] | None, list[list[str]]]:
    lower = content.lower()
    grid: list[list[str]] | None = None
    if "<tr" in lower:
        grid = _html_grid(content)
    if grid is None:
        grid = _markdown_rows(content)
    if not grid:
        return None, [[content]]
    width = max(len(row) for row in grid)
    padded = [row + [""] * (width - len(row)) for row in grid]
    return padded[0], padded[1:]


def _markdown_rows(content: str) -> list[list[str]] | None:
    rows: list[list[str]] = []
    saw_pipe = False
    for line in content.split("\n"):
        cells = _pipe_cells(line)
        if cells is None:
            continue
        saw_pipe = True
        if _separator(cells):
            continue
        rows.append(cells)
    if not saw_pipe or not rows:
        return None
    return rows


def _pipe_cells(line: str) -> list[str] | None:
    trimmed = _trim(line.replace("\r", ""))
    if "|" not in trimmed:
        return None
    parts = trimmed.split("|")
    if trimmed.startswith("|"):
        parts = parts[1:]
    if trimmed.endswith("|"):
        parts = parts[:-1]
    return [_trim(part) for part in parts]


def _separator(cells: list[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        if "-" not in cell:
            return False
        if any(char not in "-: " for char in cell):
            return False
    return True


def _html_grid(html: str) -> list[list[str]] | None:
    lower = html.lower()
    if "<tr" not in lower:
        return None
    rows: list[list[tuple[str, int, int]]] = []
    index = 0
    length = len(html)
    while index < length:
        start = lower.find("<tr", index)
        if start < 0:
            break
        name_end = start + 3
        if name_end < length and lower[name_end] not in " >/\t\r\n":
            index = name_end
            continue
        close = lower.find("</tr>", name_end)
        if close < 0:
            break
        cells = _html_cells(html[name_end:close], lower[name_end:close])
        if cells:
            rows.append(cells)
        index = close + 5
    if not rows:
        return None
    return _place(rows)


def _html_cells(row: str, lower: str) -> list[tuple[str, int, int]]:
    cells: list[tuple[str, int, int]] = []
    index = 0
    length = len(row)
    while index < length:
        td = lower.find("<td", index)
        th = lower.find("<th", index)
        if td < 0 and th < 0:
            break
        start = th if td < 0 or (th >= 0 and th < td) else td
        name_end = start + 3
        if name_end < length and lower[name_end] not in " >/\t\r\n":
            index = name_end
            continue
        tag = lower[start + 1 : start + 3]
        gt = lower.find(">", name_end)
        if gt < 0:
            break
        colspan = _attr_int(lower[name_end:gt], "colspan")
        rowspan = _attr_int(lower[name_end:gt], "rowspan")
        span_c = 1 if colspan is None or colspan < 1 else min(colspan, 32)
        span_r = 1 if rowspan is None or rowspan < 1 else min(rowspan, 32)
        if lower[gt - 1] == "/":
            cells.append(("", span_c, span_r))
            index = gt + 1
            continue
        close = lower.find(f"</{tag}>", gt + 1)
        if close < 0:
            break
        cells.append((_html_text(row[gt + 1 : close]), span_c, span_r))
        index = close + len(tag) + 3
    return cells


def _attr_int(attrs: str, name: str) -> int | None:
    start = 0
    while start < len(attrs):
        at = attrs.find(name, start)
        if at < 0:
            return None
        if at > 0 and attrs[at - 1] not in " \t\r\n/":
            start = at + len(name)
            continue
        cursor = at + len(name)
        while cursor < len(attrs) and attrs[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= len(attrs) or attrs[cursor] != "=":
            start = at + len(name)
            continue
        cursor += 1
        while cursor < len(attrs) and attrs[cursor] in " \t\r\n":
            cursor += 1
        if cursor < len(attrs) and attrs[cursor] in "\"'":
            quote = attrs[cursor]
            cursor += 1
            end = attrs.find(quote, cursor)
            token = attrs[cursor:] if end < 0 else attrs[cursor:end]
        else:
            end = cursor
            while end < len(attrs) and attrs[end] not in " \t\r\n>/":
                end += 1
            token = attrs[cursor:end]
        if token and all(char in "0123456789" for char in token):
            return int(token)
        start = at + len(name)
    return None


def _html_text(fragment: str) -> str:
    parts: list[str] = []
    index = 0
    length = len(fragment)
    while index < length:
        char = fragment[index]
        if char == "<":
            gt = fragment.find(">", index + 1)
            if gt < 0:
                break
            index = gt + 1
            continue
        if char == "&":
            semi = fragment.find(";", index + 1)
            if semi < 0 or semi - index > 12:
                parts.append("&")
                index += 1
                continue
            parts.append(_entity(fragment[index + 1 : semi]))
            index = semi + 1
            continue
        parts.append(char)
        index += 1
    return "".join(parts)


def _entity(token: str) -> str:
    named = _ENTITIES.get(token)
    if named is not None:
        return named
    if token.startswith("#") and len(token) > 1 and all(char in "0123456789" for char in token[1:]):
        code = int(token[1:])
        if 0 < code < 0x110000:
            return chr(code)
    return f"&{token};"


def _place(rows: list[list[tuple[str, int, int]]]) -> list[list[str]] | None:
    occupied: dict[tuple[int, int], str] = {}
    for row_index, cells in enumerate(rows):
        column = 0
        for text, colspan, rowspan in cells:
            while (row_index, column) in occupied and column <= 64:
                column += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    occupied[(row_index + dr, column + dc)] = text
            column += colspan
    if not occupied:
        return None
    height = max(key[0] for key in occupied) + 1
    width = max(key[1] for key in occupied) + 1
    if height > 64 or width > 64:
        return None
    return [[occupied.get((row, col), "") for col in range(width)] for row in range(height)]


def _fields(
    result: object,
    explicit: dict[int, tuple[float | None, float | None]],
    default_size: tuple[float | None, float | None],
    emitted: list[_Hit],
) -> dict[str, FieldModel]:
    if isinstance(result, list):
        if len(result) == 1:
            return _flatten(result[0], "", explicit, default_size, emitted)
        found: dict[str, FieldModel] = {}
        for index, item in enumerate(result):
            found.update(_flatten(item, str(index), explicit, default_size, emitted))
        return found
    return _flatten(result, "", explicit, default_size, emitted)


def _flatten(
    node: object,
    prefix: str,
    explicit: dict[int, tuple[float | None, float | None]],
    default_size: tuple[float | None, float | None],
    emitted: list[_Hit],
) -> dict[str, FieldModel]:
    if _is_wrapper(node):
        citations = node.get("citations")
        return _place_value(prefix, node.get("value"), citations, explicit, default_size, emitted)
    if isinstance(node, dict):
        found: dict[str, FieldModel] = {}
        for key, item in node.items():
            if not isinstance(key, str) or not key:
                continue
            child = f"{prefix}.{key}" if prefix else key
            found.update(_flatten(item, child, explicit, default_size, emitted))
        return found
    if isinstance(node, list):
        if node and all(_is_plain_object(item) for item in node):
            found = {}
            for index, item in enumerate(node):
                child = f"{prefix}.{index}" if prefix else str(index)
                found.update(_flatten(item, child, explicit, default_size, emitted))
            return found
        values: list[object] = []
        gathered: list[object] = []
        for item in node:
            if _is_wrapper(item):
                values.append(item.get("value"))
                raw = item.get("citations")
                if isinstance(raw, list):
                    gathered.extend(raw)
            else:
                values.append(item)
        return _place_value(prefix, values, gathered, explicit, default_size, emitted)
    return _place_value(prefix, node, None, explicit, default_size, emitted)


def _place_value(
    prefix: str,
    value: object,
    citations: object,
    explicit: dict[int, tuple[float | None, float | None]],
    default_size: tuple[float | None, float | None],
    emitted: list[_Hit],
) -> dict[str, FieldModel]:
    if not prefix:
        return {}
    mapped = _citations(citations, explicit, default_size, emitted)
    return {
        prefix: FieldModel(
            value=_scalar(value),
            confidence=_confidence(citations),
            citations=mapped if mapped else None,
        )
    }


def _citations(
    value: object,
    explicit: dict[int, tuple[float | None, float | None]],
    default_size: tuple[float | None, float | None],
    emitted: list[_Hit],
) -> list[Citation]:
    if not isinstance(value, list):
        return []
    found: list[Citation] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_box = item.get("bbox")
        page = _page_of(raw_box)
        width, height = _size_for(page, explicit, default_size)
        content = item.get("content")
        text = content if isinstance(content, str) and content else None
        found.append(
            Citation(
                page=page,
                block_id=_block_id(item, emitted),
                bbox=_bbox(raw_box, width, height),
                text=text,
            )
        )
    return found


def _block_id(citation: dict[str, Any], emitted: list[_Hit]) -> str | None:
    parent = citation.get("parentBlock")
    parent_box = None
    parent_page = None
    parent_text = None
    if isinstance(parent, dict):
        parent_box = _raw_box(parent.get("bbox"))
        if isinstance(parent.get("bbox"), dict):
            parent_page = _page_of(parent.get("bbox"))
        content = parent.get("content")
        if isinstance(content, str) and content:
            parent_text = content
    if parent_box is not None and parent_page is not None:
        for item in emitted:
            if item.page == parent_page and item.raw is not None and item.raw == parent_box:
                return item.id
    cite_text = citation.get("content")
    texts = [parent_text]
    if isinstance(cite_text, str) and cite_text:
        texts.append(cite_text)
    for text in texts:
        if not text:
            continue
        for item in emitted:
            if item.text == text:
                return item.id
    return None


def _confidence(value: object) -> float | None:
    if not isinstance(value, list):
        return None
    parse_score = None
    for item in value:
        if not isinstance(item, dict):
            continue
        granular = item.get("granular_confidence")
        if not isinstance(granular, dict):
            continue
        extract_score = _unit(granular.get("extract_confidence"))
        if extract_score is not None:
            return extract_score
        if parse_score is None:
            parse_score = _unit(granular.get("parse_confidence"))
    return parse_score


def _is_wrapper(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and "value" in value and "citations" in value


def _is_plain_object(value: object) -> bool:
    return isinstance(value, dict) and not _is_wrapper(value)


def _scalar(value: object) -> str | float | bool | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return _canonical_json(value)


def _canonical_json(value: object) -> str:
    return json.dumps(_jsonable(value), separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, str) or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value == math.floor(value) and abs(value) <= 2**53:
            return int(value)
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _upload(path: Path, key: str, base: str) -> str:
    status, text = _exchange(
        path,
        "POST",
        f"{base}/upload",
        headers={"Authorization": f"Bearer {key}"},
        content=None,
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    body = _check(status, text, path, "uploading")
    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id.strip():
        raise AdapterError(f"reducto upload for {path} did not return a file_id")
    return file_id.strip()


def _parse(
    path: Path,
    key: str,
    base: str,
    file_id: str,
    options: dict[str, Any],
    model: str,
    timeout_s: float,
) -> dict[str, Any]:
    status, text = _exchange(
        path,
        "POST",
        f"{base}/parse",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        content=_parse_body(file_id, options, model),
        files=None,
    )
    body = _check(status, text, path, "parsing")
    return _settle(path, key, base, body, timeout_s, "parse")


def _extract(
    path: Path,
    key: str,
    base: str,
    job_id: str,
    schema: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    request = {
        "input": f"jobid://{job_id}",
        "instructions": {"schema": schema},
        "settings": {"citations": {"enabled": True}},
    }
    status, text = _exchange(
        path,
        "POST",
        f"{base}/extract",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        content=request,
        files=None,
    )
    body = _check(status, text, path, "extracting")
    return _settle(path, key, base, body, timeout_s, "extract")


def _settle(
    path: Path,
    key: str,
    base: str,
    body: dict[str, Any],
    timeout_s: float,
    what: str,
) -> dict[str, Any]:
    label = _status(body)
    if label in _FAIL:
        job_id = _job_id(body) or "unknown"
        raise AdapterError(f"reducto job {job_id} {label} on {path}: {_error_message(body)}")
    if label in _DONE and not _result_ready(body):
        raise AdapterError(f"reducto {what} for {path} did not return a result")
    current = body
    if not _result_ready(current):
        job_id = _job_id(current)
        if not job_id:
            raise AdapterError(f"reducto {what} for {path} did not return a result")
        current = _poll(path, key, base, job_id, timeout_s)
    return _inline_url(path, key, current)


def _poll(path: Path, key: str, base: str, job_id: str, timeout_s: float) -> dict[str, Any]:
    url = f"{base}/job/{quote(job_id, safe='')}"
    headers = {"Authorization": f"Bearer {key}"}
    delay = 1.0
    started = time.monotonic()
    while True:
        status, text = _exchange(path, "GET", url, headers=headers, content=None, files=None)
        body = _check(status, text, path, "polling")
        label = _status(body)
        if label in _DONE and not _result_ready(body):
            raise AdapterError(f"reducto job {job_id} {label} on {path} did not return a result")
        if label in _DONE or _result_ready(body):
            return body
        if label in _FAIL:
            raise AdapterError(f"reducto job {job_id} {label} on {path}: {_error_message(body)}")
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise AdapterError(
                f"reducto timed out after {timeout_s}s waiting for job {job_id} on {path}. "
                "Raise options.timeout_s or retry the file."
            )
        time.sleep(delay if delay < remaining else remaining)
        delay = 8.0 if delay * 2 > 8 else delay * 2


def _inline_url(path: Path, key: str, body: dict[str, Any]) -> dict[str, Any]:
    result = body.get("result")
    if not isinstance(result, dict) or result.get("type") != "url":
        return body
    url = result.get("url")
    if not isinstance(url, str) or not url:
        raise AdapterError(f"reducto result for {path} is a URL with no url field")
    status, text = _exchange(
        path,
        "GET",
        url,
        headers={"Authorization": f"Bearer {key}"},
        content=None,
        files=None,
    )
    fetched = _loads(status, text, path, "fetching the result")
    chunks = fetched if isinstance(fetched, list) else _url_chunks(fetched)
    if not isinstance(chunks, list):
        raise AdapterError(f"reducto result URL for {path} did not contain chunks")
    body["result"] = {"type": "full", "chunks": chunks}
    return body


def _url_chunks(body: object) -> list[object] | None:
    if not isinstance(body, dict):
        return None
    chunks = body.get("chunks")
    if isinstance(chunks, list):
        return chunks
    result = body.get("result")
    if isinstance(result, dict) and isinstance(result.get("chunks"), list):
        nested = result.get("chunks")
        if isinstance(nested, list):
            return nested
    if isinstance(result, list):
        return result
    return None


def _exchange(
    path: Path,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    content: dict[str, Any] | None,
    files: dict[str, tuple[str, bytes, str]] | None,
) -> tuple[int, str]:
    try:
        response = httpx.request(
            method, url, headers=headers, json=content, files=files, timeout=60.0
        )
    except httpx.HTTPError as err:
        raise AdapterError(f"reducto failed on {path}: {err}") from err
    return response.status_code, response.text


def _check(status: int, text: str, path: Path, what: str) -> dict[str, Any]:
    body = _loads(status, text, path, what)
    if not isinstance(body, dict):
        raise AdapterError(f"reducto returned a non-object body for {path}")
    return body


def _loads(status: int, text: str, path: Path, what: str) -> object:
    if status < 200 or status >= 300:
        raise AdapterError(
            f"reducto returned HTTP {status} while {what} {path}: {_snippet(text)}. "
            "Check the model and REDUCTO_API_KEY."
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise AdapterError(
            f"reducto returned a non-JSON body for {path}. Check REDUCTO_BASE_URL and the API status."
        ) from err


def _parse_body(file_id: str, options: dict[str, Any], model: str) -> dict[str, Any]:
    public = _public_options(options)
    settings = public.pop("settings", None)
    merged: dict[str, Any] = dict(settings) if isinstance(settings, dict) else {}
    merged["model"] = model
    body: dict[str, Any] = {"input": file_id}
    for key in sorted(public):
        body[key] = public[key]
    body["settings"] = merged
    return body


def _page_specs(options: dict[str, Any]) -> list[object]:
    found: list[object] = []
    pages = options.get("pages")
    if isinstance(pages, list):
        found.extend(pages)
    size = options.get("page_size", options.get("pageSize"))
    if isinstance(size, list) and len(size) >= 2:
        found.append({"width": size[0], "height": size[1]})
    return found


def _split_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], object | None, object]:
    parsed = payload.get("parse")
    if isinstance(parsed, dict):
        extract = payload.get("extract") if "extract" in payload else None
        return parsed, extract, payload.get("pages")
    return payload, None, payload.get("pages")


def _extract_result(extracted: object) -> object:
    if isinstance(extracted, dict) and "result" in extracted:
        return extracted.get("result")
    return extracted


def _chunks(result: object) -> list[object] | None:
    if isinstance(result, dict) and isinstance(result.get("chunks"), list):
        chunks = result.get("chunks")
        if isinstance(chunks, list):
            return chunks
    if isinstance(result, list):
        return result
    return None


def _page_table(
    spec: object,
) -> tuple[dict[int, tuple[float | None, float | None]], tuple[float | None, float | None]]:
    explicit: dict[int, tuple[float | None, float | None]] = {}
    default: tuple[float | None, float | None] = (None, None)
    if not isinstance(spec, list):
        return explicit, default
    for item in spec:
        if not isinstance(item, dict):
            continue
        width = _positive(item.get("width"))
        height = _positive(item.get("height"))
        number = _page_number(item.get("number", item.get("page")))
        if number is not None:
            explicit[number] = (width, height)
        elif width is not None or height is not None:
            default = (width, height)
    return explicit, default


def _size_for(
    number: int,
    explicit: dict[int, tuple[float | None, float | None]],
    default_size: tuple[float | None, float | None],
) -> tuple[float | None, float | None]:
    width, height = explicit.get(number, default_size)
    if width is None:
        width = default_size[0]
    if height is None:
        height = default_size[1]
    return width, height


def _billed_pages(body: dict[str, Any]) -> int:
    usage = body.get("usage")
    if isinstance(usage, dict):
        count = usage.get("num_pages")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return count
    return _distinct_pages(body)


def _distinct_pages(body: dict[str, Any]) -> int:
    found: set[int] = set()
    for chunk in _chunks(body.get("result")) or []:
        if not isinstance(chunk, dict):
            continue
        blocks = chunk.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if isinstance(block, dict):
                found.add(_page_of(block.get("bbox")))
    return len(found)


def _result_ready(body: dict[str, Any]) -> bool:
    return "result" in body and body.get("result") is not None


def _reported_model(body: dict[str, Any], requested: str) -> str:
    found = body.get("model")
    if isinstance(found, str) and found.strip():
        return found.strip()
    settings = body.get("settings")
    if isinstance(settings, dict):
        nested = settings.get("model")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    if requested.strip():
        return requested.strip()
    return DEFAULT_MODEL


def _model(options: dict[str, Any]) -> str:
    value = options.get("model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    settings = options.get("settings")
    if isinstance(settings, dict):
        nested = settings.get("model")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return DEFAULT_MODEL


def _job_id(body: dict[str, Any]) -> str:
    for key in ("job_id", "jobId"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    job = body.get("job")
    if isinstance(job, dict):
        nested = job.get("id")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def _status(body: dict[str, Any]) -> str:
    value = body.get("status")
    if not isinstance(value, str):
        return ""
    chars = [char for char in value.strip().upper() if char not in " _"]
    return "".join(chars)


def _error_message(body: dict[str, Any]) -> str:
    for key in ("error", "error_message", "message", "detail"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "conversion failed"


def _load_schema(options: dict[str, Any], source: Path) -> dict[str, Any] | None:
    raw = _schema_option(options)
    if raw is None:
        present = options.get("extract_schema", options.get("extractSchema"))
        if present is not None:
            raise AdapterError("options.extract_schema must be a path to a JSON Schema file")
        return None
    path = _find_schema(Path(raw.strip()), source)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise AdapterError(f"reducto cannot read extract schema {path}: {err}") from err
    if not isinstance(loaded, dict):
        raise AdapterError(f"reducto extract schema {path} must be a JSON object")
    return loaded


def _schema_option(options: dict[str, Any]) -> str | None:
    raw = options.get("extract_schema", options.get("extractSchema"))
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def _find_schema(candidate: Path, source: Path) -> Path:
    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
        raise AdapterError(f"reducto cannot read extract schema {candidate}: path not found")
    cwd_hit = Path.cwd() / candidate
    beside = source.parent / candidate
    if cwd_hit.is_file():
        return cwd_hit
    if beside.is_file():
        return beside
    raise AdapterError(
        f"reducto cannot read extract schema {candidate}: path not found. "
        f"Looked in {cwd_hit} and {beside}."
    )


def _api_key() -> str:
    return os.environ.get("REDUCTO_API_KEY", "").strip()


def _strip_slashes(value: str) -> str:
    end = len(value)
    while end > 0 and value[end - 1] == "/":
        end -= 1
    return value[:end]


def _base_url(options: dict[str, Any]) -> str:
    for key in ("baseUrl", "base_url"):
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            return _strip_slashes(value.strip())
    env = os.environ.get("REDUCTO_BASE_URL", "").strip()
    if env:
        return _strip_slashes(env)
    return _DEFAULT_BASE


def _timeout_s(options: dict[str, Any]) -> float:
    value = options.get("timeout_s", options.get("timeoutS", 600))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError("options.timeout_s must be a number greater than 0")
    number = float(value)
    if number <= 0 or math.isnan(number) or math.isinf(number):
        raise AdapterError("options.timeout_s must be a number greater than 0")
    return number


def _public_options(options: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in options.items() if key not in _RESERVED}


def _page_of(value: object) -> int:
    if not isinstance(value, dict):
        return 1
    original = _page_number(value.get("original_page"))
    if original is not None:
        return original
    page = _page_number(value.get("page"))
    if page is not None:
        return page
    return 1


def _page_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    if (
        isinstance(value, float)
        and math.isfinite(value)
        and value >= 1
        and value == math.floor(value)
    ):
        return int(value)
    return None


def _raw_box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    left = _number(value.get("left"))
    top = _number(value.get("top"))
    width = _number(value.get("width"))
    height = _number(value.get("height"))
    if left is None or top is None or width is None or height is None:
        return None
    return (left, top, width, height)


def _bbox(value: object, width: float | None, height: float | None) -> BBox | None:
    raw = _raw_box(value)
    if raw is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    left, top, box_width, box_height = raw
    x0 = left * width
    y0 = top * height
    x1 = (left + box_width) * width
    y1 = (top + box_height) * height
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return BBox(root=[_round4(x0), _round4(y0), _round4(x1), _round4(y1)])


def _positive(value: object) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _unit(value: object) -> float | None:
    number = _number(value)
    if number is None or number < 0 or number > 1:
        return None
    return number


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _round4(value: float) -> float:
    return math.floor(value * 10000 + 0.5) / 10000


def _hash_level(text: str) -> int:
    index = 0
    while index < len(text) and text[index] in " \t":
        index += 1
    hashes = 0
    while index < len(text) and text[index] == "#" and hashes < 6:
        hashes += 1
        index += 1
    if hashes >= 1 and (index == len(text) or text[index] == " "):
        return hashes
    return 1


def _trim(text: str) -> str:
    start = 0
    end = len(text)
    while start < end and text[start] in _SPACE:
        start += 1
    while end > start and text[end - 1] in _SPACE:
        end -= 1
    return text[start:end]


def _snippet(text: str) -> str:
    flat = "".join(" " if char in "\r\n\t" else char for char in text)
    if len(flat) > 200:
        return flat[:200]
    return flat
