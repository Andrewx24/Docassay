"""Write the generated half of the public golden set and copy it into both packages.

The three original examples stay as they are. This script adds the other 17
documents, ``SOURCES.md``, and the packaged copies. Run it from the repository
root. ``--check`` writes to a temporary directory and exits 1 when the
committed files differ.

Example:
    python golden/generate/build.py --check
"""

from __future__ import annotations

import filecmp
import io
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "golden"
PY_COPY = ROOT / "python" / "src" / "docassay" / "builtin_golden"
JS_COPY = ROOT / "js" / "builtin-golden"
PUBLIC = (
    "docassay.yaml",
    "README.md",
    "SOURCES.md",
    "RESULTS.md",
    "sets",
    "files",
    "parsed",
)
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class TableSpec:
    """One labeled table. ``cell_row`` is a 0-based body row."""

    page: int
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    cell_row: int
    cell_col: str
    cell_value: str


@dataclass(frozen=True)
class Generated:
    """One generated source file plus the markdown the file adapter scores."""

    doc_id: str
    filename: str
    kind: str
    tags: tuple[str, ...]
    note: str
    markdown: str
    text: tuple[str, ...]
    order: tuple[tuple[str, str], ...] = ()
    table: TableSpec | None = None


EXISTING = (
    (
        "invoice-acme",
        "invoice-acme.pdf",
        "pdf",
        "Placeholder PDF committed with the first example. The labeled text is parsed/invoice-acme.md.",
    ),
    (
        "lease",
        "lease.pdf",
        "pdf",
        "Placeholder PDF committed with the first example. The labeled text is parsed/lease.md.",
    ),
    (
        "memo",
        "memo.pdf",
        "pdf",
        "Placeholder PDF committed with the first example. The labeled text is parsed/memo.md.",
    ),
)


def _rows(names: tuple[str, ...], prefix: str) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (f"{prefix}{index + 1}", name, f"{(index + 1) * 1.25:.2f}")
        for index, name in enumerate(names)
    )


def _pipe(table: TableSpec) -> str:
    header = "| " + " | ".join(table.header) + " |"
    align = "| " + " | ".join("---" for _ in table.header) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in table.rows)
    return f"{header}\n{align}\n{body}\n"


def _markdown(
    title: str,
    paragraphs: tuple[str, ...],
    table: TableSpec | None,
    *,
    break_before_table: bool,
    page_break_after: int | None,
) -> str:
    parts = [f"# {title}", ""]
    for index, paragraph in enumerate(paragraphs):
        parts.append(paragraph)
        parts.append("")
        if page_break_after == index:
            parts.append("<!-- page: 2 -->")
            parts.append("")
    if table is not None:
        if break_before_table:
            parts.append("<!-- page: 2 -->")
            parts.append("")
        parts.append(_pipe(table).rstrip("\n"))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _doc(
    doc_id: str,
    filename: str,
    kind: str,
    tags: tuple[str, ...],
    note: str,
    title: str,
    paragraphs: tuple[str, ...],
    text: tuple[str, ...],
    order: tuple[tuple[str, str], ...] = (),
    table: TableSpec | None = None,
    *,
    break_before_table: bool = False,
    page_break_after: int | None = None,
) -> Generated:
    return Generated(
        doc_id=doc_id,
        filename=filename,
        kind=kind,
        tags=tags,
        note=note,
        markdown=_markdown(
            title,
            paragraphs,
            table,
            break_before_table=break_before_table,
            page_break_after=page_break_after,
        ),
        text=text,
        order=order,
        table=table,
    )


