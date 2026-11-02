"""docassay run: score, cache, report, and exit codes."""

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from docassay.adapters.base import RawResult
from docassay.cli import app
from docassay.config import load_config
from docassay.models.document import Block, Document, Meta, Page, Source, Type
from docassay.report.json import render_json
from docassay.runner import RunOptions, execute, run
from jsonschema.validators import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "golden" / "docassay.yaml"
FIXTURE = ROOT / "spec" / "fixtures" / "run" / "example.json"
SCHEMA = ROOT / "spec" / "scorecard.schema.json"
WHEN = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)


class Counting:
    """Cacheable stand-in used to prove the runner reads and writes the cache."""

    fetches = 0
    id = "file"
    variants: tuple[str, ...] = ()
    cacheable = True

    def __init__(self, variant: str | None, options: dict[str, object]) -> None:
        self.variant = variant
        self.options = options

    def available(self) -> tuple[bool, str]:
        return True, ""

    def cache_identity(self) -> dict[str, object]:
        return {"dir": "counting"}

    def fetch(self, path: Path) -> RawResult:
        del path
        type(self).fetches += 1
        return RawResult(payload={}, parser_version="counting-1", latency_ms=1500, cost_usd=0.02)

    def to_document(self, raw: RawResult, source: Source) -> Document:
        del raw
        return Document(
            spec_version="1.0.0",
            source=source,
            meta=Meta(parser="placeholder", parser_version="from-adapter", adapter_version="0"),
            pages=[Page(number=1), Page(number=2)],
            blocks=[Block(id="b1", type=Type.paragraph, text="Hello", page=1)],
            tables=[],
        )


def _without_clock(payload: dict[str, object]) -> dict[str, object]:
    body = dict(payload)
    body.pop("runId", None)
    body.pop("createdAt", None)
    return body


def _example(options: RunOptions | None = None) -> dict[str, object]:
    config = load_config(CONFIG)
    opts = options or RunOptions(config_dir=CONFIG.parent, run_id="run_example", created_at=WHEN)
    return json.loads(render_json(run(config, opts)))


def test_example_json_matches_the_fixture_and_schema() -> None:
    payload = _example()
    assert payload == json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    assert list(validator.iter_errors(payload)) == []
    cache = ROOT / "golden" / ".docassay" / "cache"
    assert not cache.exists() or list(cache.rglob("*.json")) == []


def test_cli_reports_and_exit_codes(tmp_path: Path, monkeypatch: object) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    result = runner.invoke(app, ["run", "-c", str(CONFIG), "--format", "json"])
    assert result.exit_code == 0
    assert _without_clock(json.loads(result.stdout)) == _without_clock(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )
    markdown = runner.invoke(app, ["run", "-c", str(CONFIG), "--format", "md", "-v"])
    assert markdown.exit_code == 0
    assert markdown.stdout.startswith("<!-- docassay-report -->")
    assert "text not found" in markdown.stdout
    html_path = tmp_path / "report.html"
    html = runner.invoke(app, ["run", "-c", str(CONFIG), "--format", "html", "-o", str(html_path)])
    assert html.exit_code == 0
    page = html_path.read_text(encoding="utf-8")
    assert "http" not in page and 'id="metric"' in page
    assert runner.invoke(app, ["run", "-c", str(CONFIG), "--format", "xml"]).exit_code == 2
    assert runner.invoke(app, ["run", "-c", str(CONFIG), "--parser", "nope"]).exit_code == 2
    assert runner.invoke(app, ["run", "-c", str(CONFIG), "--tag", "missing"]).exit_code == 2
    assert runner.invoke(app, ["run", "-c", str(CONFIG), "--concurrency", "0"]).exit_code == 2
    assert runner.invoke(app, ["run", "-c", str(tmp_path / "missing.yaml")]).exit_code == 2
    tagged = runner.invoke(app, ["run", "-c", str(CONFIG), "--tag", "invoice", "--format", "json"])
    assert tagged.exit_code == 0
    assert [item["id"] for item in json.loads(tagged.stdout)["parsers"][0]["documents"]] == [
        "invoice-acme"
    ]


