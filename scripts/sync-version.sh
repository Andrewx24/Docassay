#!/usr/bin/env bash
# Write /VERSION into python/pyproject.toml and, when it exists, js/package.json.
#   scripts/sync-version.sh           # update the files
#   scripts/sync-version.sh --check   # exit 1 if an existing file disagrees
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(tr -d '[:space:]' < "$root/VERSION")"
mode="write"
if [[ "${1:-}" == "--check" ]]; then
  mode="check"
elif [[ -n "${1:-}" ]]; then
  echo "usage: scripts/sync-version.sh [--check]" >&2
  exit 2
fi

python3 - "$root" "$version" "$mode" << 'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = sys.argv[2]
check = sys.argv[3] == "check"
failed = False


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    global failed
    text = path.read_text()
    new, count = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if count != 1:
        print(f"{path}: could not find a version field", file=sys.stderr)
        failed = True
        return
    if new == text:
        return
    if check:
        print(f"{path}: version is not {version}", file=sys.stderr)
        failed = True
        return
    path.write_text(new)
    print(f"{path}: {version}")


pyproject = root / "python" / "pyproject.toml"
replace_once(pyproject, r'^version = "[^"]+"', f'version = "{version}"')

package_json = root / "js" / "package.json"
if package_json.exists():
    replace_once(package_json, r'"version": "[^"]+"', f'"version": "{version}"')

sys.exit(1 if failed else 0)
PY
