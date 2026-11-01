"""Table structure and cell accuracy.

Example:
    from docassay.scoring.tables import score_tables

    result = score_tables(document, golden.expect.tables or [], ScoreOptions())
"""

from __future__ import annotations

from collections.abc import Sequence

from docassay.models.document import Document, Table
from docassay.models.golden import CellExpect, TableExpect
from docassay.normalize import norm_text, round4, values_equal
from docassay.scoring import Failure, MetricResult, ScoreOptions

_HEADER_WEIGHT = 0.3
_SHAPE_WEIGHT = 0.2
_CELL_WEIGHT = 0.5


def score_tables(
    doc: Document,
    expect: Sequence[TableExpect],
    opts: ScoreOptions | None = None,
) -> MetricResult:
    """Score predicted tables against golden table expectations.

    Tables are matched in golden order. A labeled header matches the unused
    table on that page with the highest header similarity, and the first table
    wins a tie. Similarity below 0.5 does not match. With no header label, the
    highest cell score wins. The document score is the mean of the per-table
    scores, rounded to 4 decimals.

    Example:
        result = score_tables(document, expectations, ScoreOptions())
    """
    options = opts or ScoreOptions()
    if not expect:
        return MetricResult(score=round4(1.0), failures=[])
    used: set[int] = set()
    scores: list[float] = []
    failures: list[Failure] = []
    for index, item in enumerate(expect):
        score, item_failures = _score_one(doc.tables, used, index, item, options)
        scores.append(score)
        failures.extend(item_failures)
    return MetricResult(score=round4(sum(scores) / len(scores)), failures=failures)


def _score_one(
    tables: Sequence[Table],
    used: set[int],
    index: int,
    item: TableExpect,
    options: ScoreOptions,
) -> tuple[float, list[Failure]]:
    page = item.page if isinstance(item.page, int) else None
    candidates = [
        position
        for position, table in enumerate(tables)
        if position not in used and (page is None or table.page == page)
    ]
    check = f"tables[{index}]"
    if not candidates:
        reason = "no table found" if page is None else f"no table found on page {page}"
        return 0.0, [_failure(check, reason, page=page)]
    expected_header = item.header
    if expected_header is not None:
        best = max(
            candidates,
            key=lambda position: _header_sim(expected_header, tables[position]),
        )
        similarity = _header_sim(expected_header, tables[best])
        if similarity < 0.5:
            return 0.0, [
                _failure(
                    check,
                    "no table with matching header",
                    page=page,
                    expected=list(expected_header),
                    actual=_effective(tables[best])[0],
                    values=True,
                )
            ]
    else:
        best = max(
            candidates,
            key=lambda position: _cell_fraction(tables[position], item, options),
        )
    used.add(best)
    return _components(tables[best], index, item, options)


def _components(
    table: Table,
    index: int,
    item: TableExpect,
    options: ScoreOptions,
) -> tuple[float, list[Failure]]:
    header, body = _effective(table)
    page = item.page if isinstance(item.page, int) else None
    total = 0.0
    weight = 0.0
    failures: list[Failure] = []
    if item.header is not None:
        similarity = _header_sim(item.header, table)
        total += _HEADER_WEIGHT * similarity
        weight += _HEADER_WEIGHT
        if similarity < 1:
            failures.append(
                _failure(
                    f"tables[{index}].header",
                    "header does not match",
                    page=page,
                    expected=list(item.header),
                    actual=header,
                    values=True,
                )
            )
    shape = _shape(header, body, item)
    if shape is not None:
        total += _SHAPE_WEIGHT * shape
        weight += _SHAPE_WEIGHT
        if shape < 1:
            failures.append(
                _failure(
                    f"tables[{index}].shape",
                    "shape does not match",
                    page=page,
                    expected=_shape_expected(item),
                    actual=_shape_actual(header, body, item),
                    values=True,
                )
            )
    if item.cells is not None:
        fraction, cell_failures = _cells(header, body, index, item, options)
        total += _CELL_WEIGHT * fraction
        weight += _CELL_WEIGHT
        failures.extend(cell_failures)
    score = 1.0 if weight == 0 else total / weight
    return score, failures


def _header_sim(expected: Sequence[str], table: Table) -> float:
    actual, _body = _effective(table)
    return _multiset_sim(expected, actual)


def _multiset_sim(expected: Sequence[str], actual: Sequence[str]) -> float:
    if not expected and not actual:
        return 1.0
    denom = max(len(expected), len(actual))
    counts: dict[str, int] = {}
    for cell in expected:
        key = norm_text(cell)
        counts[key] = counts.get(key, 0) + 1
    matched = 0
    for cell in actual:
        key = norm_text(cell)
        have = counts.get(key, 0)
        if have > 0:
            matched += 1
            counts[key] = have - 1
    return matched / denom


