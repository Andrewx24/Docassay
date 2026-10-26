# Contributing

docassay is pre-alpha. Issues and pull requests are welcome. Please read this page, then the docs it links, before opening a change.

The project is Apache-2.0. See [LICENSE](LICENSE) and the [license decision](https://andrewx24.github.io/Docassay/adr/0005-license).

## Dev setup

You need Python 3.10+ with [Poetry](https://python-poetry.org/), and Node.js 18+ with npm. Use npm, not pnpm or yarn. Packaging details are in [Packaging](https://andrewx24.github.io/Docassay/architecture/packaging). The repository layout is in [Repository layout](https://andrewx24.github.io/Docassay/architecture/repo-layout).

```bash
# Python (from python/)
poetry install --all-extras

# TypeScript (from js/)
npm ci

# Docs site (from docs/), only when you change docs
npm ci && npm start
```

The Python and TypeScript packages are scaffolded by later build-plan issues. Until those land, `python/` and `js/` contain only their READMEs.

## Checks

Run the checks that apply to your change. This block is the same one in `AGENTS.md`.

```bash
# Python (from python/)
poetry install --all-extras
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy src
poetry run pytest

# TypeScript (from js/)
npm ci
npm run lint && npm run typecheck && npm test && npm run build

# Repo root
./scripts/gen-types.sh && git diff --exit-code   # spec drift
./scripts/parity.sh                              # Python vs TypeScript scores

# Docs (from docs/), only when docs change
npm ci && npm run build
```

Scripts that do not exist yet are added by their Linear issue. See the [build plan](https://andrewx24.github.io/Docassay/implementation/build-plan).

A pull request is mergeable when the items in the [definition of done](https://andrewx24.github.io/Docassay/implementation/definition-of-done) that apply to it are true. One Linear issue per pull request. Title the PR `ALI-<n>: <title>`.

## Add an adapter

Read [Adapters](https://andrewx24.github.io/Docassay/architecture/adapters) before writing code. In short:

1. Split the adapter into fetch (network or library call) and map (pure conversion to a `Document`).
2. Implement it in every language the issue asks for, with the same adapter id. Mirror names: `to_document` in Python, `toDocument` in TypeScript.
3. Register it (Python entry point `docassay.adapters`, or `registerAdapter` in TypeScript).
4. Commit at least two recorded vendor payloads and the expected `Document` for each. Scrub keys and personal data. Unit tests must not call the network.
5. Record the vendor model or API version in `parserVersion`. `available()` must say why it cannot run when credentials or extras are missing.

## Add parity fixtures

Read [Cross-language parity](https://andrewx24.github.io/Docassay/architecture/parity). Any change to scoring, normalization, or verify adds fixtures under `spec/fixtures/` in the same pull request, including at least one failure case. Both packages must agree to 4 decimal places.

Normalization rules for cells, numbers, and dates live in [Normalization](https://andrewx24.github.io/Docassay/architecture/normalization). Do not restate them in code comments; link the doc if a rule is unclear.

## Change the spec

The JSON Schemas in `spec/` are the contract. Read [Spec](https://andrewx24.github.io/Docassay/architecture/spec) and [ADR 0002](https://andrewx24.github.io/Docassay/adr/0002-spec-first). A schema change updates the schema, `spec/examples/`, the Spec doc page, and both generated `models/` trees in one pull request. Do not hand-edit generated models. Regenerate them with `./scripts/gen-types.sh`.
