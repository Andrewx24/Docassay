# spec/

JSON Schemas (draft 2020-12) shared by the Python and TypeScript packages, plus the parity fixture corpus.

Planned contents (built in ALI-311 and ALI-321):

- `document.schema.json`, `golden.schema.json`, `config.schema.json`, `scorecard.schema.json`, `baseline.schema.json`
- `examples/` — one valid example per schema
- `fixtures/` — `<case>/{document.json, golden.yaml, expected.json}` used by both test suites

Design: [docs/docs/architecture/spec.md](../docs/docs/architecture/spec.md)
