"""Command line interface."""

from pathlib import Path
from typing import Annotated

import typer

from docassay import __version__
from docassay.golden import validate_command

app = typer.Typer(add_completion=False, invoke_without_command=True)


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
