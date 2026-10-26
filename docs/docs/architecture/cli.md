---
title: CLI
sidebar_position: 8
---

# CLI

Both packages install a `docassay` command with **identical commands, flags, output and exit codes**. Python uses Typer; TypeScript uses commander.

| Command | What it does | Issue |
| --- | --- | --- |
| `docassay init` | Write a starter `docassay.yaml` and `golden/` folder | ALI-340 |
| `docassay validate [--strict]` | Check config and every golden file | ALI-316 |
| `docassay run` | Parse (or read cache), score, print scorecard | ALI-320 |
| `docassay label <file> --from <parser>` | Draft a golden file from a parser | ALI-326 |
| `docassay label <golden.yaml> --review` | Open the local review page | ALI-326 |
| `docassay baseline` | Run and save the result as the baseline | ALI-327 |
| `docassay check` | Run and compare against the baseline | ALI-327 |
| `docassay cache clear` | Delete cached parser outputs | ALI-341 |

## Common flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `-c, --config <path>` | `./docassay.yaml` | Config file |
| `-p, --parser <id>` | all in config | Limit to one parser; repeatable |
| `--tag <tag>` | all | Limit to golden files with this tag; repeatable |
| `--format <fmt>` | `terminal` | `terminal`, `json`, `md`, `html` |
| `-o, --out <path>` | stdout | Write the report to a file |
| `--no-cache` | off | Ignore the cache for this run (still writes to it) |
| `--refresh` | off | Re-parse and overwrite cache entries |
| `--concurrency <n>` | 4 | Parallel parse calls per parser |
| `-v, --verbose` | off | Print failures for every document |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. For `check`: no regressions |
| 1 | `check` found a regression, or `validate --strict` found TODO labels |
| 2 | Usage or config error (bad flag, invalid YAML, unknown parser id) |
| 3 | A parser was unavailable or every document errored for a parser |

## Terminal scorecard

```text
docassay 0.1.0 · 20 documents · 3 parsers

parser               tables  text   order  fields  chunks  overall  $/1k pages  p95
llamaparse:agentic   0.94    0.99   0.95   0.96    —       0.96     12.50       48.0s
reducto              0.95    0.99   0.96   0.98    0.91    0.96     10.00        9.1s
docling              0.81    0.97   0.92   —       —       0.90      0.00        6.1s

Worst documents
  scan-lease-2019     tables 0.42 (docling), 0.58 (llamaparse:agentic)
  10k-segment-table   order 0.50 (docling)

Run with -v for every failure.
```

`—` means the metric is not labeled in any golden file for that parser or the parser does not produce it. Sorting is by `overall` descending, then parser name.

## Markdown and HTML reports

- `--format md` produces a compact table plus a collapsible failure list. It is designed to be posted as a pull request comment by the GitHub Action.
- `--format html` is a single self-contained file (no external assets) with filters by parser, tag and metric.

## Library API

The CLI is a thin layer over the library, which is also public:

```python
from docassay import run, load_config
card = run(load_config("docassay.yaml"))
print(card.parsers[0].summary.tables)
```

```ts
import {run, loadConfig} from 'docassay';
const card = await run(await loadConfig('docassay.yaml'));
```
