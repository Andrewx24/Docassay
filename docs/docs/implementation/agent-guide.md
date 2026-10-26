---
title: Agent guide (Grok Build)
sidebar_position: 2
---

# Agent guide

How a coding agent such as Grok Build should take a Linear issue from backlog to merged pull request. The same rules are in [`AGENTS.md`](https://github.com/Andrewx24/Docassay/blob/main/AGENTS.md) at the repo root, which most agents read automatically.

## Loop for one issue

1. **Pick** the lowest-numbered step in the [Build plan](build-plan.md) whose blockers are merged. Move the Linear issue to *In Progress*.
2. **Read** the issue, then every doc page it links. The docs are the spec; the issue is the scope. If they disagree, stop and comment on the issue instead of guessing.
3. **Branch** using the issue's branch name, e.g. `andyaliaj/ali-339-normalization-library-text-numbers-dates`.
4. **Write tests first** from the doc examples and fixtures, in both languages when the issue is labeled `python` and `typescript`.
5. **Implement** in both languages, mirroring module and function names.
6. **Run the checks** below until all pass.
7. **Open a PR** titled `ALI-339: Normalization library` with the template filled in. Linear links the PR from the issue id.
8. **Do not** mark the issue done; it closes when the PR merges.

## Checks to run before every push

```bash
# Python
cd python && poetry install --all-extras && poetry run ruff check . && poetry run ruff format --check . \
  && poetry run mypy src && poetry run pytest

# TypeScript
cd js && npm ci && npm run lint && npm run typecheck && npm test && npm run build

# Spec drift and parity (from repo root, once these scripts exist)
./scripts/gen-types.sh && git diff --exit-code
./scripts/parity.sh

# Docs (only if docs/ changed)
cd docs && npm ci && npm run build
```

## Hard rules

- **Never edit generated files** in `python/src/docassay/models/` or `js/src/models/`. Change `spec/` and regenerate.
- **Never change a schema without** updating its example in `spec/examples/` and the [Spec](../architecture/spec.md) page in the same PR.
- **Every scoring or normalization change needs parity fixtures** in `spec/fixtures/`.
- **No network in unit tests.** Adapter tests use recorded payloads; live tests are opt-in.
- **No new runtime dependency** without saying why in the PR description. Prefer the standard library.
- **No secrets** in code, fixtures or logs. Scrub API keys and personal data from recorded vendor payloads.
- **Keep PRs to one issue.** If you find other work, open or comment on a Linear issue instead.
- **Ask, don't guess,** when the docs are silent on a behavior that changes scores. Leave a comment on the issue and a `TODO(ALI-xxx)` in code.

## Prompt template for Grok Build

```text
You are working in the repo Andrewx24/Docassay. Read AGENTS.md first.
Implement Linear issue ALI-<n>: <title>.
Issue description:
<paste the issue body, including its "Implementation brief">
Follow the linked docs exactly; they are the spec. Work on branch <branch name>.
Write tests first, implement in every language the issue lists, run all checks in
AGENTS.md, then open a PR titled "ALI-<n>: <title>". Stop and report if a check
fails twice for the same reason or the docs contradict the issue.
```
