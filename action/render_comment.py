"""Build the pull request comment posted by the docassay action.

The first line is the hidden marker ``<!-- docassay-report -->``. A later run
finds that marker and edits the same comment.

Example:
    python render_comment.py gate.txt report.md comment.md
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "<!-- docassay-report -->"
LIMIT = 65000


def render_comment(gate: str, markdown: str) -> str:
    """Return one comment that starts with the docassay marker.

    ``gate`` is the ``docassay check`` text. ``markdown`` is a scorecard from
    ``docassay run --format md``. Either one may be empty.

    Example:
        render_comment("0 regressions\\n", "<!-- docassay-report -->\\n\\n# docassay\\n")
    """
    parts = [MARKER, ""]
    gate_text = gate.strip()
    if gate_text:
        parts.extend(["## Check", "", "```text", gate_text, "```", ""])
    body = markdown
    if body.startswith(MARKER):
        body = body[len(MARKER) :].lstrip("\n")
    if body.strip():
        parts.append(body.rstrip("\n"))
    text = "\n".join(parts).rstrip() + "\n"
    if len(text) > LIMIT:
        note = "\n\n… truncated. The full report exceeded GitHub's comment limit.\n"
        text = text[: LIMIT - len(note)] + note
        if not text.startswith(MARKER):
            text = f"{MARKER}\n{text}"
    return text


def main(argv: list[str]) -> int:
    """Write the comment body to ``out``. Pass ``-`` to skip a source file.

    Example:
        python render_comment.py - report.md comment.md
    """
    if len(argv) != 4:
        print(
            "usage: render_comment.py <gate.txt|-> <report.md|-> <out.md>",
            file=sys.stderr,
        )
        return 2
    _, gate_path, markdown_path, out_path = argv
    gate = "" if gate_path == "-" else Path(gate_path).read_text(encoding="utf-8")
    markdown = "" if markdown_path == "-" else Path(markdown_path).read_text(encoding="utf-8")
    Path(out_path).write_text(render_comment(gate, markdown), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
