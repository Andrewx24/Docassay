# python/

The `docassay` package for PyPI, built with Poetry.

```bash
cd python
poetry install --all-extras
poetry run docassay --version
poetry run ruff check .
poetry run mypy src
poetry run pytest
```

`/VERSION` is the source of truth. `scripts/sync-version.sh` copies it into `pyproject.toml`.

Layout and naming: [docs/docs/architecture/repo-layout.md](../docs/docs/architecture/repo-layout.md)
Packaging: [docs/docs/architecture/packaging.md](../docs/docs/architecture/packaging.md)
