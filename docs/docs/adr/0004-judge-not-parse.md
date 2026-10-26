---
title: "ADR 0004: Judge, don't parse"
---

# ADR 0004: docassay judges parsers and never becomes one

**Status:** Accepted · 2026-10-26

## Context

The parser market is crowded and well funded (Reducto, LlamaParse, Unstructured, Docling, MinerU, Marker, Mistral OCR). Open models now top parsing benchmarks. What is missing is a neutral way for developers to measure parsers on their own documents and catch regressions.

## Decision

docassay contains no parsing, OCR, layout or chunking logic beyond the trivial Markdown reading in the `file` adapter. No built-in parser is favored in defaults, docs or reports.

## Consequences

- Every new parser is a reason to use docassay, not a competitor.
- Neutrality is a feature: no vendor sponsorship of default rankings, and the public scorecard publishes its method.
- The `label` command uses a parser to draft labels, but the user picks which one.
