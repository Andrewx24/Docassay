"""Guide samples are the files under tests/docs_snippets, and those files run."""

from __future__ import annotations

import importlib.util
import os
import re
import runpy
import shutil
import subprocess
from pathlib import Path

import yaml

from docassay.golden import load_golden
from docassay.models.document import Source

ROOT = Path(__file__).resolve().parents[2]
SNIPPETS = Path(__file__).resolve().parent / "docs_snippets"
GUIDES = ROOT / "docs" / "docs" / "guides"
README = ROOT / "README.md"
FENCE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)
TITLE = re.compile(r'title="([^"]+)"')


def _poetry_python() -> str:
    return subprocess.check_output(
        ["poetry", "-C", str(ROOT / "python"), "env", "info", "-e"],
        text=True,
    ).strip()


def _docassay_argv() -> list[str]:
    """Return a command that runs the installed docassay console script.

    Example:
        subprocess.run([*_docassay_argv(), "--version"], check=False)
    """
    python = _poetry_python()
    bindir = Path(python).parent
    exe = bindir / "docassay.exe"
    script = bindir / "docassay"
    if exe.is_file() and os.name == "nt":
        return [str(exe)]
    if os.name == "nt" and script.is_file():
        # The extensionless file is a Python script. CreateProcess cannot run it.
        return [python, str(script)]
    if script.is_file():
        return [str(script)]
    raise AssertionError(f"docassay is not installed next to {python}")


def _bash() -> str:
    """Return a bash that can run the guide scripts.

    Example:
        subprocess.run([_bash(), "quickstart.sh"], check=False)
    """
    if os.name == "nt":
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        for root in roots:
            for relative in ("Git/bin/bash.exe", "Git/usr/bin/bash.exe"):
                candidate = Path(root) / relative
                if candidate.is_file():
                    return str(candidate)
    found = shutil.which("bash")
    if found is None:
        raise AssertionError("bash is not installed. On Windows, install Git Bash.")
    return found


def _command_dir(scratch: Path) -> Path:
    """Return a directory whose ``docassay`` command Git Bash can execute.

    Example:
        env["PATH"] = str(_command_dir(tmp_path)) + os.pathsep + env["PATH"]
    """
    argv = _docassay_argv()
    if os.name != "nt":
        return Path(argv[0]).parent
    shim = scratch / "docassay-bin"
    shim.mkdir()
    quoted = " ".join(f'"{Path(part).as_posix()}"' for part in argv)
    (shim / "docassay").write_text(
        f'#!/bin/sh\nexec {quoted} "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    return shim


def _fences(path: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for info, body in FENCE.findall(path.read_text(encoding="utf-8")):
        title = TITLE.search(info)
        assert title is not None, f"{path}: code block has no title: {info!r}"
        found.append((title.group(1), body))
    return found


def test_guides_and_readme_copy_snippet_files() -> None:
    pages = [README, *sorted(GUIDES.glob("*.md*"))]
    assert len(pages) > 1, "expected guide pages"
    referenced: set[str] = set()
    for page in pages:
        fences = _fences(page)
        if page == README:
            assert fences, "README has no tested code samples"
        for title, body in fences:
            referenced.add(title)
            source = ROOT / title
            assert source.is_file(), title
            assert body == source.read_text(encoding="utf-8"), f"{page} drifted from {title}"
    assert "python/tests/docs_snippets/quickstart.sh" in referenced
    assert "examples/workflows/pr-check.yml" in referenced


def test_quickstart_scores_the_labeled_sentence(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(_command_dir(tmp_path)) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(
        [_bash(), str(SNIPPETS / "quickstart.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == (SNIPPETS / "quickstart.out.txt").read_text(encoding="utf-8")


def test_golden_scorecard_matches_the_committed_text() -> None:
    completed = subprocess.run(
        [*_docassay_argv(), "run", "-c", str(ROOT / "golden" / "docassay.yaml")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == (SNIPPETS / "golden-scorecard.txt").read_text(encoding="utf-8")
    image = ROOT / "docs" / "static" / "img" / "golden-scorecard.png"
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_values_snippet_matches_numbers_dates_and_text() -> None:
    namespace = runpy.run_path(str(SNIPPETS / "values.py"))
    assert namespace["same_number"] is True
    assert namespace["same_date"] is True
    assert namespace["different_text"] is False


def test_invoice_snippet_loads(tmp_path: Path) -> None:
    golden = tmp_path / "invoice.yaml"
    golden.write_text((SNIPPETS / "invoice.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / "files"
    target.mkdir()
    (target / "invoice-acme.pdf").write_bytes(b"%PDF-1.1\n")
    loaded = load_golden(golden)
    assert loaded[0].id == "invoice-acme"
    assert loaded[0].expect is not None
    assert loaded[0].expect.text_contains is not None
    assert [item.root for item in loaded[0].expect.text_contains] == ["Total due $10.00"]


def test_echo_adapter_reads_text_into_a_paragraph(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("echo_adapter", SNIPPETS / "echo_adapter.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source_file = tmp_path / "note.txt"
    source_file.write_text("hello\n", encoding="utf-8")
    adapter = module.EchoAdapter(None, {})
    document = adapter.to_document(
        adapter.fetch(source_file),
        Source(
            path="note.txt",
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            mime="text/plain",
        ),
    )
    assert document.blocks[0].text == "hello\n"
    assert adapter.available() == (True, "")


def test_check_snippet_passes_on_golden(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(_command_dir(tmp_path)) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(
        [_bash(), str(SNIPPETS / "ci.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "0 regressions" in completed.stdout


def test_pull_request_workflow_runs_check_and_comments() -> None:
    text = (ROOT / "examples" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    job = loaded["jobs"]["check"]
    step = job["steps"][1]
    assert step["uses"] == "Andrewx24/Docassay/action@v0"
    assert step["with"]["command"] == "check"
    assert step["with"]["comment"] is True
