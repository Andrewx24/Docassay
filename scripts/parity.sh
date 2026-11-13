#!/usr/bin/env bash
# Score every shared fixture in Python and TypeScript, then diff the golden
# example scorecard. Numbers match at 4 decimal places. Exits 1 on mismatch.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ ! -f js/dist/parity.js || ! -f js/dist/cli.js ]]; then
  echo "js/dist/parity.js is missing. From js/, run: npm ci && npm run build" >&2
  exit 1
fi

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry is not on PATH. Install Poetry 2.5.1, then from python/ run: poetry install" >&2
  exit 1
fi

py_bin="$(poetry -C "$root/python" env info -e 2>/dev/null || true)"
if [[ -z "$py_bin" || ! -x "$py_bin" ]]; then
  echo "Python package is not installed. From python/, run: poetry install" >&2
  exit 1
fi
docassay_bin="$(dirname "$py_bin")/docassay"
if [[ ! -x "$docassay_bin" ]]; then
  echo "docassay is not installed in ${py_bin}. From python/, run: poetry install" >&2
  exit 1
fi

status=0
shopt -s nullglob
cases=("$root"/spec/fixtures/*/document.json)
if [[ ${#cases[@]} -eq 0 ]]; then
  echo "no spec/fixtures/<case>/document.json files found" >&2
  exit 1
fi

for doc in "${cases[@]}"; do
  name="$(basename "$(dirname "$doc")")"
  py_out="$(mktemp)"
  js_out="$(mktemp)"
  if ! "$py_bin" -m docassay._parity "$name" >"$py_out"; then
    echo "python failed on $name" >&2
    status=1
  elif ! node "$root/js/dist/parity.js" "$name" >"$js_out"; then
    echo "javascript failed on $name" >&2
    status=1
  elif ! "$py_bin" -m docassay._parity --diff "$name" "$py_out" "$js_out"; then
    status=1
  fi
  rm -f "$py_out" "$js_out"
done

py_card="$(mktemp)"
js_card="$(mktemp)"
if ! "$docassay_bin" run -c "$root/golden/docassay.yaml" --format json >"$py_card"; then
  echo "python docassay run failed on golden/docassay.yaml" >&2
  status=1
elif ! node "$root/js/dist/cli.js" run -c "$root/golden/docassay.yaml" --format json >"$js_card"; then
  echo "javascript docassay run failed on golden/docassay.yaml" >&2
  status=1
elif ! "$py_bin" -m docassay._parity --diff-scorecard "$py_card" "$js_card"; then
  status=1
fi
rm -f "$py_card" "$js_card"

exit "$status"
