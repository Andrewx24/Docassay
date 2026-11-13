"""Runtime verification. Python and TypeScript share spec/fixtures/verify/cases.json."""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

from docassay import verify
from docassay.models.document import Document
from docassay.verify import FieldCheck, _DATE_RE, _NUMBER_RE

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "spec" / "fixtures" / "verify" / "cases.json"
REQUIRED = {
    "uncited",
    "bad-page",
    "block-wrong-page",
    "table",
    "bbox-overlap",
    "page-only",
    "number-currency",
    "number-missing",
    "date-other-format",
    "hyphen-string",
    "boolean",
}


def test_fixture_cases_match() -> None:
    loaded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document = loaded["document"]
    cases = loaded["cases"]
    assert isinstance(cases, list)
    names = {case["name"] for case in cases}
    assert REQUIRED <= names
    assert len(cases) >= 20
    for case in cases:
        result = verify(document, case["fields"])
        expect = case["checks"]
        assert [check.to_json() for check in result.checks] == expect
        assert result.ok is case["ok"]
        assert [check.to_json() for check in result.unverified] == [
            check for check in expect if check["status"] != "verified"
        ]


def test_document_model_and_derived_pages() -> None:
    doc = Document.model_validate(
        {
            "specVersion": "1.0.0",
            "source": {"path": "a.pdf", "sha256": "ab" * 32, "mime": "application/pdf"},
            "meta": {"parser": "file", "parserVersion": "0", "adapterVersion": "0"},
            "pages": [{"number": 1}],
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "text": "Total $4,120.00",
                    "page": 1,
                    "bbox": [0, 0, 10, 10],
                }
            ],
            "tables": [],
        }
    )
    result = verify(doc, {"total": {"value": 4120, "citations": [{"page": 1, "blockId": "b1"}]}})
    assert result.ok is True
    bare = {"blocks": [{"id": "b", "text": "Hello", "page": 1}], "tables": []}
    assert verify(bare, {"a": {"value": "Hello", "citations": [{"page": 1, "blockId": "b"}]}}).ok


def test_malformed_input_does_not_raise() -> None:
    class Boom:
        @property
        def pages(self) -> list[object]:
            raise RuntimeError("boom")

    blown = verify(Boom(), {"a": {"value": "x", "citations": [{"page": 1}]}})
    assert blown.checks[0].status == "citation_invalid"
    assert blown.checks[0].reason is not None
    assert "could not be read" in blown.checks[0].reason
    assert verify(None, None).checks[0].status == "citation_invalid"
    assert verify([], {"a": 1}).checks[0].reason is not None
    doc = {
        "pages": [{"number": 1}],
        "blocks": [{"id": "b1", "text": "Hello", "page": 1}],
        "tables": [],
    }
    nested = verify(
        doc, {"a": {"value": {"nested": 1}, "citations": [{"page": 1, "blockId": "b1"}]}}
    )
    assert nested.checks[0].status == "citation_invalid"
    empty_id = verify(doc, {"a": {"value": "Hello", "citations": [{"page": 1, "blockId": ""}]}})
    assert empty_id.checks[0].status == "citation_invalid"
    not_a_list = verify(doc, {"a": {"value": "Hello", "citations": "b1"}})
    assert not_a_list.checks[0].status == "citation_invalid"
    assert verify(doc, ["nope"]).checks[0].status == "citation_invalid"


def test_empty_header_table_is_joined_from_rows() -> None:
    doc = {
        "pages": [{"number": 1}],
        "blocks": [],
        "tables": [{"id": "t", "page": 1, "header": [], "rows": [["Only"]]}],
    }
    result = verify(doc, {"a": {"value": "Only", "citations": [{"page": 1, "blockId": "t"}]}})
    assert result.ok is True


def test_regexes_are_compiled_at_import() -> None:
    assert isinstance(_NUMBER_RE, type(_DATE_RE))
    tree = ast.parse(
        (ROOT / "python" / "src" / "docassay" / "verify.py").read_text(encoding="utf-8")
    )
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                assert inner.func.attr != "compile"


def test_import_graph_stays_on_models_and_normalization() -> None:
    forbidden = {"cli", "adapters", "golden", "config", "yaml", "pyyaml"}
    start = ROOT / "python" / "src" / "docassay" / "verify.py"
    seen: set[Path] = set()

    def walk(path: Path) -> None:
        if path in seen or not path.is_file():
            return
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names.append(node.module)
            for name in names:
                parts = set(name.split("."))
                assert parts.isdisjoint(forbidden), name
                if name == "docassay" or name.startswith("docassay."):
                    relative = Path(*name.split("."))
                    candidate = ROOT / "python" / "src" / relative.with_suffix(".py")
                    package = ROOT / "python" / "src" / relative / "__init__.py"
                    walk(candidate if candidate.is_file() else package)

    walk(start)
    assert any(path.name == "verify.py" for path in seen)


def test_benchmark_thousand_fields() -> None:
    pages = [{"number": index} for index in range(1, 51)]
    blocks = [
        {
            "id": f"b{index}",
            "text": "alpha November 1, 2026 total $4,120.00 end",
            "page": index,
            "bbox": [0, 0, 1, 1],
        }
        for index in range(1, 51)
    ]
    doc = {"pages": pages, "blocks": blocks, "tables": []}
    fields: dict[str, object] = {}
    for index in range(1000):
        page = (index % 50) + 1
        citation = [{"page": page, "blockId": f"b{page}"}]
        if index % 5 == 0:
            fields[f"f{index}"] = {"value": "2026-11-01", "citations": citation}
        elif index % 2 == 0:
            fields[f"f{index}"] = {"value": "alpha", "citations": citation}
        else:
            fields[f"f{index}"] = {"value": 4120, "citations": citation}
    started = time.perf_counter()
    result = verify(doc, fields)
    elapsed = time.perf_counter() - started
    assert result.ok is True
    assert len(result.checks) == 1000
    assert elapsed < 1.0


def test_field_check_example_shape() -> None:
    check = FieldCheck("total", "value_not_found", reason="value not found in span")
    assert check.to_json()["status"] == "value_not_found"
