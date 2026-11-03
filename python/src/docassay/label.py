"""Draft a golden file from a parser Document and review it locally.

The draft is deterministic: the same Document and paths always produce the
same YAML. Each drafted value ends with ``# TODO verify``. ``docassay
validate --strict`` fails until the review page saves the file.

Example:
    from docassay.label import draft_yaml

    print(draft_yaml(document, file_ref="../files/lease.pdf", doc_id="lease"))
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import webbrowser
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import yaml
from pydantic import ValidationError

from docassay.adapters.base import AdapterConfigError, AdapterError, AdapterUnavailable
from docassay.adapters.registry import resolve
from docassay.adapters.runner_support import run_parser
from docassay.config import ConfigError, load_config
from docassay.golden import CoreLoader
from docassay.models.document import Document
from docassay.models.golden import GoldenFile
from docassay.normalize.text import is_whitespace

_SNIPPET = 80
_TODO = " # TODO verify"


class LabelError(Exception):
    """A label command that cannot draft or open the review page.

    Example:
        raise LabelError("pass --from <parser> or --review")
    """

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ReviewServer:
    """Local review page bound to ``127.0.0.1``.

    Example:
        server = start_review(Path("golden/lease.yaml"), open_browser=False)
        server.close()
    """

    def __init__(self, httpd: ThreadingHTTPServer, thread: threading.Thread, done: _Flag) -> None:
        self._httpd = httpd
        self._thread = thread
        self._done = done

    @property
    def url(self) -> str:
        """Base URL of the review page.

        Example:
            start_review(path, open_browser=False).url.startswith("http://127.0.0.1:")
        """
        host, port = self._httpd.server_address[:2]
        shown = host.decode() if isinstance(host, bytes) else host
        return f"http://{shown}:{port}"

    def wait(self, timeout: float | None = None) -> None:
        """Block until the reviewer saves or ``timeout`` seconds pass.

        Example:
            server.wait(timeout=1)
        """
        self._thread.join(timeout)

    def close(self) -> None:
        """Stop the server if the page is still open.

        Example:
            server.close()
        """
        self._done.set()
        try:
            urlopen(self.url + "/shutdown", timeout=1).read()
        except (OSError, URLError):
            pass
        self._thread.join(timeout=2)
        self._httpd.server_close()


def draft_yaml(document: Document, *, file_ref: str, doc_id: str) -> str:
    """Return golden YAML for ``document``.

    Up to three largest tables are kept. Each contributes its header, body-row
    count, column count, and two cells nearest the middle of the middle body
    row. Text spans are the block before a page break, the first footnote or
    caption, and the last block on the last page. Reading-order pairs straddle
    a page break when the document has more than one page.

    Example:
        draft_yaml(document, file_ref="../files/lease.pdf", doc_id="lease")
    """
    return render_yaml(_draft_dict(document, file_ref, doc_id), todo=True)


def render_yaml(data: Mapping[str, Any], *, todo: bool) -> str:
    """Return ``data`` as golden YAML. ``todo`` marks every drafted value.

    Example:
        render_yaml({"file": "a.pdf", "id": "a"}, todo=False)
    """
    file_ref = data.get("file")
    doc_id = data.get("id")
    if not isinstance(file_ref, str) or file_ref == "":
        raise LabelError("golden file is missing file. Add a file path and retry.")
    lines = [f"file: {_quote(file_ref)}"]
    if isinstance(doc_id, str) and doc_id != "":
        lines.append(f"id: {_quote(doc_id)}")
    expect = data.get("expect")
    if isinstance(expect, dict) and expect:
        lines.append("expect:")
        lines.extend(_expect_lines(expect, todo))
    return "\n".join(lines) + "\n"


def label_command(
    path: Path,
    *,
    parser: str | None = None,
    out: Path | None = None,
    review: bool = False,
    config: Path = Path("docassay.yaml"),
    open_browser: bool = True,
) -> int:
    """Draft a golden file or open the review page. Returns the exit code.

    Example:
        label_command(Path("files/lease.pdf"), parser="file")
    """
    try:
        if review and parser is not None:
            raise LabelError("pass either --from <parser> or --review, not both.")
        if review:
            server = start_review(path, open_browser=open_browser)
            print(f"review at {server.url}")
            server.wait()
            print(f"wrote {path.as_posix()}")
            return 0
        if parser is None:
            raise LabelError(
                "usage: docassay label <file> --from <parser> [--out golden/<id>.yaml]\n"
                "       docassay label <golden.yaml> --review"
            )
        target = _write_draft(path, parser, out, config)
        print(f"wrote {target.as_posix()}")
        return 0
    except LabelError as err:
        print(err, file=sys.stderr)
        return err.exit_code
    except AdapterUnavailable as err:
        print(err, file=sys.stderr)
        return 3
    except AdapterError as err:
        print(err, file=sys.stderr)
        return 3
    except (AdapterConfigError, ConfigError) as err:
        print(err, file=sys.stderr)
        return 2


def start_review(golden_path: Path, *, open_browser: bool = True) -> ReviewServer:
    """Serve the review page for ``golden_path`` on ``127.0.0.1``.

    Saving rewrites the file without ``# TODO verify``. The browser opens
    unless ``open_browser`` is false.

    Example:
        start_review(Path("golden/lease.yaml"), open_browser=False).close()
    """
    path = golden_path.resolve()
    if not path.is_file():
        raise LabelError(f"{golden_path}: golden file not found. Draft it with --from first.")
    state = _Flag()
    model = _load_model(path)
    html = review_html(model)
    source = _source_path(path, model)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/":
                _send(self, 200, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            if self.path.split("?", 1)[0] == "/source":
                _send_source(self, source)
                return
            if self.path.split("?", 1)[0] == "/shutdown":
                state.set()
                _send(self, 200, "text/plain; charset=utf-8", b"bye\n")
                return
            _send(self, 404, "text/plain; charset=utf-8", b"not found\n")

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/save":
                _send(self, 404, "text/plain; charset=utf-8", b"not found\n")
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length)
            try:
                loaded = json.loads(raw.decode("utf-8"))
                text = _confirmed_yaml(loaded)
            except (json.JSONDecodeError, UnicodeError, LabelError, ValidationError) as err:
                message = (
                    err if isinstance(err, LabelError) else LabelError(f"could not save: {err}")
                )
                _send(self, 400, "text/plain; charset=utf-8", f"{message}\n".encode())
                return
            path.write_text(text, encoding="utf-8")
            state.set()
            _send(self, 200, "text/plain; charset=utf-8", b"saved\n")

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=_serve_until_done, args=(httpd, state), daemon=True)
    thread.start()
    server = ReviewServer(httpd, thread, state)
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(server.url,), daemon=True).start()
    return server


def review_html(model: Mapping[str, Any]) -> str:
    """Return the review page for ``model``.

    The source PDF is shown from ``/source`` on the same localhost origin.
    The page does not load a CDN.

    Example:
        "<html" in review_html({"file": "a.pdf", "id": "a"}).lower()
    """
    payload = json.dumps(model, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>docassay label</title>
<style>
body {{ margin: 0; font: 16px/1.4 sans-serif; color: #1a1a1a; }}
main {{ display: flex; height: 100vh; }}
.sheet, form {{ flex: 1; overflow: auto; padding: 16px; box-sizing: border-box; }}
.sheet {{ background: #f4f4f4; }}
iframe {{ width: 100%; height: calc(100% - 32px); border: 0; background: white; }}
button {{ margin-left: 8px; }}
label {{ display: block; margin: 8px 0; }}
section {{ border-top: 1px solid #ccc; margin-top: 12px; padding-top: 8px; }}
</style>
</head>
<body>
<main>
<section class="sheet">
<p>Source file</p>
<iframe src="/source" title="Source PDF"></iframe>
</section>
<form id="draft">
<p>Edit the draft, remove a wrong line, then save. Saving drops # TODO verify.</p>
<div id="fields"></div>
<p><button type="submit">Save</button> <span id="status"></span></p>
</form>
</main>
<script id="draft-data" type="application/json">{payload}</script>
<script>
const draft = JSON.parse(document.getElementById("draft-data").textContent);
const root = document.getElementById("fields");
const expect = draft.expect || {{}};
function item(html) {{
  const node = document.createElement("div");
  node.dataset.item = "1";
  node.innerHTML = html + '<button type="button" data-remove>Remove</button>';
  return node;
}}
function addSection(title, nodes) {{
  if (!nodes.length) return;
  const section = document.createElement("section");
  section.innerHTML = "<h2>" + title + "</h2>";
  nodes.forEach((node) => section.appendChild(node));
  root.appendChild(section);
}}
(expect.tables || []).forEach((table) => {{
  const section = document.createElement("section");
  section.dataset.table = "1";
  section.innerHTML = "<h2>Table</h2>";
  section.appendChild(item('page <input data-page value="' + esc(table.page) + '">'));
  (table.header || []).forEach((cell) => {{
    section.appendChild(item('header <input data-header value="' + esc(cell) + '">'));
  }});
  section.appendChild(item('rows <input data-rows value="' + esc(table.rows) + '">'));
  section.appendChild(item('cols <input data-cols value="' + esc(table.cols) + '">'));
  (table.cells || []).forEach((cell) => {{
    const node = item(
      'row <input data-row value="' + esc(cell.row) + '"> ' +
      'col <input data-col value="' + esc(cell.col) + '"> ' +
      'value <input data-value value="' + esc(cell.value) + '">'
    );
    node.dataset.cell = "1";
    section.appendChild(node);
  }});
  section.insertAdjacentHTML("beforeend", '<button type="button" data-remove>Remove table</button>');
  root.appendChild(section);
}});
addSection("Text", (expect.text_contains || []).map((text) =>
  item('text <input data-text value="' + esc(text) + '">')));
addSection("Reading order", (expect.reading_order || []).map((pair) =>
  item('before <input data-before value="' + esc(pair[0]) + '"> after <input data-after value="' + esc(pair[1]) + '">')));
const fieldNodes = Object.keys(expect.fields || {{}}).map((key) => {{
  const value = expect.fields[key];
  const type = value === null ? "null" : typeof value;
  return item(esc(key) + ' <input data-field="' + esc(key) + '" data-type="' + type + '" value="' + esc(value) + '">');
}});
addSection("Fields", fieldNodes);
root.addEventListener("click", (event) => {{
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  const section = button.closest("[data-table]");
  if (button.parentElement === section) section.remove();
  else button.closest("[data-item]").remove();
}});
document.getElementById("draft").addEventListener("submit", async (event) => {{
  event.preventDefault();
  const next = {{ file: draft.file, id: draft.id, expect: {{}} }};
  const tables = [];
  root.querySelectorAll("[data-table]").forEach((section) => {{
    const table = {{ cells: [] }};
    const page = section.querySelector("[data-page]");
    const rows = section.querySelector("[data-rows]");
    const cols = section.querySelector("[data-cols]");
    if (page) table.page = Number(page.value);
    if (rows) table.rows = Number(rows.value);
    if (cols) table.cols = Number(cols.value);
    const header = [];
    section.querySelectorAll("[data-header]").forEach((input) => header.push(input.value));
    if (header.length) table.header = header;
    section.querySelectorAll("[data-cell]").forEach((cell) => {{
      const col = cell.querySelector("[data-col]").value;
      table.cells.push({{
        row: Number(cell.querySelector("[data-row]").value),
        col: /^[0-9]+$/.test(col) ? Number(col) : col,
        value: cell.querySelector("[data-value]").value
      }});
    }});
    tables.push(table);
  }});
  if (tables.length) next.expect.tables = tables;
  const texts = [];
  root.querySelectorAll("[data-text]").forEach((input) => {{ if (input.value) texts.push(input.value); }});
  if (texts.length) next.expect.text_contains = texts;
  const pairs = [];
  root.querySelectorAll("[data-before]").forEach((input) => {{
    const after = input.parentElement.querySelector("[data-after]").value;
    if (input.value && after) pairs.push([input.value, after]);
  }});
  if (pairs.length) next.expect.reading_order = pairs;
  const fields = {{}};
  root.querySelectorAll("[data-field]").forEach((input) => {{
    fields[input.dataset.field] = typed(input.dataset.type, input.value);
  }});
  if (Object.keys(fields).length) next.expect.fields = fields;
  const response = await fetch("/save", {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify(next)
  }});
  document.getElementById("status").textContent = response.ok
    ? "Saved. You can close this tab."
    : "Could not save. Check the values and retry.";
}});
function typed(type, value) {{
  if (type === "number") return Number(value);
  if (type === "boolean") return value === "true";
  if (type === "null") return null;
  return value;
}}
function esc(value) {{
  return String(value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}}
</script>
</body>
</html>
"""


