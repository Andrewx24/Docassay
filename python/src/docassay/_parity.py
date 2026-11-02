"""Score one shared fixture so Python and TypeScript can be diffed.

The JSON is ``scores`` (including ``overall``), ``failures``, and ``extra``
when the chunk scorer reports token stats. Numbers that are whole values are
written as integers so ``1.0`` and ``1`` compare equal.

Example:
    python -m docassay._parity tables-exact
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeGuard

import yaml
from pydantic import ValidationError

from docassay.golden import CoreLoader
from docassay.models.document import Document
from docassay.models.golden import GoldenFile
from docassay.scoring import Failure, ScoreOptions
from docassay.scoring.aggregate import document_scores
from docassay.scoring.chunks import score_chunks
from docassay.scoring.fields import score_fields
from docassay.scoring.order import score_order
from docassay.scoring.tables import score_tables
from docassay.scoring.text import score_text


class ParityError(Exception):
    """A fixture that cannot be scored, or two JSON files that cannot be read.

    Example:
        raise ParityError("unknown fixture 'missing'. Pass spec/fixtures/<case>.")
    """


class _Missing:
    """A JSON key or list item present on only one side of a diff."""


_MISSING = _Missing()


def score_case(folder: Path) -> dict[str, object]:
    """Score ``folder`` and return the shared parity object.

    ``folder`` contains ``document.json``, ``golden.yaml``, and an optional
    ``options.json``. Scores use the same functions as ``docassay run``.

    Example:
        score_case(Path("spec/fixtures/tables-exact"))["scores"]
    """
    document = _load_document(folder / "document.json")
    golden = _load_golden(folder / "golden.yaml")
    options = _load_options(folder / "options.json")
    raw: dict[str, float] = {}
    failures: list[Failure] = []
    extra: dict[str, object] | None = None
    expect = golden.expect
    if expect is not None:
        if expect.tables is not None:
            result = score_tables(document, expect.tables, options)
            raw["tables"] = result.score
            failures.extend(result.failures)
        if expect.text_contains is not None:
            result = score_text(document, [item.root for item in expect.text_contains], options)
            raw["text"] = result.score
            failures.extend(result.failures)
        if expect.reading_order is not None:
            pairs = [(item.root[0].root, item.root[1].root) for item in expect.reading_order]
            result = score_order(document, pairs, options)
            raw["order"] = result.score
            failures.extend(result.failures)
        if expect.fields is not None:
            result = score_fields(document, expect.fields, options)
            raw["fields"] = result.score
            failures.extend(result.failures)
        if expect.chunks is not None:
            result = score_chunks(document, expect.chunks, options)
            raw["chunks"] = result.score
            failures.extend(result.failures)
            if result.extra is not None:
                extra = result.extra
    payload: dict[str, object] = {
        "scores": document_scores(raw),
        "failures": [_ready(failure.to_json()) for failure in failures],
    }
    if extra is not None:
        payload["extra"] = _ready(extra)
    return payload


def render(payload: Mapping[str, object]) -> str:
    """Return ``payload`` as indented JSON with a trailing newline.

    Example:
        render({"scores": {"tables": 1}, "failures": []})
    """
    return json.dumps(_ready(dict(payload)), indent=2, ensure_ascii=False) + "\n"


def diff_json(
    case: str,
    left: object,
    right: object,
    ignore: Sequence[str] = (),
) -> list[str]:
    """Return readable mismatches between two parsed JSON values.

    Numbers match when ``round4`` gives the same 4-decimal value (tolerance
    ``1e-4``). ``ignore`` drops those top-level keys before comparing.
    ``runId`` and ``createdAt`` are ignored for scorecards.

    Example:
        diff_json("tables-exact", {"scores": {"tables": 1}}, {"scores": {"tables": 0}})
    """
    py_value = left
    js_value = right
    if ignore and isinstance(left, dict) and isinstance(right, dict):
        py_value = {key: value for key, value in left.items() if key not in ignore}
        js_value = {key: value for key, value in right.items() if key not in ignore}
    rows: list[str] = []
    _walk(case, "", py_value, js_value, rows)
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    """Print one case, or diff two JSON files. Returns 0 on success.

    Example:
        main(["tables-exact"])
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ["--diff"]:
            return _diff_files(args[1:], case=None, ignore=())
        if args[:1] == ["--diff-scorecard"]:
            return _diff_files(args[1:], case="golden", ignore=("runId", "createdAt"))
        if len(args) != 1:
            raise ParityError(
                "usage: python -m docassay._parity <case>\n"
                "       python -m docassay._parity --diff <case> <py.json> <js.json>\n"
                "       python -m docassay._parity --diff-scorecard <py.json> <js.json>"
            )
        sys.stdout.write(render(score_case(resolve_case(args[0]))))
    except ParityError as err:
        sys.stderr.write(f"{err}\n")
        return 1
    return 0


