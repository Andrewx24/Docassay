"""Load and validate golden files.

Example:
    from docassay import load_golden

    files = load_golden("golden")
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from jsonschema.exceptions import ValidationError
from jsonschema.validators import Draft202012Validator
from pydantic import ValidationError as ModelError

from docassay.models.golden import GoldenFile

_VALIDATOR: Draft202012Validator | None = None

_YAML_SUFFIXES = {".yaml", ".yml"}
_TODO = "# TODO verify"


class CoreLoader(yaml.SafeLoader):
    """YAML 1.2.2 core schema. Dates, yes/no, and on/off stay strings."""


CoreLoader.yaml_implicit_resolvers = {}


def _add_resolver(tag: str, pattern: str, first: str) -> None:
    yaml.add_implicit_resolver(tag, re.compile(pattern), list(first), Loader=CoreLoader)


_add_resolver("tag:yaml.org,2002:null", r"^(?:null|Null|NULL|~)$", "nN~")
_add_resolver("tag:yaml.org,2002:bool", r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF")
_add_resolver("tag:yaml.org,2002:int", r"^0o[0-7]+$", "0")
_add_resolver("tag:yaml.org,2002:int", r"^0x[0-9a-fA-F]+$", "0")
_add_resolver("tag:yaml.org,2002:int", r"^[-+]?[0-9]+$", "-+0123456789")
_add_resolver(
    "tag:yaml.org,2002:float",
    r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$",
    "-+.0123456789",
)
_add_resolver("tag:yaml.org,2002:float", r"^[-+]?\.(?:inf|Inf|INF)$", "-+.")
_add_resolver("tag:yaml.org,2002:float", r"^\.(?:nan|NaN|NAN)$", ".")


def _construct_int(loader: CoreLoader, node: yaml.Node) -> int:
    if not isinstance(node, yaml.ScalarNode):
        raise ConstructorError(None, None, "expected an integer scalar", node.start_mark)
    text = loader.construct_scalar(node)
    sign = 1
    if text.startswith("+"):
        text = text[1:]
    elif text.startswith("-"):
        sign = -1
        text = text[1:]
    if text.startswith("0o"):
        return sign * int(text[2:], 8)
    if text.startswith("0x"):
        return sign * int(text[2:], 16)
    return sign * int(text, 10)


def _construct_float(loader: CoreLoader, node: yaml.Node) -> float:
    if not isinstance(node, yaml.ScalarNode):
        raise ConstructorError(None, None, "expected a float scalar", node.start_mark)
    text = loader.construct_scalar(node)
    lower = text.lower()
    if lower == ".nan":
        return math.nan
    if lower in {".inf", "+.inf", "-.inf"}:
        if text.startswith("-"):
            return -math.inf
        return math.inf
    return float(text)


CoreLoader.add_constructor("tag:yaml.org,2002:int", _construct_int)
CoreLoader.add_constructor("tag:yaml.org,2002:float", _construct_float)


@dataclass(frozen=True)
class GoldenIssue:
    """One validation problem, located in a golden file.

    Example:
        print(GoldenIssue("golden/a.yaml", 3, 1, "file not found: doc.pdf"))
    """

    path: str
    line: int
    column: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.message}"


class GoldenError(Exception):
    """Raised by :func:`load_golden` when any golden file is invalid.

    Example:
        try:
            load_golden("golden")
        except GoldenError as err:
            print(err.issues[0])
    """

    def __init__(self, issues: list[GoldenIssue]) -> None:
        ordered = sorted(issues, key=_issue_key)
        self.issues: list[GoldenIssue] = ordered
        super().__init__("\n".join(str(issue) for issue in ordered))


def _issue_key(issue: GoldenIssue) -> tuple[str, int, int, str]:
    return (issue.path, issue.line, issue.column, issue.message)


def display_path(path: Path) -> str:
    """Return ``path`` relative to the working directory, with ``/`` separators.

    Example:
        display_path(Path("golden") / "a.yaml")
    """
    return Path(os.path.relpath(path.resolve(), Path.cwd().resolve())).as_posix()


def load_golden(path: str | Path) -> list[GoldenFile]:
    """Load a golden set and return its files sorted by id.

    ``path`` may be a directory of top-level ``*.yaml`` / ``*.yml`` files or one
    golden file. ``id`` defaults to the file stem. ``file`` is resolved relative
    to the golden file and stored relative to the set directory. Raises
    :class:`GoldenError` with every problem when validation fails.

    Example:
        files = load_golden("golden")
        print(files[0].id)
    """
    report = inspect_golden(path)
    if report.issues:
        raise GoldenError(report.issues)
    return report.files


def validate_command(path: str | Path, *, strict: bool = False) -> int:
    """Print golden-set problems and return the ``docassay validate`` exit code.

    Exit 0 when the set is valid. Exit 2 when any file is invalid. Exit 1 when
    ``strict`` is set and a file still contains ``# TODO verify`` but nothing
    else is wrong.

    Example:
        raise SystemExit(validate_command("golden", strict=True))
    """
    report = inspect_golden(path)
    if report.issues:
        for issue in report.issues:
            print(issue)
        return 2
    if strict and report.todos:
        for issue in report.todos:
            print(issue)
        return 1
    return 0


@dataclass(frozen=True)
class GoldenReport:
    """Files, validation issues, and ``# TODO verify`` marks from one inspection.

    Example:
        report = inspect_golden("golden")
        print(len(report.files), len(report.issues))
    """

    files: list[GoldenFile]
    issues: list[GoldenIssue]
    todos: list[GoldenIssue]


def inspect_golden(path: str | Path) -> GoldenReport:
    """Load ``path`` without raising, collecting every issue and TODO.

    Example:
        report = inspect_golden("golden")
    """
    target = Path(path)
    shown = display_path(target)
    if not target.exists():
        issue = GoldenIssue(shown, 1, 1, "path not found")
        return GoldenReport([], [issue], [])
    if target.is_file():
        if target.suffix.lower() not in _YAML_SUFFIXES:
            issue = GoldenIssue(shown, 1, 1, "expected a .yaml or .yml file")
            return GoldenReport([], [issue], [])
        paths = [target]
        set_dir = target.parent
    elif target.is_dir():
        try:
            entries = list(target.iterdir())
        except OSError as err:
            reason = err.strerror or "could not read directory"
            return GoldenReport([], [GoldenIssue(shown, 1, 1, reason)], [])
        paths = sorted(
            (item for item in entries if item.is_file() and item.suffix.lower() in _YAML_SUFFIXES),
            key=lambda item: item.name,
        )
        set_dir = target
    else:
        issue = GoldenIssue(shown, 1, 1, "path is not a golden file or directory")
        return GoldenReport([], [issue], [])

    issues: list[GoldenIssue] = []
    todos: list[GoldenIssue] = []
    files: list[GoldenFile] = []
    seen_ids: dict[str, str] = {}
    for golden_path in paths:
        loaded = _load_one(golden_path, set_dir)
        issues.extend(loaded.issues)
        todos.extend(loaded.todos)
        if loaded.golden_id is not None:
            previous = seen_ids.get(loaded.golden_id)
            shown = display_path(golden_path)
            if previous is not None:
                issues.append(
                    GoldenIssue(
                        shown,
                        loaded.id_line,
                        loaded.id_column,
                        f'duplicate id "{loaded.golden_id}" (also in {previous})',
                    )
                )
            else:
                seen_ids[loaded.golden_id] = shown
        if loaded.model is not None and not loaded.issues:
            files.append(loaded.model)
    files.sort(key=lambda item: item.id or "")
    return GoldenReport(files, sorted(issues, key=_issue_key), sorted(todos, key=_issue_key))


def canonical_golden_json(files: list[GoldenFile]) -> str:
    """Serialize golden files to the JSON both language ports must match.

    Integer-valued numbers are written without a decimal point. Missing
    optional fields are omitted; explicit null field values are kept.

    Example:
        text = canonical_golden_json(load_golden("golden"))
    """
    payload = [_canonicalize(item.model_dump(mode="json", exclude_none=True)) for item in files]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _canonicalize(value: object) -> object:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def _schema_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "spec" / "golden.schema.json"
        if candidate.is_file():
            return candidate
    raise GoldenError(
        [
            GoldenIssue(
                "spec/golden.schema.json",
                1,
                1,
                "could not find spec/golden.schema.json; run from a docassay checkout",
            )
        ]
    )


def _validator() -> Draft202012Validator:
    global _VALIDATOR
    if _VALIDATOR is None:
        loaded = json.loads(_schema_path().read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise GoldenError(
                [GoldenIssue(str(_schema_path()), 1, 1, "golden schema must be a JSON object")]
            )
        _VALIDATOR = Draft202012Validator(loaded)
    return _VALIDATOR


@dataclass
class _One:
    model: GoldenFile | None
    golden_id: str | None
    id_line: int
    id_column: int
    issues: list[GoldenIssue]
    todos: list[GoldenIssue]


def _empty(issues: list[GoldenIssue], todos: list[GoldenIssue]) -> _One:
    return _One(None, None, 1, 1, issues, todos)


def _load_one(golden_path: Path, set_dir: Path) -> _One:
    shown = display_path(golden_path)
    try:
        text = golden_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _empty([GoldenIssue(shown, 1, 1, "file is not valid UTF-8")], [])
    except OSError as err:
        reason = err.strerror or "could not read file"
        return _empty([GoldenIssue(shown, 1, 1, reason)], [])

    todos = _todo_issues(text, shown)
    try:
        node, data = _compose(text)
    except yaml.YAMLError as err:
        return _empty([_yaml_issue(shown, err)], todos)
    if not isinstance(node, yaml.MappingNode) or not isinstance(data, dict):
        line, column = _position(node)
        return _empty([GoldenIssue(shown, line, column, "golden file must be a mapping")], todos)

    issues = _duplicate_keys(node, shown)
    raw_file = data.get("file")
    original_file = raw_file if isinstance(raw_file, str) else ""
    _apply_identity(data, golden_path, set_dir, original_file)
    id_line, id_column = _id_position(node)
    raw_id = data.get("id")
    golden_id = raw_id if isinstance(raw_id, str) and raw_id else None
    issues.extend(_schema_issues(data, node, shown))
    issues.extend(_extra_issues(data, node, golden_path, original_file, shown))
    model: GoldenFile | None = None
    if not any(_is_schema_message(issue.message) for issue in issues):
        try:
            model = GoldenFile.model_validate(data)
        except ModelError as err:
            detail = (
                err.errors()[0]["msg"]
                if err.errors()
                else "value does not match the golden file model"
            )
            issues.append(GoldenIssue(shown, 1, 1, detail))
    return _One(model, golden_id, id_line, id_column, issues, todos)


def _is_schema_message(message: str) -> bool:
    return message.startswith(
        (
            "unknown property ",
            "missing property ",
            "must be ",
            "must not be empty",
            "must have at least ",
            "must have at most ",
            "schema violation ",
        )
    )


def _compose(text: str) -> tuple[yaml.Node | None, object]:
    loader = CoreLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return None, None
        return node, loader.construct_document(node)
    finally:
        loader.dispose()


def _yaml_issue(shown: str, err: yaml.YAMLError) -> GoldenIssue:
    mark = getattr(err, "problem_mark", None)
    problem = getattr(err, "problem", None)
    message = problem if isinstance(problem, str) and problem else str(err)
    message = message.split("\n", 1)[0]
    if mark is not None:
        return GoldenIssue(shown, mark.line + 1, mark.column + 1, f"invalid YAML: {message}")
    return GoldenIssue(shown, 1, 1, f"invalid YAML: {message}")


def _todo_issues(text: str, shown: str) -> list[GoldenIssue]:
    issues: list[GoldenIssue] = []
    for line_no, raw in enumerate(text.split("\n"), start=1):
        line = raw[:-1] if raw.endswith("\r") else raw
        column = line.find(_TODO)
        if column >= 0:
            issues.append(GoldenIssue(shown, line_no, column + 1, "unresolved TODO verify"))
    return issues


def _apply_identity(
    data: dict[object, object], golden_path: Path, set_dir: Path, original_file: str
) -> None:
    raw_id = data.get("id")
    if raw_id is None and "id" not in data:
        data["id"] = golden_path.stem
    if original_file:
        absolute = (golden_path.parent / original_file).resolve()
        data["file"] = Path(os.path.relpath(absolute, set_dir.resolve())).as_posix()


def _position(node: yaml.Node | None) -> tuple[int, int]:
    if node is None:
        return 1, 1
    mark = node.start_mark
    return mark.line + 1, mark.column + 1


def _id_position(node: yaml.MappingNode) -> tuple[int, int]:
    found = _mapping_value(node, "id")
    if found is not None:
        return _position(found)
    return _position(node)


def _mapping_value(node: yaml.Node, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node if isinstance(value_node, yaml.Node) else None
    return None


def _mapping_key(node: yaml.Node, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, _value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return key_node if isinstance(key_node, yaml.Node) else None
    return None


def _duplicate_keys(node: yaml.Node, shown: str) -> list[GoldenIssue]:
    issues: list[GoldenIssue] = []
    if isinstance(node, yaml.MappingNode):
        seen: set[object] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key = key_node.value
                if key in seen:
                    line, column = _position(key_node)
                    issues.append(GoldenIssue(shown, line, column, f'duplicate key "{key}"'))
                else:
                    seen.add(key)
            issues.extend(_duplicate_keys(value_node, shown))
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            issues.extend(_duplicate_keys(item, shown))
    return issues


def _schema_issues(data: dict[object, object], node: yaml.Node, shown: str) -> list[GoldenIssue]:
    issues: list[GoldenIssue] = []
    for error in _validator().iter_errors(data):
        if error.validator == "additionalProperties" and isinstance(error.instance, dict):
            allowed = error.schema.get("properties", {}) if isinstance(error.schema, dict) else {}
            for key in error.instance:
                if key not in allowed:
                    located = _node_at(node, list(error.absolute_path), str(key))
                    line, column = _position(located)
                    issues.append(GoldenIssue(shown, line, column, f'unknown property "{key}"'))
            continue
        line, column = _position(_node_at(node, list(error.absolute_path), None))
        issues.append(GoldenIssue(shown, line, column, _schema_message(error)))
    return issues


def _schema_message(error: ValidationError) -> str:
    keyword = error.validator
    if keyword == "required":
        message = error.message
        # 'file' is a required property
        if message.startswith("'") and "' " in message:
            name = message.split("'", 2)[1]
            return f'missing property "{name}"'
        return 'missing property "file"'
    if keyword == "type":
        return _type_message(error.validator_value)
    if keyword == "minLength":
        limit = error.validator_value
        if limit == 1:
            return "must not be empty"
        return f"must be at least {limit} characters"
    if keyword == "minimum":
        return f"must be >= {error.validator_value}"
    if keyword == "minItems":
        return f"must have at least {error.validator_value} items"
    if keyword == "maxItems":
        return f"must have at most {error.validator_value} items"
    if keyword == "oneOf":
        if list(error.absolute_path)[-1:] == ["col"]:
            return "must be a column index or a header name"
        return "must match one schema"
    return f"schema violation ({keyword})"


def _type_message(expected: object) -> str:
    if isinstance(expected, list):
        names = [str(item) for item in expected]
        if len(names) == 1:
            return f"must be {names[0]}"
        return "must be " + ", ".join(names[:-1]) + f", or {names[-1]}"
    return f"must be {expected}"


def _node_at(node: yaml.Node, parts: list[object], key: str | None) -> yaml.Node | None:
    current: yaml.Node | None = node
    for part in parts:
        if current is None:
            return None
        if isinstance(part, int) and isinstance(current, yaml.SequenceNode):
            if 0 <= part < len(current.value):
                current = current.value[part]
            else:
                return current
        elif isinstance(part, str) and isinstance(current, yaml.MappingNode):
            value = _mapping_value(current, part)
            if value is None:
                return current
            current = value
        else:
            return current
    if key is not None and current is not None:
        found = _mapping_key(current, key)
        if found is not None:
            return found
    return current


def _extra_issues(
    data: dict[object, object],
    node: yaml.Node,
    golden_path: Path,
    original: str,
    shown: str,
) -> list[GoldenIssue]:
    issues: list[GoldenIssue] = []
    if original:
        target = (golden_path.parent / original).resolve()
        located = _node_at(node, ["file"], None)
        line, column = _position(located)
        if not target.exists():
            issues.append(GoldenIssue(shown, line, column, f"file not found: {original}"))
        elif not target.is_file() or not _readable(target):
            issues.append(GoldenIssue(shown, line, column, f"file is not readable: {original}"))
    expect = data.get("expect")
    expect_node = _mapping_value(node, "expect")
    if isinstance(expect, dict) and isinstance(expect_node, yaml.MappingNode):
        issues.extend(_table_issues(expect, expect_node, node, shown))
        issues.extend(_order_issues(expect, node, shown))
    return issues


def _readable(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def _table_issues(
    expect: dict[object, object], expect_node: yaml.MappingNode, root: yaml.Node, shown: str
) -> list[GoldenIssue]:
    del expect_node
    issues: list[GoldenIssue] = []
    tables = expect.get("tables")
    if not isinstance(tables, list):
        return issues
    for index, table in enumerate(tables):
        if not isinstance(table, dict):
            continue
        header = table.get("header")
        cells = table.get("cells")
        if not isinstance(cells, list):
            continue
        header_names = header if isinstance(header, list) else None
        for cell_index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                continue
            col = cell.get("col")
            located = _node_at(root, ["expect", "tables", index, "cells", cell_index, "col"], None)
            line, column = _position(located)
            if isinstance(col, str):
                if header_names is None or col not in header_names:
                    issues.append(
                        GoldenIssue(shown, line, column, f'column "{col}" is not in the header')
                    )
            elif isinstance(col, int) and not isinstance(col, bool) and header_names is not None:
                if col >= len(header_names):
                    issues.append(
                        GoldenIssue(
                            shown, line, column, f"column index {col} is outside the header"
                        )
                    )
    return issues


def _order_issues(expect: dict[object, object], root: yaml.Node, shown: str) -> list[GoldenIssue]:
    issues: list[GoldenIssue] = []
    pairs = expect.get("reading_order")
    if not isinstance(pairs, list):
        return issues
    for index, pair in enumerate(pairs):
        if (
            isinstance(pair, list)
            and len(pair) == 2
            and isinstance(pair[0], str)
            and isinstance(pair[1], str)
            and pair[0] != ""
            and pair[1] != ""
            and pair[0] == pair[1]
        ):
            located = _node_at(root, ["expect", "reading_order", index], None)
            line, column = _position(located)
            issues.append(
                GoldenIssue(
                    shown, line, column, "reading_order pair must contain two different strings"
                )
            )
    return issues
