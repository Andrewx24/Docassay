# docassay

**pytest for document ingestion.** Score any document parser against your own labeled documents, catch regressions in CI, and verify that extracted values match their sources.

> Status: pre-alpha. Architecture is written; implementation is in progress. Nothing is published yet.

```bash
pip install docassay      # Python 3.10+
npm install docassay      # Node 18+
```

```text
parser               tables  text   order  fields  overall  $/1k pages
reducto              0.95    0.99   0.96   0.98    0.96     10.00
llamaparse:agentic   0.94    0.99   0.95   0.96    0.96     12.50
docling              0.81    0.97   0.92   —       0.90      0.00
```

## Why

Teams pick a PDF parser by eyeballing a few files, then ship and hope. When a vendor updates a model or someone tweaks a chunker, tables quietly turn into digit soup and retrieval gets worse with no error. docassay makes ingestion quality measurable and testable:

1. **Label** 10–30 of your own documents in YAML (drafted for you by `docassay label`).
2. **Run** any parser through an adapter and get a scorecard.
3. **Baseline** today's scores and **check** them on every pull request.
4. **Verify** extracted values against their cited source spans in production.

docassay never parses documents itself. It judges the parsers you already use.

## Docs

Published site: <https://andrewx24.github.io/Docassay/>.

- [Architecture](docs/docs/architecture/overview.md)
- [Build plan](docs/docs/implementation/build-plan.md)
- [Agent guide](AGENTS.md) for coding agents

Run the docs site locally:

```bash
cd docs && npm install && npm start
```

## Repository

| Folder | Contents |
| --- | --- |
| `spec/` | JSON Schemas shared by both packages, plus parity fixtures |
| `python/` | PyPI package (Poetry) |
| `js/` | npm package (TypeScript) |
| `golden/` | Public example golden set |
| `docs/` | Docusaurus documentation site |

## License

Apache-2.0