def documents() -> tuple[Generated, ...]:
    """Return the 17 generated documents in id order."""
    price = TableSpec(
        page=1,
        header=("Sku", "Name", "Price"),
        rows=_rows(
            (
                "Bolt",
                "Nut",
                "Washer",
                "Screw",
                "Hinge",
                "Latch",
                "Hook",
                "Clip",
                "Pin",
                "Rivet",
                "Stud",
                "Cap",
            ),
            "A",
        ),
        cell_row=6,
        cell_col="Name",
        cell_value="Hook",
    )
    catalog = TableSpec(
        page=2,
        header=("Sku", "Name", "Qty"),
        rows=_rows(
            (
                "Bracket",
                "Plate",
                "Rod",
                "Tube",
                "Sheet",
                "Bar",
                "Coil",
                "Gear",
                "Shaft",
                "Bush",
            ),
            "B",
        ),
        cell_row=4,
        cell_col="Name",
        cell_value="Sheet",
    )
    ledger = TableSpec(
        page=1,
        header=("Account", "Debit", "Credit"),
        rows=tuple((f"400{index}", f"{index * 10}.00", f"{index * 3}.00") for index in range(1, 9)),
        cell_row=3,
        cell_col="Account",
        cell_value="4004",
    )
    timesheet = TableSpec(
        page=1,
        header=("Day", "Hours"),
        rows=(("Mon", "8"), ("Tue", "8"), ("Wed", "7"), ("Thu", "8")),
        cell_row=2,
        cell_col="Hours",
        cell_value="7",
    )
    packing = TableSpec(
        page=1,
        header=("Item", "Qty"),
        rows=(("Box", "2"), ("Tape", "1"), ("Label", "4"), ("Wrap", "1"), ("Pad", "3")),
        cell_row=1,
        cell_col="Item",
        cell_value="Tape",
    )
    slip = TableSpec(
        page=1,
        header=("Item", "Qty"),
        rows=(("Crate", "1"), ("Manual", "1")),
        cell_row=0,
        cell_col="Item",
        cell_value="Crate",
    )
    made = "Generated by golden/generate/build.py. No third-party content."
    return (
        _doc(
            "scan-receipt",
            "scan-receipt.pdf",
            "pdf",
            ("scan", "pdf"),
            made + " Synthetic scan stand-in: a text PDF, not a photographed page.",
            "Scan receipt",
            ("Warehouse receipt 4401.", "Total 18.00."),
            ("Warehouse receipt 4401",),
        ),
        _doc(
            "scan-form",
            "scan-form.pdf",
            "pdf",
            ("scan", "pdf"),
            made + " Synthetic scan stand-in: a text PDF, not a photographed page.",
            "Scan form",
            ("Applicant name Jordan Lee.", "Form code GF-17."),
            ("Applicant name Jordan Lee",),
        ),
        _doc(
            "scan-contract",
            "scan-contract.pdf",
            "pdf",
            ("scan", "pdf"),
            made + " Synthetic scan stand-in: a text PDF, not a photographed page.",
            "Scan contract",
            ("This synthetic scan records the deposit.", "Signed on page one."),
            ("synthetic scan records the deposit",),
            (("records the deposit", "page one"),),
        ),
        _doc(
            "two-column",
            "two-column.pdf",
            "pdf",
            ("multi-column", "pdf"),
            made + " Two headings stand in for columns.",
            "Two columns",
            ("Alpha opens the left column.", "Omega closes the right column."),
            ("left column", "right column"),
            (("Alpha", "Omega"),),
        ),
        _doc(
            "newsletter",
            "newsletter.pdf",
            "pdf",
            ("multi-column", "pdf"),
            made + " Two headings stand in for columns.",
            "Newsletter",
            (
                "The city desk leads this newsletter.",
                "The sports desk follows the city desk.",
            ),
            ("city desk", "sports desk"),
            (("city desk", "sports desk"),),
        ),
        _doc(
            "paper-columns",
            "paper-columns.pdf",
            "pdf",
            ("multi-column", "pdf"),
            made + " Two headings stand in for columns.",
            "Paper columns",
            ("Column north starts the paper.", "Column south ends the paper."),
            ("Column north", "Column south"),
            (("Column north", "Column south"),),
        ),
        _doc(
            "price-list",
            "price-list.pdf",
            "pdf",
            ("dense-table", "pdf"),
            made + " Dense generated price table.",
            "Price list",
            ("Generated price list for the public set.",),
            ("Generated price list",),
            table=price,
        ),
        _doc(
            "catalog",
            "catalog.pdf",
            "pdf",
            ("dense-table", "pdf"),
            made + " Table is on page 2.",
            "Catalog",
            ("Catalog of generated parts.",),
            ("Catalog of generated parts",),
            (("Catalog of generated parts", "Sheet"),),
            table=catalog,
            break_before_table=True,
        ),
        _doc(
            "ledger",
            "ledger.xlsx",
            "xlsx",
            ("dense-table", "xlsx"),
            made + " Spreadsheet plus a committed markdown parse.",
            "Ledger",
            ("Generated ledger for account checks.",),
            ("Generated ledger",),
            table=ledger,
        ),
        _doc(
            "timesheet",
            "timesheet.xlsx",
            "xlsx",
            ("xlsx",),
            made + " Spreadsheet plus a committed markdown parse.",
            "Timesheet",
            ("Hours for the generated week.",),
            ("generated week",),
            table=timesheet,
        ),
        _doc(
            "packing-list",
            "packing-list.xlsx",
            "xlsx",
            ("xlsx",),
            made + " Spreadsheet plus a committed markdown parse.",
            "Packing list",
            ("Contents of the generated shipment.",),
            ("generated shipment",),
            table=packing,
        ),
        _doc(
            "invoice-beta",
            "invoice-beta.docx",
            "docx",
            ("docx",),
            made + " Word file plus a committed markdown parse.",
            "Invoice beta",
            ("Invoice beta for Northwind.", "Amount due 42.00."),
            ("Invoice beta", "Amount due 42.00"),
            (("Invoice beta", "Amount due"),),
        ),
        _doc(
            "memo-q3",
            "memo-q3.docx",
            "docx",
            ("docx",),
            made + " Word file plus a committed markdown parse.",
            "Memo q3",
            ("Please file the q3 notes.",),
            ("q3 notes",),
        ),
        _doc(
            "policy",
            "policy.docx",
            "docx",
            ("docx",),
            made + " Word file plus a committed markdown parse.",
            "Policy",
            (
                "The generated policy covers returns.",
                "Returns are accepted within 30 days.",
            ),
            ("generated policy", "30 days"),
            (("generated policy", "30 days"),),
        ),
        _doc(
            "statement",
            "statement.pdf",
            "pdf",
            ("pdf",),
            made + " Digital PDF with a page break.",
            "Statement",
            (
                "Opening balance on page one is 100.00.",
                "Closing balance on page two is 80.00.",
            ),
            ("Opening balance", "Closing balance"),
            (("page one", "page two"),),
            page_break_after=0,
        ),
        _doc(
            "form-facsimile",
            "form-facsimile.pdf",
            "pdf",
            ("form", "pdf"),
            made + " A generated form, not a government original.",
            "Form facsimile",
            ("Generated form facsimile number GF-200.", "Box 1 reads Example."),
            ("Generated form facsimile",),
        ),
        _doc(
            "packing-slip",
            "packing-slip.pdf",
            "pdf",
            ("pdf",),
            made + " Digital PDF with a short table.",
            "Packing slip",
            ("Packing slip for order 900.",),
            ("order 900",),
            table=slip,
        ),
    )


