# Changelog

Versions match `/VERSION`. Before 1.0, a minor version may change behavior. A patch version does not change a score.

## Unreleased

- Release workflow: a `v*` tag publishes the Python package to PyPI and the TypeScript package to npm after CI passes, then opens a GitHub release. A manual dry run publishes to TestPyPI and runs `npm publish --dry-run`.
- Implements spec 1.0.0.

## 0.0.0

Nothing is published. The first published version is the one in `/VERSION` when its `v*` tag is pushed. See `RELEASING.md`.