def resolve_case(argument: str) -> Path:
    """Return the fixture directory named by ``argument``.

    A directory that already contains ``document.json`` is used as-is.
    Otherwise ``argument`` is a folder name under ``spec/fixtures/``.

    Example:
        resolve_case("tables-exact").name == "tables-exact"
    """
    raw = Path(argument)
    if raw.is_dir() and (raw / "document.json").is_file():
        return raw.resolve()
    candidate = repo_root() / "spec" / "fixtures" / argument
    if candidate.is_dir() and (candidate / "document.json").is_file():
        return candidate
    raise ParityError(
        f"unknown fixture {argument!r}. "
        "Pass a spec/fixtures/<case> directory that contains document.json."
    )


def repo_root() -> Path:
    """Return the checkout that contains ``spec/fixtures``.

    Example:
        (repo_root() / "spec" / "fixtures").is_dir()
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "spec" / "fixtures").is_dir() and (parent / "python").is_dir():
            return parent
    raise ParityError(
        "could not find the repo root from the parity module. Run it from a docassay checkout."
    )


def _diff_files(args: Sequence[str], case: str | None, ignore: Sequence[str]) -> int:
    if case is None:
        if len(args) != 3:
            raise ParityError("usage: python -m docassay._parity --diff <case> <py.json> <js.json>")
        label, py_path, js_path = args
    else:
        if len(args) != 2:
            raise ParityError(
                "usage: python -m docassay._parity --diff-scorecard <py.json> <js.json>"
            )
        label = case
        py_path, js_path = args
    rows = diff_json(label, _read_json(Path(py_path)), _read_json(Path(js_path)), ignore)
    if rows:
        sys.stdout.write("\n\n".join(rows) + "\n")
        return 1
    return 0


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ParityError(
            f"{path}: could not read JSON ({err}). Pass the path printed above."
        ) from err
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise ParityError(
            f"{path}:{err.lineno}:{err.colno}: invalid JSON: {err.msg}. "
            "Fix the file or rerun the scorer."
        ) from err


def _load_document(path: Path) -> Document:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ParityError(
            f"{path}: could not read the document ({err}). Add document.json to the fixture."
        ) from err
    try:
        return Document.model_validate_json(text)
    except json.JSONDecodeError as err:
        raise ParityError(
            f"{path}:{err.lineno}:{err.colno}: invalid JSON: {err.msg}. Fix document.json."
        ) from err
    except ValidationError as err:
        raise ParityError(_model_message(path, err, "Fix document.json.")) from err


def _load_golden(path: Path) -> GoldenFile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ParityError(
            f"{path}: could not read the golden file ({err}). Add golden.yaml to the fixture."
        ) from err
    try:
        loaded = yaml.load(text, Loader=CoreLoader)
    except yaml.YAMLError as err:
        raise ParityError(_yaml_message(path, err)) from err
    if not isinstance(loaded, dict):
        raise ParityError(f"{path}: golden file must be a mapping. Fix golden.yaml.")
    try:
        return GoldenFile.model_validate(loaded)
    except ValidationError as err:
        raise ParityError(_model_message(path, err, "Fix golden.yaml.")) from err


def _load_options(path: Path) -> ScoreOptions:
    if not path.exists():
        return ScoreOptions()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ParityError(f"{path}: could not read options ({err}). Fix options.json.") from err
    except json.JSONDecodeError as err:
        raise ParityError(
            f"{path}:{err.lineno}:{err.colno}: invalid JSON: {err.msg}. Fix options.json."
        ) from err
    if not isinstance(loaded, dict):
        raise ParityError(f"{path}: options must be a JSON object. Fix options.json.")
    decimal = loaded.get("decimal", ".")
    order = loaded.get("dateOrder", loaded.get("date_order", "mdy"))
    tokenizer = loaded.get("tokenizer", "words")
    if decimal not in (".", ","):
        raise ParityError(f'{path}: decimal must be "." or ",". Fix options.json.')
    if order not in ("mdy", "dmy"):
        raise ParityError(f"{path}: dateOrder must be mdy or dmy. Fix options.json.")
    if not isinstance(tokenizer, str) or tokenizer == "":
        raise ParityError(f"{path}: tokenizer must be a non-empty string. Fix options.json.")
    return ScoreOptions(decimal=decimal, date_order=order, tokenizer=tokenizer)


def _model_message(path: Path, err: ValidationError, fix: str) -> str:
    errors = err.errors()
    if not errors:
        return f"{path}: value does not match the schema. {fix}"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "value does not match the schema"))
    if loc:
        return f"{path}: {loc}: {message}. {fix}"
    return f"{path}: {message}. {fix}"


def _yaml_message(path: Path, err: yaml.YAMLError) -> str:
    mark = getattr(err, "problem_mark", None)
    problem = getattr(err, "problem", None)
    detail = problem if isinstance(problem, str) and problem else str(err).split("\n", 1)[0]
    if mark is not None:
        return f"{path}:{mark.line + 1}:{mark.column + 1}: invalid YAML: {detail}. Fix golden.yaml."
    return f"{path}: invalid YAML: {detail}. Fix golden.yaml."


def _ready(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, dict):
        return {str(key): _ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_ready(item) for item in value]
    return str(value)


def _number(value: float) -> int | float:
    if math.isfinite(value) and value == math.trunc(value) and abs(value) <= 2**53:
        return int(value)
    return value


def _walk(case: str, path: str, left: object, right: object, rows: list[str]) -> None:
    if isinstance(left, bool) or isinstance(right, bool):
        if left is not right:
            rows.append(_line(case, path, left, right))
        return
    if _is_number(left) and _is_number(right):
        if not _same4(float(left), float(right)):
            rows.append(_line(case, path, left, right))
        return
    if isinstance(left, dict) and isinstance(right, dict):
        keys = list(left)
        for key in right:
            if key not in left:
                keys.append(key)
        for key in keys:
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                rows.append(_line(case, child, left.get(key, _MISSING), right.get(key, _MISSING)))
            else:
                _walk(case, child, left[key], right[key], rows)
        return
    if isinstance(left, list) and isinstance(right, list):
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            py_item: object = left[index] if index < len(left) else _MISSING
            js_item: object = right[index] if index < len(right) else _MISSING
            if isinstance(py_item, _Missing) or isinstance(js_item, _Missing):
                rows.append(_line(case, child, py_item, js_item))
            else:
                _walk(case, child, py_item, js_item, rows)
        return
    if left != right:
        rows.append(_line(case, path, left, right))


def _line(case: str, path: str, py_value: object, js_value: object) -> str:
    shown = path or "$"
    return f"case: {case}\npath: {shown}\npy: {_show(py_value)}\njs: {_show(js_value)}"


def _show(value: object) -> str:
    if isinstance(value, _Missing):
        return "<missing>"
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _same4(left: float, right: float) -> bool:
    if not math.isfinite(left) or not math.isfinite(right):
        return False
    return math.floor(left * 10000 + 0.5) == math.floor(right * 10000 + 0.5)


if __name__ == "__main__":
    raise SystemExit(main())