def _effective(table: Table) -> tuple[list[str], list[list[str]]]:
    if table.header is not None:
        return list(table.header), list(table.rows)
    if not table.rows:
        return [], []
    return list(table.rows[0]), list(table.rows[1:])


def _shape(header: Sequence[str], body: Sequence[Sequence[str]], item: TableExpect) -> float | None:
    parts: list[float] = []
    if item.rows is not None:
        parts.append(_dimension(len(body), item.rows))
    if item.cols is not None:
        parts.append(_dimension(_actual_cols(header, body), item.cols))
    if not parts:
        return None
    return sum(parts) / len(parts)


def _dimension(actual: int, expected: int) -> float:
    if expected == 0:
        return 1.0 if actual == 0 else 0.0
    delta = abs(actual - expected) / expected
    if delta > 1:
        delta = 1.0
    return 1.0 - delta


def _actual_cols(header: Sequence[str], body: Sequence[Sequence[str]]) -> int:
    if header:
        return len(header)
    longest = 0
    for row in body:
        if len(row) > longest:
            longest = len(row)
    return longest


def _shape_expected(item: TableExpect) -> dict[str, int]:
    payload: dict[str, int] = {}
    if item.rows is not None:
        payload["rows"] = item.rows
    if item.cols is not None:
        payload["cols"] = item.cols
    return payload


def _shape_actual(
    header: Sequence[str], body: Sequence[Sequence[str]], item: TableExpect
) -> dict[str, int]:
    payload: dict[str, int] = {}
    if item.rows is not None:
        payload["rows"] = len(body)
    if item.cols is not None:
        payload["cols"] = _actual_cols(header, body)
    return payload


def _cell_fraction(table: Table, item: TableExpect, options: ScoreOptions) -> float:
    header, body = _effective(table)
    cells = item.cells or []
    if not cells:
        return 1.0
    passed = 0
    for cell in cells:
        if _read_cell(header, body, cell, item.header, options)[0]:
            passed += 1
    return passed / len(cells)


def _cells(
    header: Sequence[str],
    body: Sequence[Sequence[str]],
    index: int,
    item: TableExpect,
    options: ScoreOptions,
) -> tuple[float, list[Failure]]:
    cells = item.cells or []
    if not cells:
        return 1.0, []
    page = item.page if isinstance(item.page, int) else None
    failures: list[Failure] = []
    passed = 0
    for cell_index, cell in enumerate(cells):
        ok, actual = _read_cell(header, body, cell, item.header, options)
        if ok:
            passed += 1
            continue
        reason = "cell out of range" if actual is None else "value not found in matched table"
        failures.append(
            _failure(
                f"tables[{index}].cells[{cell_index}]",
                reason,
                page=page,
                expected=cell.value,
                actual=actual,
                values=True,
            )
        )
    return passed / len(cells), failures


def _read_cell(
    header: Sequence[str],
    body: Sequence[Sequence[str]],
    cell: CellExpect,
    expected_header: Sequence[str] | None,
    options: ScoreOptions,
) -> tuple[bool, str | None]:
    column = _resolve_col(_column(cell), header, expected_header)
    if (
        column is None
        or column < 0
        or cell.row < 0
        or cell.row >= len(body)
        or column >= len(body[cell.row])
    ):
        return False, None
    actual = body[cell.row][column]
    matched = values_equal(
        cell.value,
        actual,
        {"decimal": options.decimal, "date_order": options.date_order},
    )
    return matched, actual


def _resolve_col(
    col: int | str,
    header: Sequence[str],
    expected_header: Sequence[str] | None,
) -> int | None:
    if isinstance(col, int):
        return col
    target = norm_text(col)
    for index, name in enumerate(header):
        if norm_text(name) == target:
            return index
    if expected_header is not None:
        for index, name in enumerate(expected_header):
            if norm_text(name) == target:
                return index
    return None


def _column(cell: CellExpect) -> int | str:
    value = cell.col
    root = getattr(value, "root", value)
    if isinstance(root, bool) or not isinstance(root, int | str):
        raise TypeError(f"cell column must be a header name or index, got {value!r}")
    return root


def _failure(
    check: str,
    reason: str,
    *,
    page: int | None = None,
    expected: object | None = None,
    actual: object | None = None,
    values: bool = False,
) -> Failure:
    if values:
        return Failure(
            metric="tables",
            check=check,
            reason=reason,
            expected=expected,
            actual=actual,
            page=page,
        )
    return Failure(metric="tables", check=check, reason=reason, page=page)