def _draft_dict(document: Document, file_ref: str, doc_id: str) -> dict[str, Any]:
    expect: dict[str, Any] = {}
    tables = _tables(document)
    texts = _texts(document)
    pairs = _pairs(texts)
    spans = _spans(texts)
    if tables:
        expect["tables"] = tables
    if spans:
        expect["text_contains"] = spans
    if pairs:
        expect["reading_order"] = pairs
    fields = _fields(document)
    if fields:
        expect["fields"] = fields
    payload: dict[str, Any] = {"file": file_ref, "id": doc_id}
    if expect:
        payload["expect"] = expect
    return payload


def _tables(document: Document) -> list[dict[str, Any]]:
    ranked: list[tuple[int, int, Any]] = []
    for index, table in enumerate(document.tables):
        size = _table_size(table.header, table.rows)
        if size == 0:
            continue
        ranked.append((size, index, table))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    drafted: list[dict[str, Any]] = []
    for _, _, table in ranked[:3]:
        header = list(table.header) if table.header is not None else None
        width = _width(header, table.rows)
        body = table.rows
        entry: dict[str, Any] = {"page": table.page, "rows": len(body), "cols": width}
        if header is not None:
            entry = {"page": table.page, "header": header, "rows": len(body), "cols": width}
        cells = []
        if body and width > 0:
            mid_row = len(body) // 2
            for col in _middle_columns(width):
                row = body[mid_row]
                value = row[col] if col < len(row) else ""
                cells.append({"row": mid_row, "col": _column_label(header, col), "value": value})
        if cells:
            entry["cells"] = cells
        drafted.append(entry)
    return drafted