def test_adapter_error_scores_zero_and_the_run_continues(tmp_path: Path) -> None:
    files = tmp_path / "files"
    parsed = tmp_path / "parsed"
    golden = tmp_path / "golden"
    files.mkdir()
    parsed.mkdir()
    golden.mkdir()
    (files / "ok.pdf").write_bytes(b"%PDF-1.4\n")
    (files / "gone.pdf").write_bytes(b"%PDF-1.4\n")
    (parsed / "ok.md").write_text("# Note\n\nHello\n", encoding="utf-8")
    (golden / "ok.yaml").write_text(
        "file: ../files/ok.pdf\nexpect:\n  text_contains: [Hello]\n  chunks:\n    max_tokens: 10\n",
        encoding="utf-8",
    )
    (golden / "gone.yaml").write_text(
        "file: ../files/gone.pdf\nexpect:\n  text_contains: [Hello]\n  tables:\n    - {page: 1}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "docassay.yaml"
    config_path.write_text(
        "version: 1\ngolden: golden\ncache: .docassay/cache\nparsers:\n  - id: file\n"
        "    options: {dir: parsed}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["run", "-c", str(config_path), "--format", "json"])
    assert result.exit_code == 0
    documents = {item["id"]: item for item in json.loads(result.stdout)["parsers"][0]["documents"]}
    assert documents["ok"]["scores"]["text"] == 1
    assert documents["ok"]["scores"]["chunks"] == 0
    assert documents["ok"]["failures"][0]["reason"] == "parser produced no chunks"
    assert documents["gone"]["error"].startswith("no output for gone")
    assert documents["gone"]["scores"] == {"tables": 0, "text": 0, "overall": 0}
    assert documents["gone"]["failures"] == []
    only_gone = tmp_path / "only"
    only_gone.mkdir()
    (only_gone / "gone.yaml").write_text(
        "file: ../files/gone.pdf\nexpect:\n  text_contains: [Hello]\n",
        encoding="utf-8",
    )
    config_path.write_text(
        "version: 1\ngolden: only\nparsers:\n  - id: file\n    options: {dir: parsed}\n",
        encoding="utf-8",
    )
    failed = CliRunner().invoke(app, ["run", "-c", str(config_path), "--format", "json"])
    assert failed.exit_code == 3
    assert "no output for gone" in failed.stdout


def test_unavailable_parser_exits_3(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    golden.mkdir()
    (tmp_path / "note.pdf").write_bytes(b"%PDF")
    (golden / "note.yaml").write_text(
        "file: ../note.pdf\nexpect:\n  text_contains: [Hi]\n", encoding="utf-8"
    )
    config = tmp_path / "docassay.yaml"
    config.write_text("version: 1\ngolden: golden\nparsers:\n  - id: docling\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 3
    assert "docling" in result.stdout
    empty = tmp_path / "empty"
    empty.mkdir()
    config.write_text(
        "version: 1\ngolden: empty\nparsers:\n  - id: file\n    options: {dir: parsed}\n",
        encoding="utf-8",
    )
    assert CliRunner().invoke(app, ["run", "-c", str(config)]).exit_code == 2


def test_cacheable_adapter_reuses_and_refresh_refetches(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    Counting.fetches = 0
    monkeypatch.setattr(
        "docassay.runner.resolve", lambda parser_id, options=None: Counting(None, options or {})
    )  # type: ignore[attr-defined]
    files = tmp_path / "files"
    golden = tmp_path / "golden"
    files.mkdir()
    golden.mkdir()
    (files / "note.pdf").write_bytes(b"hello")
    (golden / "note.yaml").write_text(
        "file: ../files/note.pdf\nexpect:\n  text_contains: [Hello]\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "docassay.yaml"
    config_path.write_text(
        "version: 1\ngolden: golden\ncache: cache\nparsers:\n  - id: file\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    options = RunOptions(config_dir=tmp_path, run_id="run_cache", created_at=WHEN)
    first = execute(config, options)
    assert Counting.fetches == 1
    assert first.card.parsers[0].parser_version == "counting-1"
    assert first.pages == (2,)
    assert list((tmp_path / "cache").rglob("*.json"))
    execute(
        config, RunOptions(config_dir=tmp_path, verbose=True, run_id="run_cache", created_at=WHEN)
    )
    assert Counting.fetches == 1
    assert "cache hit file" in capsys.readouterr().err  # type: ignore[attr-defined]
    execute(config, RunOptions(config_dir=tmp_path, cache_policy="refresh"))
    assert Counting.fetches == 2
    execute(config, RunOptions(config_dir=tmp_path, cache_policy="no-cache"))
    assert Counting.fetches == 3
    same = execute(
        config, RunOptions(config_dir=tmp_path, concurrency=2, run_id="run_cache", created_at=WHEN)
    )
    assert render_json(same.card) == render_json(first.card)
