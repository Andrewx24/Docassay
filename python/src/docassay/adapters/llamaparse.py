"""LlamaParse adapter.

Both languages call the LlamaParse v2 REST API (upload, poll, fetch items).
Mapping is pure. ``parserVersion`` is the version the API reports, and the
dated pin when the response omits one.

Example:
    from docassay.adapters.llamaparse import LlamaParseAdapter

    adapter = LlamaParseAdapter("agentic", {"version": "latest"})
    ok, reason = adapter.available()
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

import httpx

from docassay.adapters.base import AdapterError, RawResult
from docassay.adapters.runner_support import package_version
from docassay.models.document import BBox, Block, Document, Meta, Page, Source, Table, Type

# LlamaParse v2 credit prices.
# Source: https://developers.llamaindex.ai/llamaparse/general/pricing/
# Read 2026-11-02. Conversion is $1.25 per 1,000 credits.
CREDITS_PER_PAGE: dict[str, int] = {
    "fast": 1,
    "cost_effective": 3,
    "agentic": 10,
    "agentic_plus": 45,
}
USD_PER_THOUSAND_CREDITS = 1.25

TIERS: tuple[str, ...] = ("fast", "cost_effective", "agentic", "agentic_plus")
API_KEY = (
    "LLAMA_CLOUD_API_KEY is not set. Create a key at https://cloud.llamaindex.ai and export it."
)
_DEFAULT_BASE = "https://api.cloud.llamaindex.ai"
_EXPAND = "items,markdown,job_metadata"
_RESERVED = {"baseUrl", "base_url", "timeout_s", "timeoutS", "apiKey", "api_key", "version"}
_SPACE = " \t\r\n\u00a0"
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}


class LlamaParseAdapter:
    """Upload a file to LlamaParse and map the items tree to a Document.

    The variant is the tier (``fast``, ``cost_effective``, ``agentic``,
    ``agentic_plus``). ``options.version`` defaults to ``"latest"``.

    Example:
        LlamaParseAdapter("fast", {}).cache_identity()
    """

    id: ClassVar[str] = "llamaparse"
    variants: ClassVar[tuple[str, ...]] = TIERS

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None:
        self.variant = variant
        self.options = options

    def available(self) -> tuple[bool, str]:
        """Return whether ``LLAMA_CLOUD_API_KEY`` is set.

        Example:
            ok, reason = LlamaParseAdapter("fast", {}).available()
        """
        if not _api_key():
            return False, API_KEY
        return True, ""

    def cache_identity(self) -> dict[str, Any]:
        """Return the tier, version, and options that change parser output.

        The API key, base URL, and timeout are omitted.

        Example:
            LlamaParseAdapter("agentic", {"version": "2026-09-29"}).cache_identity()
        """
        return {
            "variant": self.variant,
            "version": _version_option(self.options),
            "options": _public_options(self.options),
        }

    def fetch(self, path: Path) -> RawResult:
        """Upload ``path``, poll until the job finishes, and return the JSON body.

        Example:
            raw = LlamaParseAdapter("fast", {}).fetch(Path("files/sample.pdf"))
        """
        if self.variant not in TIERS:
            raise AdapterError(
                "llamaparse needs a tier variant (fast, cost_effective, agentic, "
                'agentic_plus). Use an id like "llamaparse:agentic".'
            )
        if not path.is_file():
            raise AdapterError(f"llamaparse cannot read {path}: path not found")
        key = _api_key()
        if not key:
            raise AdapterError(API_KEY)
        timeout_s = _timeout_s(self.options)
        version = _version_option(self.options)
        base = _base_url(self.options)
        started = time.perf_counter()
        job_id = _upload(path, key, base, self.variant, self.options)
        payload = _poll(path, key, base, job_id, timeout_s)
        elapsed = (time.perf_counter() - started) * 1000
        return RawResult(
            payload=payload,
            parser_version=_reported_version(payload, version),
            latency_ms=elapsed,
            cost_usd=llamaparse_cost_usd(self.variant, _page_count(payload)),
        )

    def to_document(self, raw: RawResult, source: Source) -> Document:
        """Map a recorded LlamaParse result. This method does not call the API.

        Example:
            document = adapter.to_document(raw, source)
        """
        return llamaparse_to_document(raw.payload, source, raw.parser_version, self.variant)


def llamaparse_cost_usd(tier: str, page_count: int) -> float:
    """Return USD for ``page_count`` pages at ``tier``.

    One ``agentic`` page is 10 credits. The credit table is ``CREDITS_PER_PAGE``.

    Example:
        llamaparse_cost_usd("agentic", 1)
    """
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        return 0.0
    credits = CREDITS_PER_PAGE.get(tier)
    if credits is None:
        return 0.0
    return credits * page_count * USD_PER_THOUSAND_CREDITS / 1000


def llamaparse_to_document(
    payload: object,
    source: Source,
    parser_version: str,
    variant: str | None,
) -> Document:
    """Map one LlamaParse v2 result (``expand=items,markdown``) to a Document.

    Item boxes are top-left page points. A box whose coordinates all lie in
    0–1 is scaled by the page size, and omitted when that size is missing.
    The first table row is the header. HTML ``colspan`` and ``rowspan`` repeat
    the cell text. Header and footer containers stay ``header`` and ``footer``.

    Example:
        document = llamaparse_to_document({"items": {"pages": []}}, source, "2026-09-29", "fast")
    """
    if not isinstance(payload, dict):
        raise AdapterError(
            "llamaparse payload must be a JSON object from GET /api/v2/parse/{job_id}"
        )
    if not parser_version:
        raise AdapterError(
            "llamaparse parserVersion is empty. Record the version the API reported."
        )
    pages_raw = _record(payload.get("items")).get("pages")
    if not isinstance(pages_raw, list):
        raise AdapterError("llamaparse payload needs items.pages. Fetch the job with expand=items.")
    headers, footers = _markdown_regions(payload)
    sizes: dict[int, tuple[float | None, float | None]] = {}
    state = _State()
    for page in pages_raw:
        if not isinstance(page, dict):
            continue
        number = _page_number(page.get("page_number"))
        width = _positive(page.get("page_width"))
        height = _positive(page.get("page_height"))
        sizes[number] = (width, height)
        items = page.get("items")
        if not isinstance(items, list):
            items = []
        failed = page.get("success") is False
        if not _has_region(items, "header") and number in headers:
            _add_block(state, Type.header, headers[number], number, None, None)
        if not failed:
            for item in items:
                _emit(item, number, width, height, state, None, False)
        if not _has_region(items, "footer") and number in footers:
            _add_block(state, Type.footer, footers[number], number, None, None)
    for number in state.used_pages:
        sizes.setdefault(number, (None, None))
    ordered = [
        Page(number=number, width=size[0], height=size[1]) for number, size in sorted(sizes.items())
    ]
    parser = "llamaparse" if not variant else f"llamaparse:{variant}"
    return Document(
        spec_version="1.0.0",
        source=source,
        meta=Meta(parser=parser, parser_version=parser_version, adapter_version=package_version()),
        pages=ordered,
        blocks=state.blocks,
        tables=state.tables,
    )


class _State:
    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.tables: list[Table] = []
        self.used_pages: set[int] = set()


def _upload(path: Path, key: str, base: str, variant: str, options: dict[str, Any]) -> str:
    config: dict[str, Any] = {"tier": variant, "version": _version_option(options)}
    public = _public_options(options)
    for name in sorted(public):
        config[name] = public[name]
    status, text = _exchange(
        path,
        "POST",
        f"{base}/api/v2/parse/upload",
        headers={"Authorization": f"Bearer {key}"},
        data={"configuration": json.dumps(config, separators=(",", ":"), ensure_ascii=False)},
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    body = _check(status, text, path, "uploading")
    job_id = _job_id(body)
    if not job_id:
        raise AdapterError(f"llamaparse upload for {path} did not return a job id")
    return job_id


def _poll(path: Path, key: str, base: str, job_id: str, timeout_s: float) -> dict[str, Any]:
    url = f"{base}/api/v2/parse/{quote(job_id, safe='')}?expand={_EXPAND}"
    headers = {"Authorization": f"Bearer {key}"}
    delay = 1.0
    started = time.monotonic()
    while True:
        status, text = _exchange(path, "GET", url, headers=headers, data=None, files=None)
        body = _check(status, text, path, "polling")
        job = _record(body.get("job")) or body
        label = _status(job)
        if label == "COMPLETED":
            return body
        if label in {"FAILED", "CANCELLED"}:
            raise AdapterError(
                f"llamaparse job {job_id} {label} on {path}: {_error_message(body, job)}"
            )
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise AdapterError(
                f"llamaparse timed out after {timeout_s}s waiting for job {job_id} on {path}. "
                "Raise options.timeout_s or retry the file."
            )
        time.sleep(delay if delay < remaining else remaining)
        delay = 8.0 if delay * 2 > 8 else delay * 2


def _exchange(
    path: Path,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    data: dict[str, str] | None,
    files: dict[str, tuple[str, bytes, str]] | None,
) -> tuple[int, str]:
    try:
        response = httpx.request(method, url, headers=headers, data=data, files=files, timeout=60.0)
    except httpx.HTTPError as err:
        raise AdapterError(f"llamaparse failed on {path}: {err}") from err
    return response.status_code, response.text


def _check(status: int, text: str, path: Path, what: str) -> dict[str, Any]:
    if status < 200 or status >= 300:
        raise AdapterError(
            f"llamaparse returned HTTP {status} while {what} {path}: {_snippet(text)}. "
            "Check the tier, version, and LLAMA_CLOUD_API_KEY."
        )
    try:
        body = json.loads(text)
    except json.JSONDecodeError as err:
        raise AdapterError(
            f"llamaparse returned a non-JSON body for {path}. "
            "Check LLAMA_CLOUD_BASE_URL and the API status."
        ) from err
    if not isinstance(body, dict):
        raise AdapterError(f"llamaparse returned a non-object body for {path}")
    return body


def _emit(
    item: object,
    page: int,
    width: float | None,
    height: float | None,
    state: _State,
    force: Type | None,
    in_list: bool,
) -> None:
    if not isinstance(item, dict):
        return
    kind = item.get("type")
    if not isinstance(kind, str):
        kind = ""
    if kind == "table":
        _add_table(item, page, width, height, state)
        return
    if kind == "list":
        children = item.get("items")
        if isinstance(children, list):
            for child in children:
                _emit(child, page, width, height, state, force, True)
        return
    if kind in {"header", "footer"}:
        forced = Type.header if kind == "header" else Type.footer
        children = item.get("items")
        if isinstance(children, list) and any(isinstance(child, dict) for child in children):
            for child in children:
                _emit(child, page, width, height, state, forced, False)
            return
        md = item.get("md")
        text = _trim(md if isinstance(md, str) else "")
        if text:
            _add_block(state, forced, text, page, None, _bbox(item.get("bbox"), width, height))
        return
    if force is not None:
        text = _item_text(item, kind)
        if text:
            _add_block(state, force, text, page, None, _bbox(item.get("bbox"), width, height))
        return
    block_type, text, level = _classify(item, kind, in_list)
    if text:
        _add_block(state, block_type, text, page, level, _bbox(item.get("bbox"), width, height))


def _classify(item: dict[str, Any], kind: str, in_list: bool) -> tuple[Type, str, int | None]:
    if kind == "heading":
        return Type.heading, _string(item.get("value")), _level(item.get("level"))
    if kind == "text":
        block_type = Type.list_item if in_list else Type.paragraph
        return block_type, _string(item.get("value")), None
    if kind == "code":
        return Type.code, _string(item.get("value")), None
    if kind == "image":
        caption = item.get("caption")
        if isinstance(caption, str) and caption:
            return Type.caption, caption, None
        return Type.other, _string(item.get("md")), None
    if kind == "link":
        text = item.get("text")
        if isinstance(text, str) and text:
            return Type.other, text, None
        return Type.other, _string(item.get("md")), None
    return Type.other, _item_text(item, kind), None


def _add_table(
    item: dict[str, Any],
    page: int,
    width: float | None,
    height: float | None,
    state: _State,
) -> None:
    matrix = _table_matrix(item)
    if not matrix:
        return
    header = matrix[0]
    body = matrix[1:]
    markdown = item.get("md")
    state.tables.append(
        Table(
            id=f"t{len(state.tables) + 1}",
            page=page,
            bbox=_bbox(item.get("bbox"), width, height),
            header=header,
            rows=body,
            markdown=markdown if isinstance(markdown, str) and markdown else None,
        )
    )
    state.used_pages.add(page)


def _table_matrix(item: dict[str, Any]) -> list[list[str]]:
    html = item.get("html")
    html_text = html if isinstance(html, str) else ""
    rows = _rows(item.get("rows"))
    lower = html_text.lower()
    spanned = "colspan=" in lower or "rowspan=" in lower
    if html_text and (spanned or not rows):
        grid = _html_grid(html_text)
        if grid and (spanned or not rows):
            return grid
    return rows


def _rows(value: object) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        return []
    raw: list[list[str]] = []
    width = 0
    for row in value:
        if not isinstance(row, list):
            return []
        cells = [_cell(cell) for cell in row]
        raw.append(cells)
        if len(cells) > width:
            width = len(cells)
    return [row + [""] * (width - len(row)) for row in raw]


def _cell(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value == math.floor(value) and abs(value) <= 2**53:
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        return value
    return ""


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


def _add_block(
    state: _State,
    block_type: Type,
    text: str,
    page: int,
    level: int | None,
    bbox: BBox | None,
) -> None:
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
    state.used_pages.add(page)


def _item_text(item: dict[str, Any], kind: str) -> str:
    if kind == "image":
        caption = item.get("caption")
        if isinstance(caption, str) and caption:
            return caption
        return _string(item.get("md"))
    if kind == "link":
        text = item.get("text")
        if isinstance(text, str) and text:
            return text
        return _string(item.get("md"))
    value = item.get("value")
    if isinstance(value, str) and value:
        return value
    return _string(item.get("md"))


def _bbox(value: object, width: float | None, height: float | None) -> BBox | None:
    if not isinstance(value, list):
        return None
    boxes: list[tuple[float, float, float, float]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        x = _number(item.get("x"))
        y = _number(item.get("y"))
        w = _number(item.get("w"))
        h = _number(item.get("h"))
        if x is None or y is None or w is None or h is None:
            continue
        x1 = x + w
        y1 = y + h
        largest = max(abs(x), abs(y), abs(w), abs(h), abs(x1), abs(y1))
        if largest <= 1:
            if width is None or height is None or width <= 1 or height <= 1:
                continue
            x *= width
            y *= height
            x1 *= width
            y1 *= height
        if x > x1:
            x, x1 = x1, x
        if y > y1:
            y, y1 = y1, y
        boxes.append((x, y, x1, y1))
    if not boxes:
        return None
    return BBox(
        root=[
            _round4(min(box[0] for box in boxes)),
            _round4(min(box[1] for box in boxes)),
            _round4(max(box[2] for box in boxes)),
            _round4(max(box[3] for box in boxes)),
        ]
    )


def _markdown_regions(payload: dict[str, Any]) -> tuple[dict[int, str], dict[int, str]]:
    headers: dict[int, str] = {}
    footers: dict[int, str] = {}
    pages = _record(payload.get("markdown")).get("pages")
    if not isinstance(pages, list):
        return headers, footers
    for page in pages:
        if not isinstance(page, dict):
            continue
        number = _page_number(page.get("page_number"))
        header_text = page.get("header")
        footer_text = page.get("footer")
        header = _trim(header_text if isinstance(header_text, str) else "")
        footer = _trim(footer_text if isinstance(footer_text, str) else "")
        if header:
            headers[number] = header
        if footer:
            footers[number] = footer
    return headers, footers


def _has_region(items: list[object], kind: str) -> bool:
    return any(isinstance(item, dict) and item.get("type") == kind for item in items)


def _reported_version(payload: dict[str, Any], requested: str) -> str:
    found = _version_field(payload)
    if found:
        return found
    if requested and requested != "latest":
        return requested
    return "unknown"


def _version_field(payload: dict[str, Any]) -> str:
    job = _record(payload.get("job"))
    found = _nonempty(job.get("version"))
    if found:
        return found
    found = _nonempty(_record(job.get("parameters")).get("version"))
    if found:
        return found
    raw = _record(payload.get("raw_parameters"))
    found = _nonempty(raw.get("version"))
    if found:
        return found
    found = _nonempty(_record(raw.get("parameters")).get("version"))
    if found:
        return found
    meta = _record(payload.get("job_metadata"))
    found = _nonempty(meta.get("version"))
    if found:
        return found
    return _nonempty(_record(meta.get("parameters")).get("version"))


def _page_count(payload: dict[str, Any]) -> int:
    items = _record(payload.get("items")).get("pages")
    if isinstance(items, list) and items:
        return len(items)
    pages = _record(payload.get("markdown")).get("pages")
    if isinstance(pages, list):
        return len(pages)
    return 0


def _job_id(body: dict[str, Any]) -> str:
    value = body.get("id")
    if isinstance(value, str) and value:
        return value
    return _nonempty(_record(body.get("job")).get("id"))


def _status(job: dict[str, Any]) -> str:
    value = job.get("status")
    if isinstance(value, str):
        return value.strip().upper()
    return ""


def _error_message(body: dict[str, Any], job: dict[str, Any]) -> str:
    for source in (job, body):
        text = source.get("error_message")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return "conversion failed"


def _api_key() -> str:
    return os.environ.get("LLAMA_CLOUD_API_KEY", "").strip()


def _base_url(options: dict[str, Any]) -> str:
    for key in ("baseUrl", "base_url"):
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip("/")
    env = os.environ.get("LLAMA_CLOUD_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    return _DEFAULT_BASE


def _version_option(options: dict[str, Any]) -> str:
    value = options.get("version")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "latest"


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


def _page_number(value: object) -> int:
    if isinstance(value, bool):
        return 1
    if isinstance(value, int) and value >= 1:
        return value
    if (
        isinstance(value, float)
        and math.isfinite(value)
        and value >= 1
        and value == math.floor(value)
    ):
        return int(value)
    return 1


def _positive(value: object) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _round4(value: float) -> float:
    return math.floor(value * 10000 + 0.5) / 10000


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _level(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 1
    return value


def _string(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


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


def _record(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _nonempty(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""
