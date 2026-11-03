# docassay GitHub Action

Composite action that installs [docassay](https://github.com/Andrewx24/Docassay) and runs `check` or `run`. On a pull request it can post the Markdown scorecard as one comment, then edit that same comment on later pushes. The comment starts with `<!-- docassay-report -->`.

Marketplace publishing is an owner step after `v0.1.0`. Until that release, use this action from the repository (`uses: ./action`) or pin a commit.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `command` | `check` | `run` or `check` |
| `python-or-node` | `python` | Install the Python package or the Node package |
| `config` | `docassay.yaml` | Config path in the workspace |
| `comment` | `false` | Post or edit the scorecard comment on a pull request |
| `version` | latest release | `0.1.0`, empty for the latest release, or `local` for this checkout |
| `args` | empty | Extra arguments, split on spaces. Example: `--refresh` |

`version: local` installs the Python package in editable mode so `spec/` resolves from this checkout. A PyPI wheel does not include `spec/` yet, so pin a published version only after that layout ships.

The step exits with the CLI's exit code. Exit 1 is a regression. Exit 2 is a config or usage error. Exit 3 means a parser was unavailable. Warnings do not fail the step.

`comment: true` needs `pull-requests: write`. The comment is posted even when `check` finds a regression, and the step still fails with exit 1. On a push that is not a pull request, the comment is skipped.

`args` is appended to the command. `--refresh` re-parses and is intended for the nightly drift workflow. The comment's scorecard is rendered from the cache that command just wrote, so vendor APIs are not called a second time.

## Pull request

```yaml
name: docassay
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Andrewx24/Docassay/action@v0
        with:
          command: check
          python-or-node: python
          comment: true
          version: "0.1.0"
```

Copy-paste forms of this workflow and the nightly workflow live in [`examples/workflows/`](../examples/workflows/).

## Nightly vendor drift

Schedule `check --refresh` on the default branch. A regression opens an issue (or comments on the open drift issue) and does not fail a pull request. Exit 2 and exit 3 still fail the scheduled job so a broken config is not filed as drift.

The workflow needs the vendor credentials your adapters read, as repository secrets. It does not post a pull request comment.
