# Releasing

`/VERSION` is the version. `scripts/sync-version.sh` copies it into `python/pyproject.toml` and `js/package.json`. The release workflow refuses a tag that is not `v` plus that exact string.

## What the workflow does

`.github/workflows/release.yml` runs in two ways.

| Trigger | Publishes |
| --- | --- |
| Push a tag `v*` | PyPI and npm, then a GitHub release with generated notes |
| Actions → Release → Run workflow, dry run on | TestPyPI and `npm publish --dry-run` |

The tag path fails before CI when the tag disagrees with `/VERSION` or `scripts/sync-version.sh --check` fails. CI is `.github/workflows/ci.yml`, called with `workflow_call` so spec, Python, TypeScript, and parity all run. Publishing starts only after that CI succeeds.

A manual run with dry run turned off stops immediately. Production publishes only from a tag.

## One-time setup

These are owner steps. The workflow file does not create the publisher or the token.

1. On PyPI, add a trusted publisher for this repository, workflow `release.yml`, and the environment left empty (the job does not set one). The PyPI project name is `docassay`.
2. On TestPyPI, add a trusted publisher the same way, so the dry run can upload.
3. Create an npm access token that can publish `docassay` and store it as the repository secret `NPM_TOKEN`. npm provenance uses the workflow's `id-token: write` permission. The repository must be public.
4. Confirm the GitHub Actions `GITHUB_TOKEN` can create releases (`contents: write` is set on the publish job).

## Cut a release

1. Set `/VERSION` to the new version, for example `0.1.0`.
2. Run `scripts/sync-version.sh` and commit the version files plus `CHANGELOG.md`.
3. Wait until that commit is on `main` and CI is green.
4. Tag the commit and push the tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The tag must be `v` followed by the exact contents of `/VERSION`. `v0.1.0` matches `0.1.0`. `v0.1` does not.

5. Watch the Release workflow. It builds with `poetry build` in `python/` and `npm run build` in `js/`, publishes, then opens the GitHub release.

## Dry run

On the commit you want to rehearse, run the Release workflow from the Actions tab and leave **dry run** checked. That uploads the Python distributions to TestPyPI (`skip-existing` is on, so a repeated version does not fail the upload) and runs `npm publish --dry-run` without writing to the npm registry. The dry run still sends `NPM_TOKEN` so the registry can authenticate the check. It does not open a GitHub release. The version it uploads is whatever `/VERSION` says on that commit.

## Checks you can run locally

```bash
scripts/sync-version.sh --check
scripts/check-release-version.sh --tag v0.0.0   # use the version in /VERSION
bash scripts/test-release-version.sh
```

`scripts/test-release-version.sh` is also run by the spec job in CI.
