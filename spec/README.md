# spec/

JSON Schemas (draft 2020-12) shared by the Python and TypeScript packages, plus the parity fixture corpus.

- `document.schema.json`, `golden.schema.json`, `config.schema.json`, `scorecard.schema.json`, `baseline.schema.json` (`specVersion` 1.0.0)
- `examples/` — one valid example per schema, JSON and YAML
- `invalid/` — examples that must fail validation
- `fixtures/` — parity corpus, added in ALI-321: `<case>/{document.json, golden.yaml, expected.json}`

Check the schemas:

```bash
npm ci --prefix scripts
node scripts/validate-spec.mjs
```

Design: [docs/docs/architecture/spec.md](../docs/docs/architecture/spec.md)
