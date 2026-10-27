"""Scaffold smoke tests: version command and document model round-trip."""

import json
from importlib.metadata import version
from pathlib import Path

from typer.testing import CliRunner

from docassay.cli import app
from docassay.models.document import Document

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "spec" / "examples" / "document.example.json"


def test_version_matches_version_file() -> None:
    expected = (ROOT / "VERSION").read_text().strip()
    assert version("docassay") == expected
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == expected


def test_document_example_round_trip() -> None:
    raw = json.loads(EXAMPLE.read_text())
    dumped = Document.model_validate(raw).model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert dumped == raw
