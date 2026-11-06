# Results

This scorecard is the output of `docassay run -c golden/docassay.yaml`. The parser is the `file` adapter (`parserVersion` `file`) reading `parsed/<id>.md`. docassay `0.0.0` produced it from those committed files, so running the command again prints the same table.

The file adapter does not call Docling, LlamaParse, or Reducto. This file does not invent scores for those parsers. A three-vendor column belongs here only after those adapters run on these documents and the cache is committed.

```text
docassay 0.0.0 · 20 documents · 1 parser

parser  tables  text  order  fields  chunks  overall  $/1k pages  p95
file    0.88    0.98  1.00   —       —       0.96     0.00        0.0s

Worst documents
  memo   tables 0.00 (file)
  lease  text 0.50 (file)

Run with -v for every failure.
```