def _visible(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw in markdown.split("\n"):
        text = raw.strip(" \t")
        if text == "" or text.startswith("<!--"):
            continue
        if text.startswith("|"):
            cells = [cell.strip(" \t") for cell in text.strip("|").split("|")]
            if cells and all(set(cell) <= set("-: ") and "-" in cell for cell in cells):
                continue
            lines.append(" | ".join(cells))
            continue
        while text.startswith("#"):
            text = text[1:]
        text = text.strip(" \t")
        if text:
            lines.append(text)
    return lines


def _pdf(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 11 Tf", "72 740 Td"]
    for index, line in enumerate(lines[:45]):
        if index:
            commands.append("0 -16 Td")
        commands.append("(" + _pdf_escape(line) + ") Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        ),
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    parts = [header]
    offsets = [0]
    cursor = len(header)
    for obj in objects:
        offsets.append(cursor)
        parts.append(obj)
        cursor += len(obj)
    xref = [f"xref\n0 {len(offsets)}\n", "0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    trailer = f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{cursor}\n%%EOF\n"
    return b"".join(parts) + "".join(xref).encode("ascii") + trailer.encode("ascii")


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(parts):
            info = zipfile.ZipInfo(filename=name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            archive.writestr(info, parts[name])
    return buffer.getvalue()


def _docx(lines: list[str]) -> bytes:
    paragraphs = "".join(f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>" for line in lines)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>\n"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>\n"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>\n"
    )
    return _zip(
        {
            "[Content_Types].xml": types,
            "_rels/.rels": rels,
            "word/document.xml": document,
        }
    )


def _xlsx(lines: list[str]) -> bytes:
    rows: list[str] = []
    for index, line in enumerate(lines, start=1):
        cells = line.split(" | ") if " | " in line else [line]
        bits: list[str] = []
        for column, cell in enumerate(cells):
            letter = chr(ord("A") + column)
            bits.append(f'<c r="{letter}{index}" t="inlineStr"><is><t>{escape(cell)}</t></is></c>')
        rows.append(f'<row r="{index}">{"".join(bits)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(rows)}</sheetData></worksheet>\n"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>\n'
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>\n"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>\n"
    )
    book_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>\n"
    )
    return _zip(
        {
            "[Content_Types].xml": types,
            "_rels/.rels": rels,
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": book_rels,
            "xl/worksheets/sheet1.xml": sheet,
        }
    )


def _bytes(doc: Generated) -> bytes:
    lines = _visible(doc.markdown)
    if doc.kind == "pdf":
        return _pdf(lines)
    if doc.kind == "docx":
        return _docx(lines)
    if doc.kind == "xlsx":
        return _xlsx(lines)
    raise ValueError(f"unknown kind {doc.kind}")


def _yaml(doc: Generated) -> str:
    tags = ", ".join(doc.tags)
    lines = [
        f"id: {doc.doc_id}",
        f"file: ../files/{doc.filename}",
        f"tags: [{tags}]",
        "expect:",
        "  text_contains:",
    ]
    lines.extend(f'    - "{item}"' for item in doc.text)
    if doc.order:
        lines.append("  reading_order:")
        lines.extend(f'    - ["{before}", "{after}"]' for before, after in doc.order)
    if doc.table is not None:
        header = ", ".join(f'"{cell}"' for cell in doc.table.header)
        lines.extend(
            [
                "  tables:",
                f"    - page: {doc.table.page}",
                f"      header: [{header}]",
                f"      rows: {len(doc.table.rows)}",
                f"      cols: {len(doc.table.header)}",
                "      cells:",
                (
                    f"        - {{row: {doc.table.cell_row}, col: {doc.table.cell_col}, "
                    f'value: "{doc.table.cell_value}"}}'
                ),
            ]
        )
    return "\n".join(lines) + "\n"


def _sources(docs: tuple[Generated, ...]) -> str:
    lines = [
        "# Sources",
        "",
        "Every file in this golden set is generated for this repository and redistributed",
        "under the repository Apache-2.0 license. None of them is copied from a third party.",
        "The file adapter scores `parsed/<id>.md`. The bytes in `files/` are the source",
        "container for that text: a small PDF, DOCX, or XLSX written by",
        "`golden/generate/build.py`, except the three original placeholders below.",
        "",
        "Scans are synthetic text PDFs tagged `scan`. They are stand-ins for photographed",
        "pages, not images of real documents. `form-facsimile` is not a government form.",
        "",
        "Vendor scorecards are not invented here. `RESULTS.md` is the `file` adapter on",
        "this committed parse. Docling, LlamaParse, and Reducto scores belong in that",
        "file only after those adapters run and the cache is committed.",
        "",
        "| id | file | kind | redistribution |",
        "| --- | --- | --- | --- |",
    ]
    for doc_id, filename, kind, note in EXISTING:
        lines.append(f"| {doc_id} | files/{filename} | {kind} | {note} |")
    for doc in docs:
        lines.append(f"| {doc.doc_id} | files/{doc.filename} | {doc.kind} | {doc.note} |")
    lines.append("")
    return "\n".join(lines)


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


def write_generated(golden: Path, docs: tuple[Generated, ...]) -> None:
    """Write generated sources under ``golden`` without touching the original three."""
    for doc in docs:
        _write(golden / "files" / doc.filename, _bytes(doc))
        _write(golden / "parsed" / f"{doc.doc_id}.md", doc.markdown)
        _write(golden / "sets" / f"{doc.doc_id}.yaml", _yaml(doc))
    _write(golden / "SOURCES.md", _sources(docs))


def generated_paths(docs: tuple[Generated, ...]) -> tuple[str, ...]:
    """Return repo-relative paths this script owns."""
    paths = ["golden/SOURCES.md"]
    for doc in docs:
        paths.append(f"golden/files/{doc.filename}")
        paths.append(f"golden/parsed/{doc.doc_id}.md")
        paths.append(f"golden/sets/{doc.doc_id}.yaml")
    return tuple(sorted(paths))


def sync_packages(golden: Path, py_copy: Path, js_copy: Path) -> None:
    """Replace both packaged copies with the public golden files."""
    for dest in (py_copy, js_copy):
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for name in PUBLIC:
            source = golden / name
            if not source.exists():
                continue
            target = dest / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)


def _same(left: Path, right: Path) -> bool:
    if left.is_dir() and right.is_dir():
        comparison = filecmp.dircmp(left, right)
        if comparison.left_only or comparison.right_only or comparison.diff_files:
            return False
        return all(_same(left / name, right / name) for name in comparison.common_dirs)
    if left.is_file() and right.is_file():
        return filecmp.cmp(left, right, shallow=False)
    return False


def check() -> int:
    """Return 0 when committed generated files and package copies match the script."""
    docs = documents()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        staged = root / "golden"
        for relative in (
            "docassay.yaml",
            "README.md",
            "RESULTS.md",
            "sets/invoice-acme.yaml",
            "sets/lease.yaml",
            "sets/memo.yaml",
            "files/invoice-acme.pdf",
            "files/lease.pdf",
            "files/memo.pdf",
            "parsed/invoice-acme.md",
            "parsed/lease.md",
            "parsed/memo.md",
        ):
            source = GOLDEN / relative
            if source.is_file():
                _write(staged / relative, source.read_bytes())
        write_generated(staged, docs)
        for relative in generated_paths(docs):
            name = relative.removeprefix("golden/")
            if not _same(GOLDEN / name, staged / name):
                print(f"{relative} drifted from golden/generate/build.py")
                return 1
        sync_packages(staged, root / "py", root / "js")
        if not _same(PY_COPY, root / "py") or not _same(JS_COPY, root / "js"):
            print("packaged golden copies drifted from golden/")
            return 1
    print(f"golden set matches ({len(documents()) + len(EXISTING)} documents)")
    return 0


def main(argv: list[str]) -> int:
    """Write the set, or check it when ``--check`` is the only argument."""
    docs = documents()
    if argv == ["--check"]:
        return check()
    if argv:
        print("usage: python golden/generate/build.py [--check]")
        return 2
    write_generated(GOLDEN, docs)
    sync_packages(GOLDEN, PY_COPY, JS_COPY)
    print(f"wrote {len(docs)} generated documents into golden/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
