"""Score pre-parsed Document JSON or Markdown. No network and no API key.

Looks in ``options.dir`` for ``<golden id>.json``, then ``<golden id>.md``.
The golden id is the stem of the path passed to :meth:`FileAdapter.fetch`.

Example:
    from docassay.adapters.file import FileAdapter

    adapter = FileAdapter(None, {"dir": "out/parsed"})
    raw = adapter.fetch(Path("files/invoice-acme.pdf"))
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from jsonschema.validators import Draft202012Validator
from pydantic import ValidationError

from docassay.adapters.base import AdapterError, RawResult
from docassay.adapters.runner_support import package_version
from docassay.models.document import Block, Document, Meta, Page, Source, Table, Type

_VALIDATOR: Draft202012Validator | None = None


class FileAdapter:
    """Read parser output a pipeline already wrote to disk.

    ``cacheable`` is false: the output is already a file, so the parser cache
    must not store another copy. ``cache_identity`` is ``{"dir": <dir>}``.

    Example:
        identity = FileAdapter(None, {"dir": "out/parsed"}).cache_identity()
    """

    id: ClassVar[str] = "file"
    variants: ClassVar[tuple[str, ...]] = ()
    cacheable: ClassVar[bool] = False

    def __init__(self, variant: str | None, options: dict[str, Any]) -> None:
        self.variant = variant
        directory = options.get("dir")
        self.directory = directory if isinstance(directory, str) else ""
        self.options = options

    def available(self) -> tuple[bool, str]:
        """Return whether ``options.dir`` is set.

        Example:
            ok, reason = FileAdapter(None, {}).available()
        """
        if not self.directory:
            return (
                False,
                "file adapter requires options.dir, a folder of <golden id>.json or .md files",
            )
        return True, ""

    def cache_identity(self) -> dict[str, Any]:
        """Return ``{"dir": directory}``. The file adapter is not cached.

        Example:
            FileAdapter(None, {"dir": "out/parsed"}).cache_identity()
        """
        return {"dir": self.directory}

    def fetch(self, path: Path) -> RawResult:
        """Load ``<dir>/<path stem>.json`` or, if that is missing, the ``.md`` file.

        Example:
            raw = FileAdapter(None, {"dir": "out"}).fetch(Path("files/example.pdf"))
        """
        if not self.directory:
            raise AdapterError(
                "file adapter requires options.dir, a folder of <golden id>.json or .md files"
            )
        golden_id = path.stem
        root = Path(self.directory)
        json_path = root / f"{golden_id}.json"
        markdown_path = root / f"{golden_id}.md"
        if json_path.is_file():
            return _read_json(json_path)
        if markdown_path.is_file():
            return RawResult(
                payload={"markdown": _read_text(markdown_path)},
                parser_version="file",
                latency_ms=0,
                cost_usd=0,
            )
        raise AdapterError(f"no output for {golden_id} in {self.directory}")

    def to_document(self, raw: RawResult, source: Source) -> Document:
        """Turn fetched JSON or Markdown into a Document.

        JSON is validated against ``document.schema.json`` and used as-is,
        except ``source`` is the source the runner computed. Markdown becomes
        headings, paragraphs, list items, and pipe tables.

        Example:
            document = adapter.to_document(raw, source)
        """
        payload = raw.payload
        if isinstance(payload, dict) and "specVersion" in payload:
            _require_valid(payload, "output")
            try:
                document = Document.model_validate(payload)
            except ValidationError as err:
                detail = err.errors()[0]["msg"] if err.errors() else "invalid document"
                raise AdapterError(f"output does not match document.schema.json: {detail}") from err
            document.source = source
            return document
        if isinstance(payload, dict) and isinstance(payload.get("markdown"), str):
            return markdown_to_document(payload["markdown"], source)
        raise AdapterError("file adapter payload must be a Document or markdown text")


def markdown_to_document(text: str, source: Source) -> Document:
    """Convert a small Markdown subset to a Document.

    ATX headings become ``heading`` blocks. Other text becomes ``paragraph``.
    ``-``, ``*``, ``+``, and ``1.`` items become ``list_item``. A GFM pipe table
    uses the first row as ``header`` and skips the alignment row. ``<!-- page: N -->``
    sets the page; otherwise the page is 1. Block ids are ``b1``, ``b2``, … and
    table ids are ``t1``, ``t2``, … in document order.

    Example:
        document = markdown_to_document("# Title\\n", source)
    """
    blocks, tables, highest = _parse_markdown(text)
    for number, block in enumerate(blocks, start=1):
        block.id = f"b{number}"
    return Document(
        spec_version="1.0.0",
        source=source,
        meta=Meta(parser="file", parser_version="file", adapter_version=package_version()),
        pages=[Page(number=number) for number in range(1, highest + 1)],
        blocks=blocks,
        tables=tables,
    )


def _parse_markdown(text: str) -> tuple[list[Block], list[Table], int]:
    lines = _split_lines(text)
    blocks: list[Block] = []
    tables: list[Table] = []
    paragraph: list[str] = []
    page = 1
    highest = 1
    index = 0
    while index < len(lines):
        line = lines[index]
        if _blank(line):
            _flush_paragraph(paragraph, blocks, page)
            index += 1
            continue
        marker = _page_marker(line)
        if marker is not None:
            _flush_paragraph(paragraph, blocks, page)
            page = marker
            highest = max(highest, page)
            index += 1
            continue
        heading = _heading(line)
        if heading is not None:
            _flush_paragraph(paragraph, blocks, page)
            level, body = heading
            blocks.append(_block(Type.heading, body, page, level))
            index += 1
            continue
        item = _list_item(line)
        if item is not None:
            _flush_paragraph(paragraph, blocks, page)
            blocks.append(_block(Type.list_item, item, page, None))
            index += 1
            continue
        if _starts_table(lines, index):
            _flush_paragraph(paragraph, blocks, page)
            table, index = _parse_table(lines, index, page, len(tables) + 1)
            tables.append(table)
            continue
        paragraph.append(_trim(line))
        index += 1
    _flush_paragraph(paragraph, blocks, page)
    return blocks, tables, highest


def _flush_paragraph(paragraph: list[str], blocks: list[Block], page: int) -> None:
    if not paragraph:
        return
    text = " ".join(part for part in paragraph if part)
    paragraph.clear()
    if text:
        blocks.append(_block(Type.paragraph, text, page, None))


def _block(kind: Type, text: str, page: int, level: int | None) -> Block:
    return Block(id="pending", type=kind, text=text, page=page, level=level)


def _parse_table(lines: list[str], index: int, page: int, number: int) -> tuple[Table, int]:
    header = _split_row(lines[index]) or []
    cursor = index + 2
    rows: list[list[str]] = []
    width = len(header)
    while cursor < len(lines):
        cells = _split_row(lines[cursor])
        if cells is None or _blank(lines[cursor]):
            break
        if _is_alignment(lines[cursor]):
            cursor += 1
            continue
        width = max(width, len(cells))
        rows.append(cells)
        cursor += 1
    table = Table(
        id=f"t{number}",
        page=page,
        header=_pad(header, width),
        rows=[_pad(row, width) for row in rows],
    )
    return table, cursor


def _starts_table(lines: list[str], index: int) -> bool:
    if _split_row(lines[index]) is None:
        return False
    if index + 1 >= len(lines):
        return False
    return _is_alignment(lines[index + 1])


def _split_row(line: str) -> list[str] | None:
    text = _trim(line)
    if not text.startswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append(_trim("".join(current)))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if not text.endswith("|"):
        cells.append(_trim("".join(current)))
    return cells


def _is_alignment(line: str) -> bool:
    cells = _split_row(line)
    if not cells:
        return False
    for cell in cells:
        body = cell
        if body.startswith(":"):
            body = body[1:]
        if body.endswith(":"):
            body = body[:-1]
        if not body or any(char != "-" for char in body):
            return False
    return True


def _pad(cells: list[str], width: int) -> list[str]:
    if len(cells) >= width:
        return cells
    return [*cells, *[""] * (width - len(cells))]


def _heading(line: str) -> tuple[int, str] | None:
    text = _trim(line)
    level = 0
    while level < len(text) and text[level] == "#":
        level += 1
    if level < 1 or level > 6 or level >= len(text) or text[level] != " ":
        return None
    body = _strip_closing_hashes(_trim(text[level + 1 :]))
    if not body:
        return None
    return level, body


def _strip_closing_hashes(text: str) -> str:
    end = len(text)
    while end > 0 and text[end - 1] == "#":
        end -= 1
    if end < len(text) and end > 0 and text[end - 1] == " ":
        return _trim(text[:end])
    return text


def _list_item(line: str) -> str | None:
    text = _trim_indent(line)
    for marker in ("- ", "* ", "+ "):
        if text.startswith(marker):
            return _trim(text[len(marker) :])
    digits = 0
    while digits < len(text) and _is_digit(text[digits]):
        digits += 1
    if digits == 0 or digits + 1 >= len(text):
        return None
    if text[digits] in ".)" and text[digits + 1] == " ":
        return _trim(text[digits + 2 :])
    return None


def _page_marker(line: str) -> int | None:
    text = _trim(line)
    if not text.startswith("<!--") or not text.endswith("-->"):
        return None
    inner = _trim(text[4:-3])
    if not inner.startswith("page:"):
        return None
    number = _trim(inner[5:])
    if not number or any(char < "0" or char > "9" for char in number):
        return None
    value = int(number)
    if value < 1:
        return None
    return value


def _read_json(path: Path) -> RawResult:
    try:
        payload = json.loads(_read_text(path))
    except json.JSONDecodeError as err:
        raise AdapterError(f"output {path.as_posix()} is not valid JSON: {err.msg}") from err
    if not isinstance(payload, dict):
        raise AdapterError(
            f"output {path.as_posix()} does not match document.schema.json: must be object"
        )
    _require_valid(payload, path.as_posix())
    return RawResult(payload=payload, parser_version="file", latency_ms=0, cost_usd=0)


def _require_valid(payload: dict[str, Any], label: str) -> None:
    errors = sorted(_validator().iter_errors(payload), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    first = errors[0]
    location = "/".join(str(part) for part in first.absolute_path) or "<root>"
    raise AdapterError(
        f"output {label} does not match document.schema.json: {location}: {first.message}"
    )


def _validator() -> Draft202012Validator:
    global _VALIDATOR
    if _VALIDATOR is None:
        schema_path = _schema_path()
        loaded = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise AdapterError(f"{schema_path.as_posix()} must be a JSON object")
        _VALIDATOR = Draft202012Validator(loaded)
    return _VALIDATOR


def _schema_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "spec" / "document.schema.json"
        if candidate.is_file():
            return candidate
    raise AdapterError("could not find spec/document.schema.json; run from a docassay checkout")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise AdapterError(f"output {path.as_posix()} is not valid UTF-8") from err
    except OSError as err:
        raise AdapterError(f"output {path.as_posix()} could not be read: {err.strerror}") from err


def _split_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n")


def _blank(line: str) -> bool:
    return _trim(line) == ""


def _trim(text: str) -> str:
    return text.strip(" \t")


def _is_digit(char: str) -> bool:
    return "0" <= char <= "9"


def _trim_indent(text: str) -> str:
    index = 0
    while index < len(text) and text[index] in " \t":
        index += 1
    return text[index:]
