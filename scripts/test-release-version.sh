#!/usr/bin/env bash
# Exercise the release version gate against this checkout.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
check="$root/scripts/check-release-version.sh"
version="$(tr -d '[:space:]' < "$root/VERSION")"

"$check"
"$check" --tag "v${version}"

set +e
mismatch="$("$check" --tag v9.9.9 2>&1)"
mismatch_code=$?
notag="$("$check" --tag 0.0.0 2>&1)"
notag_code=$?
usage="$("$check" --nope 2>&1)"
usage_code=$?
set -e

if [[ "$mismatch_code" -ne 1 ]] || [[ "$mismatch" != *"does not match /VERSION"* ]]; then
  echo "expected a mismatch for v9.9.9, got ${mismatch_code}: ${mismatch}" >&2
  exit 1
fi
if [[ "$notag_code" -ne 1 ]] || [[ "$notag" != *"does not start with v"* ]]; then
  echo "expected a bare version to be rejected, got ${notag_code}: ${notag}" >&2
  exit 1
fi
if [[ "$usage_code" -ne 2 ]] || [[ "$usage" != *"usage:"* ]]; then
  echo "expected usage exit 2, got ${usage_code}: ${usage}" >&2
  exit 1
fi

set +e
empty="$("$check" --tag 2>&1)"
empty_code=$?
extra="$("$check" --tag "v${version}" extra 2>&1)"
extra_code=$?
set -e
if [[ "$empty_code" -ne 2 ]] || [[ "$extra_code" -ne 2 ]]; then
  echo "expected usage exit 2 for a missing or extra tag argument" >&2
  echo "missing (${empty_code}): ${empty}" >&2
  echo "extra (${extra_code}): ${extra}" >&2
  exit 1
fi

echo "release version gate ok"