def _table_size(header: Sequence[str] | None, rows: Sequence[Sequence[str]]) -> int:
    count = len(header or [])
    for row in rows:
        count += len(row)
    return count


def _width(header: Sequence[str] | None, rows: Sequence[Sequence[str]]) -> int:
    width = len(header or [])
    for row in rows:
        width = max(width, len(row))
    return width


def _middle_columns(width: int) -> list[int]:
    center = (width - 1) / 2
    ranked = sorted(range(width), key=lambda index: (abs(index - center), index))
    return sorted(ranked[:2])


def _column_label(header: Sequence[str] | None, col: int) -> str | int:
    if header is None or col >= len(header) or header[col] == "":
        return col
    name = header[col]
    if sum(1 for item in header if item == name) > 1:
        return col
    return name


def _texts(document: Document) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for block in document.blocks:
        text = _snippet(block.text)
        if text:
            found.append((block.page, block.type.value, text))
    return found


def _spans(items: Sequence[tuple[int, str, str]]) -> list[str]:
    if not items:
        return []
    pages = sorted({page for page, _, _ in items})
    chosen: list[str] = []
    if len(pages) >= 2:
        earlier = pages[-2]
        for page, _, text in reversed(items):
            if page == earlier:
                chosen.append(text)
                break
    for _, kind, text in items:
        if kind in ("footnote", "caption"):
            chosen.append(text)
            break
    last_page = pages[-1]
    for page, _, text in reversed(items):
        if page == last_page:
            chosen.append(text)
            break
    unique: list[str] = []
    for text in chosen:
        if text not in unique:
            unique.append(text)
    return unique


