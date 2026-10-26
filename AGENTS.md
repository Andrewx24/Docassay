# AGENTS.md

Instructions for coding agents (Grok Build, Claude Code, Copilot, Cursor) working in this repository. Humans should read it too.

## What this project is

docassay is "pytest for document ingestion". It scores document parsers against user-labeled golden files, gates regressions in CI, and verifies extracted values against their sources. It ships as **two native packages with identical behavior**: Python (`python/`, Poetry, PyPI) and TypeScript (`js/`, npm). It never parses documents itself.

## Where the spec lives

The design docs in `docs/docs/` are the specification. Read the pages an issue links before writing code.

| Topic | File |
| --- | --- |
| Overview | `docs/docs/architecture/overview.md` |
| Repo layout and naming | `docs/docs/architecture/repo-layout.md` |
| Data model | `docs/docs/architecture/spec.md` |
| Scoring algorithms | `docs/docs/architecture/scoring.md` |
| Normalization rules | `docs/docs/architecture/normalization.md` |
| Adapters | `docs/docs/architecture/adapters.md` |
| CLI | `docs/docs/architecture/cli.md` |
| CI gate | `docs/docs/architecture/ci-gate.md` |
| Verify | `docs/docs/architecture/verify.md` |
| Parity rules | `docs/docs/architecture/parity.md` |
| Packaging | `docs/docs/architecture/packaging.md` |
| Build order | `docs/docs/implementation/build-plan.md` |
| Definition of done | `docs/docs/implementation/definition-of-done.md` |

Work is tracked in Linear (team `ALI`, project "docassay"). Each issue has an **Implementation brief** with files to touch and acceptance criteria.

## Workflow

1. Take the lowest-numbered step in the build plan whose blockers are merged.
2. Use the Linear branch name, e.g. `andyaliaj/ali-339-normalization-library-text-numbers-dates`.
3. Write tests first from the docs and fixtures.
4. Implement in every language the issue is labeled with. Mirror module and function names (`score_tables` ↔ `scoreTables`).
5. Run all checks below. Fix until green.
6. Open a PR titled `ALI-<n>: <title>` and fill in the template. One issue per PR.

## Commands

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

Scripts that do not exist yet are created by their Linear issue (see the build plan).

## Hard rules

- Never hand-edit `python/src/docassay/models/` or `js/src/models/`. They are generated from `spec/`.
- A schema change updates `spec/*.schema.json`, `spec/examples/`, the Spec doc page and both generated model sets in one PR.
- Any change to scoring, normalization or verify adds fixtures under `spec/fixtures/` and must pass parity to 4 decimal places.
- Scorers are pure: no network, clock, randomness, environment variables or locale-dependent functions.
- Do not use regex `\s`, `\w`, `\b` or `\d` in scoring or normalization code; use the explicit sets in the Normalization doc.
- Round scores only on output, with `round4(x) = floor(x * 10000 + 0.5) / 10000`.
- Load YAML with 1.2 core-schema semantics in both languages (unquoted dates stay strings).
- Unit tests make no network calls. Adapter tests use recorded payloads; live tests are opt-in (`@pytest.mark.live`, `LIVE=1`).
- No secrets or personal data in code, fixtures or logs.
- No new runtime dependency without a reason in the PR description.
- If the docs and the issue disagree, or the docs are silent on something that changes a score, stop and comment on the Linear issue.

## Style

- Python 3.10+, type hints everywhere, `mypy --strict` clean, ruff defaults with line length 100.
- TypeScript strict mode, ESM source, no `any` in public APIs.
- Public functions get a docstring / TSDoc with a short example.
- Error messages say what is wrong and how to fix it, and include the file path and line when input came from a file.
