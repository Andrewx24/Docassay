---
title: Spec (data model)
sidebar_position: 3
---

# Spec

The `spec/` folder holds five JSON Schemas (draft 2020-12). They are the contract between adapters, scorers, reporters and the two languages. This page is the design; the schema files themselves are built in **ALI-311**.

| Schema | Describes | Written by | Read by |
| --- | --- | --- | --- |
| `document.schema.json` | One parsed file, normalized | Adapters | Scorers, verify |
| `golden.schema.json` | What "correct" looks like for one file | Users, `docassay label` | Scorers |
| `config.schema.json` | `docassay.yaml` | Users, `docassay init` | CLI |
| `scorecard.schema.json` | Results of one run | `docassay run` | Reporters, gate |
| `baseline.schema.json` | Saved scorecard used as the reference | `docassay baseline` | `docassay check` |

## Conventions

- **Wire format is camelCase** (`blockIds`, `headingPath`). Python models use snake_case attributes with camelCase aliases, so JSON written by either package is byte-identical after sorting keys.
- **Golden and config YAML use snake_case** because humans write them (`text_contains`, `reading_order`).
- **Pages are 1-based.** Table rows and columns are **0-based**, and row 0 is the header row when `header` is present.
- **Bounding boxes** are `[x0, y0, x1, y1]` in PDF points with the origin at the **top-left** of the page. Adapters convert from their native system.
- **`specVersion`** is semver, stored in every Document, scorecard and baseline. Breaking changes bump the major.
- **`additionalProperties: false`** everywhere except `meta.extra` and `fields`, so typos fail fast.

## Document

```ts
interface Document {
  specVersion: string;            // "1.0.0"
  source: {
    path: string;                 // relative to the config file
    sha256: string;               // hash of file bytes, used as the cache key
    mime: string;                 // "application/pdf"
    pageCount?: number;
  };
  meta: {
    parser: string;               // adapter id, e.g. "llamaparse:agentic"
    parserVersion: string;        // vendor model/version actually used, e.g. "2026-09-24" or "r-1"
    adapterVersion: string;       // docassay version of the adapter
    latencyMs?: number;
    costUsd?: number;
    createdAt?: string;           // ISO 8601; excluded from cache identity and scoring
    extra?: Record<string, unknown>;
  };
  pages: Page[];
  blocks: Block[];                // in reading order as the parser reports it
  tables: Table[];
  chunks?: Chunk[];               // present only if the parser or user pipeline chunked
  fields?: Record<string, Field>; // present only for extraction parsers
  warnings?: string[];
}

interface Page   { number: number; width?: number; height?: number }

interface Block {
  id: string;                     // stable within the document, e.g. "b12"
  type: "title" | "heading" | "paragraph" | "list_item" | "caption"
      | "footnote" | "header" | "footer" | "code" | "formula" | "other";
  text: string;
  page: number;
  level?: number;                 // heading level, 1 = top
  bbox?: [number, number, number, number];
}

interface Table {
  id: string;                     // "t3"
  page: number;                   // first page the table appears on
  bbox?: [number, number, number, number];
  header?: string[];              // header row, if the parser identified one
  rows: string[][];               // body rows only; ragged rows padded with ""
  markdown?: string;              // parser's own rendering, informational only
}

interface Chunk {
  id: string;
  text: string;
  blockIds: string[];             // blocks (or "t3" table ids) the chunk came from
  pages: number[];
  headingPath?: string[];         // ["3 Pricing", "3.2 Discounts"]
  tokens?: number;
}

interface Field {
  value: string | number | boolean | null;
  confidence?: number;            // 0..1 if the parser reports it
  citations?: Citation[];
}

interface Citation { page: number; blockId?: string; bbox?: [number, number, number, number]; text?: string }
```

**Full document text** is defined, for scoring, as the `text` of every block in array order joined with `"\n\n"`, followed by every table rendered as `header` and `rows` joined with `" | "` per row and `"\n"` between rows. Both languages must build it the same way (`document_text()` / `documentText()`).

## Golden file

One YAML file per source document. Every section under `expect` is optional; a scorer runs only if its section exists.

```yaml
file: files/invoice-acme.pdf          # relative to the golden file
id: invoice-acme                      # optional; defaults to the file stem
tags: [invoice, digital]              # optional; used for filtering and grouping
expect:
  tables:
    - page: 1
      header: [Item, Qty, Price]
      rows: 4                         # expected body row count
      cols: 3                         # optional
      cells:                          # spot-checked cells, 0-based body rows
        - {row: 1, col: Price, value: "1,200.00"}   # col may be a header name or an index
  text_contains:
    - "Payment due within 30 days"
  reading_order:
    - ["Bill To", "Line Items"]       # first must appear before second
  fields:
    total: 4120.00
    due_date: 2026-11-01
    vendor: "Acme Corp"
  chunks:
    max_tokens: 800
    min_tokens: 20
    no_split_tables: true
    heading_paths:
      - text: "Late fees accrue"      # a chunk containing this text...
        path: ["Terms", "Late fees"]  # ...must have this heading path
```

Detailed semantics for each section live in [Golden sets](golden-sets.md) and [Scoring](scoring.md).

## Config (`docassay.yaml`)

```yaml
version: 1
golden: golden/                       # folder of *.yaml golden files
cache: .docassay/cache                # optional; this is the default
baseline: .docassay/baseline.json     # optional; this is the default
parsers:
  - id: docling
  - id: llamaparse:agentic
    options: {version: "2026-09-24"}  # passed to the adapter as-is
  - id: file
    name: my-pipeline                 # display name when one adapter is used twice
    options: {dir: out/parsed}
thresholds:
  default: {drop: 0.02}               # fail if a score falls more than 0.02 below baseline
  tables: {drop: 0.01, min: 0.80}     # min = absolute floor regardless of baseline
normalize:
  date_order: mdy                     # mdy | dmy, for ambiguous dates like 03/04/2026
  decimal: "."                        # "." | ","
tokenizer: words                      # words (default) | tiktoken:cl100k_base (optional extra)
```

## Scorecard

```ts
interface Scorecard {
  specVersion: string;
  docassayVersion: string;
  runId: string;                      // random; excluded from comparisons
  createdAt: string;                  // excluded from comparisons
  parsers: ParserResult[];
}

interface ParserResult {
  parser: string;                     // display name
  adapter: string;                    // adapter id
  parserVersion: string;
  summary: MetricScores;              // mean over documents that have each metric
  costUsd?: number;                   // total
  latencyMsP50?: number;
  latencyMsP95?: number;
  documents: DocumentResult[];
}

interface DocumentResult {
  id: string;                         // golden id
  file: string;
  error?: string;                     // adapter failure; all metrics count as 0
  scores: MetricScores;
  failures: Failure[];
}

type MetricScores = Partial<Record<"tables" | "text" | "order" | "fields" | "chunks" | "overall", number>>;

interface Failure {
  metric: "tables" | "text" | "order" | "fields" | "chunks";
  check: string;                      // e.g. "tables[0].cells[1]"
  expected?: unknown;
  actual?: unknown;
  reason: string;                     // human sentence, e.g. "value not found in matched table"
  page?: number;
}
```

All scores are in `[0, 1]`, computed in float64 and **rounded to 4 decimals only when written**.

## Baseline

A baseline is a scorecard with `documents[].failures` removed and a `pinnedAt` timestamp added. It is committed to git so a pull request shows score changes in its diff.
