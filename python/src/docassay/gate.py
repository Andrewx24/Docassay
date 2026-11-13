"""Save a baseline and fail the run when scores regress.

Comparisons use scores rounded to 4 decimal places, so a baseline file
round-trips. Improvements never fail. ``docassay check`` exits 1 when any
check fails.

Example:
    from docassay.gate import compare, make_baseline

    text = make_baseline(scorecard, pinned_at=pinned)
    result = compare(scorecard, baseline, thresholds)
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from docassay.adapters.base import AdapterConfigError, AdapterUnavailable
from docassay.cache import CacheError, CachePolicy
from docassay.config import ConfigError, load_config
from docassay.golden import GoldenError
from docassay.models.baseline import Baseline
from docassay.models.config import Thresholds
from docassay.models.scorecard import Scorecard
from docassay.report.json import format_created
from docassay.runner import RunError, RunOptions, execute

_METRICS = ("tables", "text", "order", "fields", "chunks", "overall")
_MINUS = "\u2212"


class GateError(Exception):
    """A baseline file or scorecard that ``check`` cannot compare.

    Example:
        raise GateError("baseline.json:1:1: baseline not found. Run `docassay baseline`.")
    """

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class GateResult:
    """Rendered check output and the process exit code.

    Example:
        result = compare(scorecard, baseline, thresholds)
        result.exit_code == 0
    """

    exit_code: int
    text: str


def make_baseline(
    scorecard: Scorecard | Mapping[str, Any], *, pinned_at: datetime | None = None
) -> str:
    """Return ``scorecard`` as baseline JSON with failures removed.

    ``pinned_at`` is the ``pinnedAt`` timestamp. When it is omitted, the clock
    is UTC now. Keys are sorted and the file uses a 2-space indent.

    Example:
        make_baseline(scorecard, pinned_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    """
    data = _scorecard(scorecard, "scorecard")
    moment = pinned_at or datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "specVersion": data["specVersion"],
        "docassayVersion": data["docassayVersion"],
        "runId": data["runId"],
        "createdAt": _timestamp(data.get("createdAt"), "createdAt"),
        "pinnedAt": format_created(moment),
        "parsers": [_baseline_parser(parser) for parser in _parsers(data)],
    }
    try:
        Baseline.model_validate(payload)
    except ValidationError as err:
        detail = err.errors()[0]["msg"] if err.errors() else "invalid baseline"
        raise GateError(f"could not build the baseline: {detail}.") from err
    return _stable_json(payload)


def compare(
    scorecard: Scorecard | Mapping[str, Any],
    baseline: Baseline | Mapping[str, Any],
    thresholds: Thresholds | None = None,
    *,
    summary_only: bool = False,
) -> GateResult:
    """Compare ``scorecard`` to ``baseline`` and return the check text.

    A score fails when its 4-decimal value is lower than the baseline by more
    than the metric's ``drop``, or when it is below ``min``. ``summary_only``
    skips every per-document check. The text tells the reader to run
    ``docassay baseline`` after an intended change.

    Example:
        compare(scorecard, baseline, thresholds).exit_code
    """
    current = _scorecard(scorecard, "scorecard")
    reference = _baseline(baseline)
    lines: list[str] = []
    failures = 0
    warnings = 0
    seen: set[str] = set()
    reference_by_name = {str(parser["parser"]): parser for parser in _parsers(reference)}
    for parser in _parsers(current):
        name = str(parser["parser"])
        seen.add(name)
        previous = reference_by_name.get(name)
        if previous is None:
            lines.append(f"WARN  {name} · new parser, no baseline")
            warnings += 1
            continue
        failures += _summary_lines(lines, name, parser, previous, thresholds)
        if summary_only:
            continue
        doc_failures, doc_warnings = _document_lines(lines, name, parser, previous, thresholds)
        failures += doc_failures
        warnings += doc_warnings
        warnings += _missing_documents(lines, name, parser, previous)
    for parser in _parsers(reference):
        name = str(parser["parser"])
        if name not in seen:
            lines.append(f"WARN  {name} · missing from this run")
            warnings += 1
    footer = (
        f"{_count(failures, 'regression', 'regressions')} · "
        f"{_count(warnings, 'warning', 'warnings')} · "
        "run `docassay baseline` after an intended change"
    )
    body = ""
    if lines:
        body = "\n".join(lines) + "\n\n"
    return GateResult(exit_code=1 if failures else 0, text=body + footer + "\n")


def baseline_command(
    config: str | Path,
    *,
    baseline: str | Path | None = None,
    parsers: Sequence[str] = (),
    tags: Sequence[str] = (),
    no_cache: bool = False,
    refresh: bool = False,
    concurrency: int = 4,
    verbose: bool = False,
    pinned_at: datetime | None = None,
) -> int:
    """Run the suite and write ``.docassay/baseline.json``. Return the exit code.

    Example:
        raise SystemExit(baseline_command("docassay.yaml"))
    """
    try:
        path = Path(config)
        loaded = load_config(path)
        card = _execute(path, loaded, parsers, tags, no_cache, refresh, concurrency, verbose)
        target = _baseline_file(path, loaded.baseline, baseline)
        text = make_baseline(card, pinned_at=pinned_at)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except GateError as err:
        print(err)
        return err.exit_code
    except (RunError, ConfigError, GoldenError, AdapterConfigError, CacheError, OSError) as err:
        _print_os(err)
        return 2
    except AdapterUnavailable as err:
        print(err)
        return 3
    print(f"wrote {target.as_posix()}")
    return 0


def check_command(
    config: str | Path,
    *,
    baseline: str | Path | None = None,
    summary_only: bool = False,
    parsers: Sequence[str] = (),
    tags: Sequence[str] = (),
    no_cache: bool = False,
    refresh: bool = False,
    concurrency: int = 4,
    verbose: bool = False,
) -> int:
    """Run the suite, compare it to the baseline, and return the exit code.

    Exit 1 when a score regresses or a document starts erroring. Exit 2 when
    the baseline or config cannot be read. Warnings do not fail the run.

    Example:
        raise SystemExit(check_command("docassay.yaml", summary_only=True))
    """
    try:
        path = Path(config)
        loaded = load_config(path)
        target = _baseline_file(path, loaded.baseline, baseline)
        if not target.is_file():
            raise GateError(
                f"{target.as_posix()}:1:1: baseline not found. Run `docassay baseline` to create it."
            )
        reference = _read_json(target, "baseline")
        card = _execute(path, loaded, parsers, tags, no_cache, refresh, concurrency, verbose)
        result = compare(card, reference, loaded.thresholds, summary_only=summary_only)
    except GateError as err:
        print(err)
        return err.exit_code
    except (RunError, ConfigError, GoldenError, AdapterConfigError, CacheError) as err:
        print(err)
        return 2
    except AdapterUnavailable as err:
        print(err)
        return 3
    print(result.text, end="")
    return result.exit_code


def _execute(
    path: Path,
    loaded: Any,
    parsers: Sequence[str],
    tags: Sequence[str],
    no_cache: bool,
    refresh: bool,
    concurrency: int,
    verbose: bool,
) -> Scorecard:
    policy: CachePolicy = "refresh" if refresh else "no-cache" if no_cache else "use"
    outcome = execute(
        loaded,
        RunOptions(
            config_dir=path.resolve().parent,
            parsers=tuple(parsers),
            tags=tuple(tags),
            cache_policy=policy,
            concurrency=concurrency,
            verbose=verbose,
        ),
    )
    return outcome.card


def _baseline_file(config: Path, configured: str | None, override: str | Path | None) -> Path:
    if override is not None:
        return Path(override)
    name = configured or ".docassay/baseline.json"
    return config.parent / name


def _print_os(err: Exception) -> None:
    if isinstance(err, OSError):
        reason = err.strerror or "could not write the baseline"
        print(f"could not write the baseline: {reason}")
        return
    print(err)


def _summary_lines(
    lines: list[str],
    name: str,
    parser: Mapping[str, Any],
    previous: Mapping[str, Any],
    thresholds: Thresholds | None,
) -> int:
    failures = 0
    earlier = _score_map(previous.get("summary"))
    for metric, value in _score_pairs(parser.get("summary")):
        before = earlier.get(metric)
        drop, floor = _limits(thresholds, metric)
        if before is not None and _regressed(value, before, drop):
            lines.append(_drop_line(name, "summary", metric, before, value, drop))
            failures += 1
        if floor is not None and _below(value, floor):
            shown = before if before is not None else value
            lines.append(_floor_line(name, "summary", metric, shown, value, floor))
            failures += 1
    return failures


def _document_lines(
    lines: list[str],
    name: str,
    parser: Mapping[str, Any],
    previous: Mapping[str, Any],
    thresholds: Thresholds | None,
) -> tuple[int, int]:
    failures = 0
    warnings = 0
    earlier_docs = {str(doc["id"]): doc for doc in _documents(previous)}
    for doc in _documents(parser):
        doc_id = str(doc["id"])
        earlier = earlier_docs.get(doc_id)
        if earlier is None:
            lines.append(f"WARN  {name} · {doc_id} · new document")
            warnings += 1
            continue
        error = doc.get("error")
        if isinstance(error, str) and error and not earlier.get("error"):
            lines.append(f"FAIL  {name} · {doc_id} · error")
            lines.append(f"      document now errors: {error}")
            failures += 1
        previous_scores = _score_map(earlier.get("scores"))
        for metric, value in _score_pairs(doc.get("scores")):
            before = previous_scores.get(metric)
            drop, floor = _limits(thresholds, metric)
            dropped = before is not None and _regressed(value, before, drop)
            floored = floor is not None and _below(value, floor)
            detail = _first_failure(doc, metric) if dropped or floored else None
            if dropped and before is not None:
                lines.append(_drop_line(name, doc_id, metric, before, value, drop))
                if detail is not None:
                    lines.append(detail)
                failures += 1
            if floored and floor is not None:
                shown = before if before is not None else value
                lines.append(_floor_line(name, doc_id, metric, shown, value, floor))
                if detail is not None and not dropped:
                    lines.append(detail)
                failures += 1
    return failures, warnings


def _missing_documents(
    lines: list[str],
    name: str,
    parser: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> int:
    present = {str(doc["id"]) for doc in _documents(parser)}
    warnings = 0
    for doc in _documents(previous):
        doc_id = str(doc["id"])
        if doc_id not in present:
            lines.append(f"WARN  {name} · {doc_id} · missing from this run")
            warnings += 1
    return warnings


def _drop_line(
    parser: str, scope: str, metric: str, before: float, after: float, drop: float
) -> str:
    delta = _units(after) - _units(before)
    return (
        f"FAIL  {parser} · {scope} · {metric} {_fmt(before)} → {_fmt(after)} "
        f"({_signed(delta)}, allowed {_signed(-_units(drop))})"
    )


def _floor_line(
    parser: str, scope: str, metric: str, before: float, after: float, floor: float
) -> str:
    return (
        f"FAIL  {parser} · {scope} · {metric} {_fmt(before)} → {_fmt(after)} "
        f"(below floor {_fmt(floor)})"
    )


def _first_failure(doc: Mapping[str, Any], metric: str) -> str | None:
    failures = doc.get("failures")
    if not isinstance(failures, list):
        return None
    for failure in failures:
        if not isinstance(failure, Mapping) or failure.get("metric") != metric:
            continue
        check = failure.get("check")
        if not isinstance(check, str) or not check:
            continue
        parts: list[str] = []
        if "expected" in failure:
            parts.append(f"expected {_shown(failure.get('expected'))}")
        if "actual" in failure:
            parts.append(f"got {_shown(failure.get('actual'))}")
        body = ", ".join(parts) if parts else str(failure.get("reason", ""))
        return f"      {check}: {body}"
    return None


def _limits(thresholds: Thresholds | None, metric: str) -> tuple[float, float | None]:
    specific = getattr(thresholds, metric, None) if thresholds is not None else None
    default = thresholds.default if thresholds is not None else None
    drop = 0.02
    if specific is not None and specific.drop is not None:
        drop = specific.drop
    elif default is not None and default.drop is not None:
        drop = default.drop
    floor: float | None = None
    if specific is not None and specific.min is not None:
        floor = specific.min
    elif default is not None and default.min is not None:
        floor = default.min
    return drop, floor


def _regressed(current: float, baseline: float, drop: float) -> bool:
    return _units(current) < _units(baseline) - _units(drop)


def _below(current: float, floor: float) -> bool:
    return _units(current) < _units(floor)


def _units(value: float) -> int:
    return math.floor(float(value) * 10000 + 0.5)


def _fmt(value: float) -> str:
    return f"{_units(value) / 10000:.4f}"


def _signed(units: int) -> str:
    number = f"{abs(units) / 10000:.4f}"
    if units < 0:
        return f"{_MINUS}{number}"
    if units > 0:
        return f"+{number}"
    return "0.0000"


def _shown(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _count(count: int, singular: str, plural: str) -> str:
    word = singular if count == 1 else plural
    return f"{count} {word}"


def _score_pairs(value: object) -> list[tuple[str, float]]:
    scores = _score_map(value)
    return [(name, scores[name]) for name in _METRICS if name in scores]


def _score_map(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    found: dict[str, float] = {}
    for name in _METRICS:
        item = value.get(name)
        if isinstance(item, bool) or not isinstance(item, int | float):
            continue
        found[name] = float(item)
    return found


def _parsers(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    parsers = data.get("parsers")
    if not isinstance(parsers, list):
        raise GateError("scorecard parsers must be a list. Fix the JSON and retry.")
    return [parser for parser in parsers if isinstance(parser, Mapping)]


def _documents(parser: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    documents = parser.get("documents")
    if not isinstance(documents, list):
        return []
    return [doc for doc in documents if isinstance(doc, Mapping)]


def _scorecard(value: Scorecard | Mapping[str, Any], label: str) -> dict[str, Any]:
    data = _plain(value)
    try:
        Scorecard.model_validate(data)
    except ValidationError as err:
        detail = err.errors()[0]["msg"] if err.errors() else f"invalid {label}"
        raise GateError(f"{label} does not match scorecard.schema.json: {detail}.") from err
    return data


def _baseline(value: Baseline | Mapping[str, Any]) -> dict[str, Any]:
    data = _plain(value)
    try:
        Baseline.model_validate(data)
    except ValidationError as err:
        detail = err.errors()[0]["msg"] if err.errors() else "invalid baseline"
        raise GateError(f"baseline does not match baseline.schema.json: {detail}.") from err
    return data


def _plain(value: Scorecard | Baseline | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Scorecard | Baseline):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Mapping):
        return dict(value)
    raise GateError("expected a scorecard object. Pass JSON from `docassay run --format json`.")


def _baseline_parser(parser: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parser": parser["parser"],
        "adapter": parser["adapter"],
        "parserVersion": parser["parserVersion"],
        "summary": dict(_score_map(parser.get("summary"))),
        "documents": [_baseline_document(doc) for doc in _documents(parser)],
    }
    for key in ("costUsd", "latencyMsP50", "latencyMsP95"):
        if key in parser and parser[key] is not None:
            payload[key] = parser[key]
    return payload


def _baseline_document(doc: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": doc["id"],
        "file": doc["file"],
        "scores": dict(_score_map(doc.get("scores"))),
    }
    error = doc.get("error")
    if isinstance(error, str) and error:
        payload["error"] = error
    return payload


def _timestamp(value: object, label: str) -> str:
    if isinstance(value, datetime):
        return format_created(value)
    if isinstance(value, str):
        try:
            return format_created(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError as err:
            raise GateError(
                f"{label} must be an ISO 8601 timestamp. Example: 2026-11-03T12:00:00Z."
            ) from err
    raise GateError(f"{label} must be an ISO 8601 timestamp. Example: 2026-11-03T12:00:00Z.")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise GateError(
            f"{path.as_posix()}:{err.lineno}:{err.colno}: invalid JSON: {err.msg}. "
            "Fix the file or run `docassay baseline` again."
        ) from err
    except OSError as err:
        reason = err.strerror or "could not read the file"
        raise GateError(f"{path.as_posix()}:1:1: {reason}.") from err
    if not isinstance(loaded, dict):
        raise GateError(f"{path.as_posix()}:1:1: {label} must be a JSON object.")
    return loaded


def _stable_json(value: object) -> str:
    return json.dumps(_canonicalize(value), indent=2, ensure_ascii=False) + "\n"


def _canonicalize(value: object) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value == math.trunc(value) and abs(value) <= 2**53:
            return int(value)
        return value
    if isinstance(value, dict):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return str(value)
