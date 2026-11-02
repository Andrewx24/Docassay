"""Chunk-quality score.

Example:
    from docassay.scoring.chunks import score_chunks

    score_chunks(document, golden.expect.chunks, ScoreOptions())
"""

from __future__ import annotations

from docassay.models.document import Chunk, Document, Table
from docassay.models.golden import ChunkExpect, HeadingPath
from docassay.normalize import norm_text, round4
from docassay.scoring import Failure, MetricResult, ScoreOptions
from docassay.tokenize import token_count


def score_chunks(
    doc: Document,
    expect: ChunkExpect,
    opts: ScoreOptions | None = None,
) -> MetricResult:
    """Score chunk size, split tables, and heading paths.

    Absent ``doc.chunks`` scores 0 with ``"parser produced no chunks"``.
    Otherwise each present rule adds checks and the score is the fraction
    that pass, rounded to 4 decimals. ``min_tokens`` skips the last chunk.
    ``chunk.tokens`` is used when it is set. ``extra["tokens"]`` is the min,
    median, and max of those counts and does not change the score. An even
    count uses the mean of the two central values. No checks scores 1.

    Example:
        score_chunks(document, expectations, ScoreOptions(tokenizer="words"))
    """
    options = opts or ScoreOptions()
    if doc.chunks is None:
        return MetricResult(
            score=round4(0.0),
            failures=[Failure(metric="chunks", check="chunks", reason="parser produced no chunks")],
        )
    chunks = doc.chunks
    counts = [_tokens(chunk, options.tokenizer) for chunk in chunks]
    failures: list[Failure] = []
    passed = 0
    total = 0
    if expect.max_tokens is not None:
        for index, count in enumerate(counts):
            total += 1
            if count <= expect.max_tokens:
                passed += 1
                continue
            failures.append(
                _failure(
                    f"chunks[{index}].max_tokens",
                    "chunk exceeds max_tokens",
                    expect.max_tokens,
                    count,
                )
            )
    if expect.min_tokens is not None:
        for index, count in enumerate(counts[:-1]):
            total += 1
            if count >= expect.min_tokens:
                passed += 1
                continue
            failures.append(
                _failure(
                    f"chunks[{index}].min_tokens",
                    "chunk below min_tokens",
                    expect.min_tokens,
                    count,
                )
            )
    if expect.no_split_tables:
        for index, table in enumerate(doc.tables):
            total += 1
            owners = [chunk for chunk in chunks if table.id in _block_ids(chunk)]
            cells = _header_cells(table)
            if len(owners) <= 1 or all(_contains_cells(chunk, cells) for chunk in owners):
                passed += 1
                continue
            failures.append(
                _failure(
                    f"tables[{index}]",
                    "table split without repeated header",
                    cells,
                    [chunk.id for chunk in owners],
                )
            )
    if expect.heading_paths:
        for index, item in enumerate(expect.heading_paths):
            total += 1
            failure = _heading(chunks, index, item)
            if failure is None:
                passed += 1
                continue
            failures.append(failure)
    score = round4(1.0) if total == 0 else round4(passed / total)
    extra: dict[str, object] | None = None
    if counts:
        extra = {}
        extra["tokens"] = _distribution(counts)
    return MetricResult(score=score, failures=failures, extra=extra)


def _tokens(chunk: Chunk, tokenizer: str) -> int:
    if chunk.tokens is not None:
        return chunk.tokens
    return token_count(chunk.text, tokenizer)


def _block_ids(chunk: Chunk) -> list[str]:
    return [item.root for item in chunk.block_ids]


def _header_cells(table: Table) -> list[str]:
    if table.header is not None:
        return list(table.header)
    if table.rows:
        return list(table.rows[0])
    return []


def _contains_cells(chunk: Chunk, cells: list[str]) -> bool:
    haystack = norm_text(chunk.text)
    return all(norm_text(cell) in haystack for cell in cells)


def _heading(chunks: list[Chunk], index: int, item: HeadingPath) -> Failure | None:
    needle = norm_text(item.text)
    expected = [_plain(part) for part in item.path]
    for chunk in chunks:
        if needle not in norm_text(chunk.text):
            continue
        actual = (
            None if chunk.heading_path is None else [_plain(part) for part in chunk.heading_path]
        )
        if _norm_list(actual) == _norm_list(expected):
            return None
        return _failure(f"heading_paths[{index}]", "heading path does not match", expected, actual)
    return _failure(f"heading_paths[{index}]", "text not in any chunk", item.text, None)


def _norm_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [norm_text(value) for value in values]


def _plain(value: object) -> str:
    root = getattr(value, "root", None)
    if isinstance(root, str):
        return root
    if isinstance(value, str):
        return value
    raise TypeError(f"heading path item must be a string, got {type(value).__name__}.")


def _distribution(counts: list[int]) -> dict[str, object]:
    ordered = sorted(counts)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        median: int | float = ordered[middle]
    else:
        median = (ordered[middle - 1] + ordered[middle]) / 2
    return {"min": ordered[0], "median": median, "max": ordered[-1]}


def _failure(check: str, reason: str, expected: object, actual: object) -> Failure:
    return Failure(
        metric="chunks",
        check=check,
        reason=reason,
        expected=expected,
        actual=actual,
    )
