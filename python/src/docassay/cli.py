"""Command line interface."""

from pathlib import Path
from typing import Annotated

import typer

from docassay import __version__
from docassay.cache import clear_cache
from docassay.config import init_command
from docassay.golden import validate_command
from docassay.label import label_command
from docassay.runner import run_command

app = typer.Typer(add_completion=False, invoke_without_command=True)
cache_app = typer.Typer(help="Manage the parser output cache.")
app.add_typer(cache_app, name="cache")


def _print_version(value: bool) -> None:
    """Print the installed version and exit.

    Example:
        docassay --version
    """
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Score document parsers against labeled golden files.

    Example:
        docassay --version
    """
    del version


@app.command()
def init(
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite docassay.yaml and golden/example.yaml."),
    ] = False,
) -> None:
    """Write a starter docassay.yaml and golden/example.yaml.

    Example:
        docassay init
    """
    raise typer.Exit(code=init_command(force=force))


@app.command()
def validate(
    path: Annotated[
        Path,
        typer.Argument(help="Golden set directory or a single golden file."),
    ] = Path("golden"),
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Fail if a file still contains # TODO verify."),
    ] = False,
) -> None:
    """Check golden files against the schema.

    Example:
        docassay validate --strict golden
    """
    raise typer.Exit(code=validate_command(path, strict=strict))


@app.command()
def label(
    path: Annotated[
        Path,
        typer.Argument(help="Source file to draft, or a golden file with --review."),
    ],
    parser: Annotated[
        str | None,
        typer.Option("--from", help="Parser id that drafts the golden file."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Golden file to write. Default: golden/<stem>.yaml."),
    ] = None,
    review: Annotated[
        bool,
        typer.Option("--review", help="Open the local review page for a golden file."),
    ] = False,
    config: Annotated[
        Path,
        typer.Option("-c", "--config", help="Config file."),
    ] = Path("docassay.yaml"),
) -> None:
    """Draft a golden file from a parser, or open the review page.

    Example:
        docassay label files/lease.pdf --from docling
    """
    raise typer.Exit(code=label_command(path, parser=parser, out=out, review=review, config=config))


@cache_app.command("clear")
def cache_clear(
    parser: Annotated[
        str | None,
        typer.Option("--parser", help="Delete entries for one parser id, including its variants."),
    ] = None,
    cache: Annotated[
        Path,
        typer.Option("--cache", help="Cache directory."),
    ] = Path(".docassay/cache"),
) -> None:
    """Delete cached parser outputs.

    Example:
        docassay cache clear --parser llamaparse:agentic
    """
    removed = clear_cache(cache, parser)
    scope = "" if parser is None else f" for {parser}"
    typer.echo(f"removed {removed} cache file(s){scope} from {cache.as_posix()}")


@app.command()
def run(
    config: Annotated[
        Path,
        typer.Option("-c", "--config", help="Config file."),
    ] = Path("docassay.yaml"),
    parser: Annotated[
        list[str] | None,
        typer.Option("-p", "--parser", help="Limit to one parser. Repeatable."),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Limit to golden files with this tag. Repeatable."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", help="terminal, json, md, or html."),
    ] = "terminal",
    out: Annotated[
        Path | None,
        typer.Option("-o", "--out", help="Write the report to a file."),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Ignore the cache for this run (still writes to it)."),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Re-parse and overwrite cache entries."),
    ] = False,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="Parallel parse calls per parser."),
    ] = 4,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Print failures for every document."),
    ] = False,
) -> None:
    """Parse (or read the cache), score, and print a scorecard.

    Example:
        docassay run -c docassay.yaml --format json
    """
    raise typer.Exit(
        code=run_command(
            config,
            parsers=parser or [],
            tags=tag or [],
            fmt=fmt,
            out=out,
            no_cache=no_cache,
            refresh=refresh,
            concurrency=concurrency,
            verbose=verbose,
        )
    )