def _pairs(items: Sequence[tuple[int, str, str]]) -> list[list[str]]:
    if len(items) < 2:
        return []
    pages = sorted({page for page, _, _ in items})
    pairs: list[list[str]] = []

    def add(before: str, after: str) -> None:
        if before == after or len(pairs) >= 2:
            return
        pair = [before, after]
        if pair not in pairs:
            pairs.append(pair)

    if len(pages) >= 2:
        add(_last_on(items, pages[0]), _first_on(items, pages[1]))
        if len(pages) > 2:
            add(_last_on(items, pages[-2]), _first_on(items, pages[-1]))
        add(items[0][2], items[-1][2])
    else:
        add(items[0][2], items[-1][2])
        add(items[0][2], items[len(items) // 2][2])
    return pairs


def _last_on(items: Sequence[tuple[int, str, str]], page: int) -> str:
    found = ""
    for item_page, _, text in items:
        if item_page == page:
            found = text
    return found


def _first_on(items: Sequence[tuple[int, str, str]], page: int) -> str:
    for item_page, _, text in items:
        if item_page == page:
            return text
    return ""


def _fields(document: Document) -> dict[str, Any]:
    if not document.fields:
        return {}
    return {key: field.value for key, field in document.fields.items()}


def _snippet(text: str) -> str:
    trimmed = _trim(text)
    if len(trimmed) <= _SNIPPET:
        return trimmed
    cut = _SNIPPET
    while cut > 0 and not is_whitespace(trimmed[cut - 1]):
        cut -= 1
    if cut == 0:
        return trimmed[:_SNIPPET]
    return _trim(trimmed[:cut])


def _trim(text: str) -> str:
    start = 0
    end = len(text)
    while start < end and is_whitespace(text[start]):
        start += 1
    while end > start and is_whitespace(text[end - 1]):
        end -= 1
    return text[start:end]


def _expect_lines(expect: Mapping[str, Any], todo: bool) -> list[str]:
    lines: list[str] = []
    tables = expect.get("tables")
    if isinstance(tables, list) and tables:
        lines.append("  tables:")
        for table in tables:
            if isinstance(table, dict):
                lines.extend(_table_lines(table, todo))
    texts = expect.get("text_contains")
    if isinstance(texts, list) and texts:
        lines.append("  text_contains:")
        for text in texts:
            if isinstance(text, str) and text:
                lines.append(f"    - {_quote(text)}{_mark(todo)}")
    pairs = expect.get("reading_order")
    if isinstance(pairs, list) and pairs:
        lines.append("  reading_order:")
        for pair in pairs:
            if isinstance(pair, list) and len(pair) == 2:
                lines.append(f"    - - {_quote(str(pair[0]))}{_mark(todo)}")
                lines.append(f"      - {_quote(str(pair[1]))}{_mark(todo)}")
    fields = expect.get("fields")
    if isinstance(fields, dict) and fields:
        lines.append("  fields:")
        for key, value in fields.items():
            lines.append(f"    {_field_key(str(key))}: {_scalar(value)}{_mark(todo)}")
    return lines


def _table_lines(table: Mapping[str, Any], todo: bool) -> list[str]:
    lines = [f"    - page: {_scalar(table.get('page'))}{_mark(todo)}"]
    header = table.get("header")
    if isinstance(header, list):
        lines.append("      header:")
        for cell in header:
            lines.append(f"        - {_quote(str(cell))}{_mark(todo)}")
    if "rows" in table:
        lines.append(f"      rows: {_scalar(table.get('rows'))}{_mark(todo)}")
    if "cols" in table:
        lines.append(f"      cols: {_scalar(table.get('cols'))}{_mark(todo)}")
    cells = table.get("cells")
    if isinstance(cells, list) and cells:
        lines.append("      cells:")
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            lines.append(f"        - row: {_scalar(cell.get('row'))}{_mark(todo)}")
            lines.append(f"          col: {_scalar(cell.get('col'))}{_mark(todo)}")
            lines.append(f"          value: {_quote(str(cell.get('value', '')))}{_mark(todo)}")
    return lines


def _mark(todo: bool) -> str:
    return _TODO if todo else ""


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if math.isfinite(value) and value == math.trunc(value) and abs(value) <= 2**53:
            return str(int(value))
        return json.dumps(value)
    if isinstance(value, str):
        return _quote(value)
    return _quote(str(value))


def _field_key(key: str) -> str:
    if _plain_key(key):
        return key
    return _quote(key)


def _plain_key(key: str) -> bool:
    if key == "" or not _ascii_letter(key[0]):
        return False
    return all(_ascii_letter(char) or _ascii_digit(char) or char == "_" for char in key)


def _ascii_letter(char: str) -> bool:
    return ("A" <= char <= "Z") or ("a" <= char <= "z")


def _ascii_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _write_draft(source: Path, parser: str, out: Path | None, config: Path) -> Path:
    if not source.is_file():
        raise LabelError(f"{source}: file not found. Pass the source document to label.")
    options = _parser_options(config, parser)
    adapter = resolve(parser, options)
    from docassay.adapters.runner_support import ensure_available

    ensure_available([(parser, adapter)])
    document = run_parser(adapter, source, parser)
    target = out if out is not None else Path("golden") / f"{source.stem}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    file_ref = Path(os.path.relpath(source.resolve(), target.resolve().parent)).as_posix()
    target.write_text(draft_yaml(document, file_ref=file_ref, doc_id=source.stem), encoding="utf-8")
    return target


def _parser_options(config: Path, parser: str) -> dict[str, Any]:
    if not config.is_file():
        return {}
    loaded = load_config(config)
    for item in loaded.parsers:
        if item.id == parser:
            return dict(item.options or {})
    return {}


def _load_model(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise LabelError(f"{path}: could not read the golden file ({err}).") from err
    try:
        loaded = yaml.load(text, Loader=CoreLoader)
    except yaml.YAMLError as err:
        raise LabelError(f"{path}: invalid YAML: {err}. Fix the golden file.") from err
    if not isinstance(loaded, dict):
        raise LabelError(f"{path}: golden file must be a mapping. Fix the golden file.")
    try:
        GoldenFile.model_validate(loaded)
    except ValidationError as err:
        detail = err.errors()[0]["msg"] if err.errors() else "invalid golden file"
        raise LabelError(f"{path}: {detail}. Fix the golden file.") from err
    return loaded


def _confirmed_yaml(loaded: object) -> str:
    if not isinstance(loaded, dict):
        raise LabelError("save body must be a golden file object.")
    try:
        GoldenFile.model_validate(loaded)
    except ValidationError as err:
        detail = err.errors()[0]["msg"] if err.errors() else "invalid golden file"
        raise LabelError(f"could not save: {detail}. Fix the values and retry.") from err
    return render_yaml(loaded, todo=False)


def _source_path(golden_path: Path, model: Mapping[str, Any]) -> Path:
    file_ref = model.get("file")
    if not isinstance(file_ref, str) or file_ref == "":
        return golden_path
    return (golden_path.parent / file_ref).resolve()


def _send(handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


def _send_source(handler: BaseHTTPRequestHandler, source: Path) -> None:
    if not source.is_file():
        _send(
            handler,
            404,
            "text/plain; charset=utf-8",
            f"{source}: source file not found.\n".encode(),
        )
        return
    content_type = (
        "application/pdf" if source.suffix.lower() == ".pdf" else "application/octet-stream"
    )
    _send(handler, 200, content_type, source.read_bytes())


class _Flag:
    def __init__(self) -> None:
        self._done = threading.Event()

    def set(self) -> None:
        self._done.set()

    def is_set(self) -> bool:
        return self._done.is_set()


def _serve_until_done(httpd: ThreadingHTTPServer, done: _Flag) -> None:
    while not done.is_set():
        httpd.handle_request()
