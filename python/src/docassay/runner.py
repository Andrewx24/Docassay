"""Run parsers, score golden files, and build a scorecard.

The file adapter is not cached (``cacheable`` is false). Every other adapter
reads and writes the parser cache from ALI-341. Paths inside the config are
relative to the config file; pass ``RunOptions.config_dir`` when the process
working directory is somewhere else.

Example:
    from docassay import load_config, run

    card = run(load_config("docassay.yaml"))
    print(card.parsers[0].summary.tables)
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

from docassay.adapters.base import (
    Adapter,
    AdapterConfigError,
    AdapterError,
    AdapterUnavailable,
)
from docassay.adapters.registry import resolve
from docassay.adapters.runner_support import ensure_available, package_version, run_parser
from docassay.cache import (
    CacheError,
    CachePolicy,
    file_sha256,
    lookup_cache,
    write_cache,
)
from docassay.config import ConfigError, load_config
from docassay.golden import GoldenError, load_golden
from docassay.models.config import Config, Parser
from docassay.models.document import Document
from docassay.models.golden import Expect, GoldenFile, ReadingOrderItem, TextContain
from docassay.models.scorecard import Scorecard
from docassay.report.html import render_html
from docassay.report.json import render_json
from docassay.report.markdown import render_markdown
from docassay.report.terminal import render_terminal
from docassay.scoring import Failure, ScoreOptions
from docassay.scoring.aggregate import aggregate_scores, document_scores, percentile
from docassay.scoring.chunks import score_chunks
from docassay.scoring.fields import score_fields
from docassay.scoring.order import score_order
from docassay.scoring.tables import score_tables
from docassay.scoring.text import score_text

_FORMATS = ("terminal", "json", "md", "html")
_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


class RunError(Exception):
    """A usage problem found while starting a run. CLI commands exit 2.

    Example:
        raise RunError('unknown format "xml". Use terminal, json, md, or html.')
    """

    exit_code = 2


@dataclass(frozen=True)
class RunOptions:
    """Filters, cache policy, and clocks for :func:`run`.

    ``parsers`` and ``tags`` empty means no filter. ``config_dir`` is the
    directory that contains the config file. ``run_id`` and ``created_at``
    are filled in when omitted.

    Example:
        RunOptions(parsers=("file",), cache_policy="refresh", concurrency=2)
    """

    config_dir: Path | None = None
    parsers: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    cache_policy: CachePolicy = "use"
    concurrency: int = 4
    verbose: bool = False
    run_id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class RunOutcome:
    """A scorecard plus the page totals and tags the reporters need.

    Page totals are not part of the scorecard schema. They are the denominator
    for dollars per 1,000 pages. Tags filter the HTML report.

    Example:
        outcome = execute(load_config("docassay.yaml"))
    """

    card: Scorecard
    pages: tuple[int, ...]
    tags: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class _DocOutcome:
    doc_id: str
    file: str
    scores: dict[str, float]
    failures: list[dict[str, object]]
    error: str | None
    pages: int
    cost: float | None
    latency: float | None
    version: str | None


def run(config: Config, options: RunOptions | None = None) -> Scorecard:
    """Score every selected parser and return the scorecard.

    Paths in ``config`` are relative to the config file. Pass
    ``options.config_dir`` when the working directory is somewhere else.
    ``runId`` and ``createdAt`` change on every call unless ``options`` sets
    them.

    Example:
        card = run(load_config("docassay.yaml"))
        print(card.parsers[0].summary.tables)
    """
    return execute(config, options).card


def execute(config: Config, options: RunOptions | None = None) -> RunOutcome:
    """Score parsers and keep the page totals and tags used by reporters.

    Example:
        outcome = execute(load_config("docassay.yaml"), RunOptions(verbose=True))
    """
    opts = options or RunOptions()
    _check_options(opts)
    base = Path.cwd() if opts.config_dir is None else Path(opts.config_dir)
    selected = _select_parsers(config, opts.parsers)
    adapters = _build_adapters(selected, base)
    ensure_available([(parser.id, adapter) for parser, adapter in adapters])
    golden_dir = (base / config.golden).resolve()
    files = _select_files(load_golden(golden_dir), opts.tags)
    cache_root = (base / (config.cache or ".docassay/cache")).resolve()
    score_options = _score_options(config)
    moment = opts.created_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    run_id = opts.run_id if opts.run_id is not None else _new_run_id()
    if not run_id:
        raise RunError("run id must not be empty. Omit run_id to generate one.")
    tag_map = {_doc_id(item): _tag_list(item) for item in files}
    parsers: list[dict[str, object]] = []
    page_totals: list[int] = []

    def _score_file(parser: Parser, adapter: Adapter) -> Callable[[GoldenFile], _DocOutcome]:
        def score(golden: GoldenFile) -> _DocOutcome:
            return _score_one(
                parser,
                adapter,
                golden,
                golden_dir,
                cache_root,
                opts.cache_policy,
                opts.verbose,
                score_options,
            )

        return score

    for parser, adapter in adapters:
        outcomes = _parallel(files, _score_file(parser, adapter), opts.concurrency)
        parsers.append(_parser_payload(parser, outcomes))
        page_totals.append(sum(item.pages for item in outcomes))
    card = Scorecard.model_validate(
        {
            "specVersion": "1.0.0",
            "docassayVersion": package_version(),
            "runId": run_id,
            "createdAt": moment,
            "parsers": parsers,
        }
    )
    return RunOutcome(card=card, pages=tuple(page_totals), tags=tag_map)


def run_command(
    config_path: str | Path,
    *,
    parsers: Sequence[str] = (),
    tags: Sequence[str] = (),
    fmt: str = "terminal",
    out: str | Path | None = None,
    no_cache: bool = False,
    refresh: bool = False,
    concurrency: int = 4,
    verbose: bool = False,
) -> int:
    """Print a scorecard and return the ``docassay run`` exit code.

    Exit 0 when the run finished and at least one document succeeded for every
    parser. Exit 2 for usage, config, or golden-file errors. Exit 3 when a
    parser is unavailable or every document errored for a parser. ``--refresh``
    wins over ``--no-cache`` when both are set: the cache is not read and the
    next write overwrites it.

    Example:
        raise SystemExit(run_command("docassay.yaml", fmt="json"))
    """
    if fmt not in _FORMATS:
        print(f'unknown format "{fmt}". Use terminal, json, md, or html.')
        return 2
    path = Path(config_path)
    policy: CachePolicy = "refresh" if refresh else "no-cache" if no_cache else "use"
    try:
        config = load_config(path)
        outcome = execute(
            config,
            RunOptions(
                config_dir=path.resolve().parent,
                parsers=tuple(parsers),
                tags=tuple(tags),
                cache_policy=policy,
                concurrency=concurrency,
                verbose=verbose,
            ),
        )
    except RunError as err:
        print(err)
        return err.exit_code
    except (ConfigError, GoldenError, AdapterConfigError, CacheError) as err:
        print(err)
        return 2
    except AdapterUnavailable as err:
        print(err)
        return 3
    text = _render(outcome, fmt, verbose=verbose)
    if out is None:
        print(text, end="")
    else:
        target = Path(out)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except OSError as err:
            reason = err.strerror or "could not write the file"
            print(f"could not write {target.as_posix()}: {reason}")
            return 2
    return _exit_code(outcome.card)


def _render(outcome: RunOutcome, fmt: str, *, verbose: bool) -> str:
    if fmt == "json":
        return render_json(outcome.card)
    if fmt == "md":
        return render_markdown(outcome.card, outcome.pages)
    if fmt == "html":
        return render_html(outcome.card, outcome.pages, outcome.tags)
    return render_terminal(outcome.card, outcome.pages, verbose=verbose)


def _exit_code(card: Scorecard) -> int:
    for parser in card.parsers:
        if parser.documents and all(document.error for document in parser.documents):
            return 3
    return 0


def _check_options(options: RunOptions) -> None:
    if options.concurrency < 1:
        raise RunError("concurrency must be an integer >= 1. Pass --concurrency 4.")
    if options.cache_policy not in ("use", "no-cache", "refresh"):
        raise RunError('unknown cache policy. Use "use", "no-cache", or "refresh".')


def _select_parsers(config: Config, requested: Sequence[str]) -> list[Parser]:
    if not requested:
        return list(config.parsers)
    by_id = {parser.id: parser for parser in config.parsers}
    chosen: list[Parser] = []
    for parser_id in requested:
        found = by_id.get(parser_id)
        if found is None:
            known = ", ".join(parser.id for parser in config.parsers)
            raise RunError(f'unknown parser "{parser_id}". Configured parsers: {known}.')
        chosen.append(found)
    return chosen


def _build_adapters(parsers: Sequence[Parser], base: Path) -> list[tuple[Parser, Adapter]]:
    built: list[tuple[Parser, Adapter]] = []
    for parser in parsers:
        options = dict(parser.options or {})
        if parser.id.split(":", 1)[0] == "file":
            directory = options.get("dir")
            if isinstance(directory, str) and directory:
                options["dir"] = (base / directory).resolve().as_posix()
        built.append((parser, resolve(parser.id, options)))
    return built


def _select_files(files: Sequence[GoldenFile], tags: Sequence[str]) -> list[GoldenFile]:
    if not files:
        raise RunError("golden set has no files. Add a *.yaml file under the golden folder.")
    if not tags:
        return list(files)
    wanted = set(tags)
    chosen = [item for item in files if wanted.intersection(_tag_list(item))]
    if not chosen:
        listed = ", ".join(tags)
        raise RunError(
            f"no golden file has tag {listed}. Remove --tag or add the tag to a golden file."
        )
    return chosen


def _score_options(config: Config) -> ScoreOptions:
    decimal: Literal[".", ","] = "."
    date_order: Literal["mdy", "dmy"] = "mdy"
    normalize = config.normalize
    if normalize is not None and normalize.decimal is not None and normalize.decimal.value == ",":
        decimal = ","
    if (
        normalize is not None
        and normalize.date_order is not None
        and normalize.date_order.value == "dmy"
    ):
        date_order = "dmy"
    return ScoreOptions(decimal=decimal, date_order=date_order, tokenizer=_tokenizer(config))


def _tokenizer(config: Config) -> str:
    value = config.tokenizer
    if value is None or value == "words":
        return "words"
    if isinstance(value, str):
        return value
    return value.root


def _score_one(
    parser: Parser,
    adapter: Adapter,
    golden: GoldenFile,
    golden_dir: Path,
    cache_root: Path,
    policy: CachePolicy,
    verbose: bool,
    score_options: ScoreOptions,
) -> _DocOutcome:
    doc_id = _doc_id(golden)
    defined = _defined(golden.expect)
    source = (golden_dir / golden.file).resolve()
    try:
        document = _load_document(
            adapter, parser.id, source, golden.file, cache_root, policy, verbose
        )
    except (AdapterError, OSError) as err:
        message = str(err).strip() or "adapter failed"
        return _DocOutcome(
            doc_id=doc_id,
            file=golden.file,
            scores=document_scores({name: 0.0 for name in defined}),
            failures=[],
            error=message,
            pages=0,
            cost=None,
            latency=None,
            version=None,
        )
    scores, failures = _score_document(document, golden, score_options)
    return _DocOutcome(
        doc_id=doc_id,
        file=golden.file,
        scores=scores,
        failures=failures,
        error=None,
        pages=_page_count(document),
        cost=document.meta.cost_usd,
        latency=document.meta.latency_ms,
        version=document.meta.parser_version,
    )


def _load_document(
    adapter: Adapter,
    parser_id: str,
    source: Path,
    stored: str,
    cache_root: Path,
    policy: CachePolicy,
    verbose: bool,
) -> Document:
    if not _cacheable(adapter):
        return run_parser(adapter, source, parser_id, stored_path=stored)
    data = source.read_bytes()
    digest = file_sha256(data)
    identity = adapter.cache_identity()
    lookup = lookup_cache(cache_root, digest, parser_id, identity, policy=policy, verbose=verbose)
    if lookup.document is not None:
        return lookup.document
    document = run_parser(adapter, source, parser_id, stored_path=stored)
    write_cache(cache_root, digest, parser_id, identity, document)
    return document


def _score_document(
    document: Document, golden: GoldenFile, options: ScoreOptions
) -> tuple[dict[str, float], list[dict[str, object]]]:
    expect = golden.expect
    raw: dict[str, float] = {}
    failures: list[Failure] = []
    if expect is not None:
        if expect.tables is not None:
            result = score_tables(document, expect.tables, options)
            raw["tables"] = result.score
            failures.extend(result.failures)
        if expect.text_contains is not None:
            result = score_text(document, _text_list(expect.text_contains), options)
            raw["text"] = result.score
            failures.extend(result.failures)
        if expect.reading_order is not None:
            result = score_order(document, _order_list(expect.reading_order), options)
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
    return document_scores(raw), [failure.to_json() for failure in failures]


def _parser_payload(parser: Parser, outcomes: Sequence[_DocOutcome]) -> dict[str, object]:
    version = next((item.version for item in outcomes if item.version), None) or "unknown"
    costs = [item.cost for item in outcomes if item.cost is not None]
    latencies = [item.latency for item in outcomes if item.latency is not None]
    payload: dict[str, object] = {
        "parser": parser.name or parser.id,
        "adapter": parser.id,
        "parserVersion": version,
        "summary": aggregate_scores([item.scores for item in outcomes]),
        "documents": [_document_payload(item) for item in outcomes],
    }
    if costs:
        payload["costUsd"] = sum(costs)
    if latencies:
        payload["latencyMsP50"] = percentile(latencies, 0.5)
        payload["latencyMsP95"] = percentile(latencies, 0.95)
    return payload


def _document_payload(item: _DocOutcome) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": item.doc_id,
        "file": item.file,
        "scores": item.scores,
        "failures": item.failures,
    }
    if item.error:
        payload["error"] = item.error
    return payload


def _defined(expect: Expect | None) -> list[str]:
    if expect is None:
        return []
    found: list[str] = []
    if expect.tables is not None:
        found.append("tables")
    if expect.text_contains is not None:
        found.append("text")
    if expect.reading_order is not None:
        found.append("order")
    if expect.fields is not None:
        found.append("fields")
    if expect.chunks is not None:
        found.append("chunks")
    return found


def _text_list(items: Sequence[TextContain]) -> list[str]:
    return [item.root for item in items]


def _order_list(items: Sequence[ReadingOrderItem]) -> list[tuple[str, str]]:
    return [(item.root[0].root, item.root[1].root) for item in items]


def _page_count(document: Document) -> int:
    if document.source.page_count is not None:
        return document.source.page_count
    return len(document.pages)


def _doc_id(golden: GoldenFile) -> str:
    if golden.id:
        return golden.id
    return Path(golden.file).stem


def _tag_list(golden: GoldenFile) -> tuple[str, ...]:
    if not golden.tags:
        return ()
    return tuple(tag.root for tag in golden.tags)


def _cacheable(adapter: Adapter) -> bool:
    flag = getattr(adapter, "cacheable", True)
    return flag is not False


def _parallel(
    items: Sequence[_Item],
    worker: Callable[[_Item], _Result],
    concurrency: int,
) -> list[_Result]:
    if concurrency <= 1 or len(items) <= 1:
        return [worker(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(concurrency, len(items))) as pool:
        futures = [pool.submit(worker, item) for item in items]
        return [future.result() for future in futures]


def _new_run_id() -> str:
    return "run_" + secrets.token_hex(8)
