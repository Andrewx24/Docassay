"""Scorecard reporters.

Example:
    from docassay.report import render_json, render_terminal

    print(render_terminal(card, pages))
"""

from docassay.report.html import render_html
from docassay.report.json import render_json
from docassay.report.markdown import render_markdown
from docassay.report.terminal import render_terminal

__all__ = ["render_html", "render_json", "render_markdown", "render_terminal"]
