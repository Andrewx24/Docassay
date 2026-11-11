#!/usr/bin/env bash
# Fail a release whose tag disagrees with /VERSION, then check the package files.
#
#   scripts/check-release-version.sh
#   scripts/check-release-version.sh --tag v0.1.0
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tag=""
if [[ "${1:-}" == "--tag" ]]; then
  tag="${2:-}"
  if [[ -z "$tag" || -n "${3:-}" ]]; then
    echo "usage: scripts/check-release-version.sh [--tag vX.Y.Z]" >&2
    exit 2
  fi
elif [[ -n "${1:-}" ]]; then
  echo "usage: scripts/check-release-version.sh [--tag vX.Y.Z]" >&2
  exit 2
fi

version="$(tr -d '[:space:]' < "$root/VERSION")"
if [[ -z "$version" ]]; then
  echo "VERSION is empty. Write the release version in /VERSION, then run scripts/sync-version.sh." >&2
  exit 1
fi

if [[ -n "$tag" ]]; then
  case "$tag" in
    v*) ;;
    *)
      echo "tag ${tag} does not start with v. Push a tag like v${version}." >&2
      exit 1
      ;;
  esac
  bare="${tag#v}"
  if [[ "$bare" != "$version" ]]; then
    echo "tag ${tag} does not match /VERSION (${version})." >&2
    echo "Set /VERSION to ${bare}, run scripts/sync-version.sh, and commit, or push v${version}." >&2
    exit 1
  fi
fi

"$root/scripts/sync-version.sh" --check
