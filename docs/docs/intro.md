---
slug: /
sidebar_position: 1
title: What is docassay?
---

# docassay

**pytest for document ingestion.** An assay tests how pure a material is. docassay tests how clean your document ingestion is.

docassay does not parse documents. It **judges** the parsers you already use (Docling, LlamaParse, Reducto, your own pipeline) against a small set of your own labeled documents, and it keeps them honest over time.

## The problem

Teams pick a parser by eyeballing a few PDFs, then ship and hope. Three things then go wrong without any error:

1. **The choice is made on vibes.** Vendor benchmarks disagree, and every guide says "test on your own documents". Almost nobody has a way to do that.
2. **Quality drifts.** A vendor model update, a library upgrade or a chunking tweak turns tables into digit soup. The pipeline still runs; retrieval just gets worse.
3. **Extracted values are never checked.** The LLM says the invoice total is $4,120 on page 2, and nothing confirms that $4,120 is actually there.

## What docassay does

| Step | Command | Result |
| --- | --- | --- |
| Label 10–30 of your documents | `docassay label` | YAML golden files, drafted for you to correct |
| Compare parsers | `docassay run` | A scorecard: tables, text, reading order, fields, chunks, cost, latency |
| Lock in quality | `docassay baseline` | A committed baseline of today's scores |
| Catch regressions | `docassay check` | A non-zero exit in CI when a score drops |
| Check production output | `verify()` | Each extracted value confirmed against its cited source |

## Two packages, one behavior

docassay ships as a Python package on PyPI (built with Poetry) and a TypeScript package on npm. Both implement one shared spec and must produce **identical scores** for identical input. See [Parity](architecture/parity.md).

```bash
pip install docassay        # or: poetry add docassay
npm install docassay
```

## Where to go next

- New to the design: read the [Architecture overview](architecture/overview.md).
- Building it: follow the [Build plan](implementation/build-plan.md). Each step maps to a Linear issue.
- Using a coding agent: give it the [Agent guide](implementation/agent-guide.md).
