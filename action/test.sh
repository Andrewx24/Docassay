#!/usr/bin/env bash
# Exercise the comment body, the GitHub comment update, and command validation.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
action="${root}/action"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

python3 "${action}/render_comment.py" - - "${tmp}/empty.md"
grep -qx '<!-- docassay-report -->' "${tmp}/empty.md"

printf '%s\n' '<!-- docassay-report -->' '' '# docassay 0.0.0' >"${tmp}/score.md"
printf '%s\n' 'FAIL  file · invoice-acme · tables 1.0000 → 0.5000' >"${tmp}/gate.txt"
python3 "${action}/render_comment.py" "${tmp}/gate.txt" "${tmp}/score.md" "${tmp}/comment.md"
head -n 1 "${tmp}/comment.md" | grep -qx '<!-- docassay-report -->'
grep -q '## Check' "${tmp}/comment.md"
grep -q 'FAIL  file · invoice-acme' "${tmp}/comment.md"
grep -q '# docassay 0.0.0' "${tmp}/comment.md"
# The marker stays on the first line so the next run finds this comment.
test "$(grep -c '<!-- docassay-report -->' "${tmp}/comment.md")" -eq 1

python3 -c 'open("/dev/stdout","w").write("x"*70000)' >"${tmp}/big.md"
python3 "${action}/render_comment.py" - "${tmp}/big.md" "${tmp}/truncated.md"
python3 -c 'import pathlib,sys; text=pathlib.Path(sys.argv[1]).read_text(); assert text.startswith("<!-- docassay-report -->"); assert len(text) <= 65000; assert "truncated" in text' "${tmp}/truncated.md"

# comment.sh posts, then edits the comment that carries the marker.
fake="${tmp}/bin"
mkdir -p "$fake"
cat >"${fake}/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${GH_LOG:?}"
if [[ "$*" == *"--method PATCH"* || "$*" == *"--method POST"* ]]; then
  cat >"${GH_BODY:?}"
  exit 0
fi
cat "${GH_RESPONSE:?}"
EOF
chmod +x "${fake}/gh" "${action}/comment.sh" "${action}/run.sh"

export PATH="${fake}:${PATH}"
export GH_LOG="${tmp}/gh.log"
export GH_BODY="${tmp}/gh-body.json"
export GH_RESPONSE="${tmp}/gh-response.json"
export INPUT_COMMENT=true
export GITHUB_EVENT_NAME=pull_request
export PR_NUMBER=24
export GITHUB_REPOSITORY=Andrewx24/Docassay
export GITHUB_TOKEN=test-token
export REPORT_FILE="${tmp}/comment.md"

printf '%s\n' '[]' >"$GH_RESPONSE"
: >"$GH_LOG"
bash "${action}/comment.sh"
grep -q -- '--method POST' "$GH_LOG"
python3 -c 'import json,sys; body=json.load(open(sys.argv[1]))["body"]; assert body.startswith("<!-- docassay-report -->")' "$GH_BODY"

printf '%s\n' '[{"id":7,"body":"<!-- docassay-report -->\nold"}]' >"$GH_RESPONSE"
: >"$GH_LOG"
bash "${action}/comment.sh"
grep -q '/issues/comments/7' "$GH_LOG"
grep -q -- '--method PATCH' "$GH_LOG"
if grep -q -- '--method POST' "$GH_LOG"; then
  echo "edited comment was posted a second time" >&2
  exit 1
fi

: >"$GH_LOG"
INPUT_COMMENT=false bash "${action}/comment.sh"
test ! -s "$GH_LOG"

: >"$GH_LOG"
GITHUB_EVENT_NAME=push bash "${action}/comment.sh"
test ! -s "$GH_LOG"

set +e
GITHUB_EVENT_NAME=pull_request GITHUB_TOKEN= GH_TOKEN= bash "${action}/comment.sh"
missing=$?
set -e
test "$missing" -eq 2

set +e
INPUT_COMMAND=score INPUT_RUNTIME=python INPUT_VERSION= bash "${action}/run.sh"
bad_command=$?
INPUT_COMMAND=check INPUT_RUNTIME=ruby INPUT_VERSION= bash "${action}/run.sh"
bad_runtime=$?
INPUT_COMMAND=check INPUT_RUNTIME=python INPUT_VERSION='0.1.0;rm -rf /' bash "${action}/run.sh"
bad_version=$?
set -e
test "$bad_command" -eq 2
test "$bad_runtime" -eq 2
test "$bad_version" -eq 2

# Install the checkout and require a regression to exit 1 after the comment is posted.
# The package requires Python >=3.10,<3.14. ubuntu-latest's python3 may be newer.
supported_python() {
  local candidate version
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    case "$version" in
      3.10|3.11|3.12|3.13) printf '%s\n' "$candidate"; return 0 ;;
    esac
  done
  return 1
}

py_bin=""
if command -v poetry >/dev/null 2>&1; then
  py_bin="$(dirname "$(poetry -C "${root}/python" env info -e)")"
elif py="$(supported_python)"; then
  if "$py" -m venv "${tmp}/ci-venv"; then
    "${tmp}/ci-venv/bin/python" -m pip install --disable-pip-version-check --editable "${root}/python"
    py_bin="${tmp}/ci-venv/bin"
  fi
fi
if [[ -n "$py_bin" ]]; then
  bad_base="${tmp}/bad-baseline.json"
  python3 -c '
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
doc["parsers"][0]["summary"]["tables"] = 1
doc["parsers"][0]["documents"][0]["scores"]["tables"] = 1
json.dump(doc, open(sys.argv[2], "w", encoding="utf-8"))
' "${root}/golden/.docassay/baseline.json" "$bad_base"
  printf '%s\n' '[]' >"$GH_RESPONSE"
  : >"$GH_LOG"
  set +e
  PATH="${py_bin}:${fake}:${PATH}" \
    INPUT_COMMAND=check \
    INPUT_RUNTIME=python \
    INPUT_VERSION=local \
    INPUT_CONFIG="${root}/golden/docassay.yaml" \
    INPUT_COMMENT=true \
    INPUT_ARGS="--baseline ${bad_base}" \
    INPUT_ACTION_PATH="${action}" \
    GITHUB_EVENT_NAME=pull_request \
    GITHUB_OUTPUT="${tmp}/output.txt" \
    bash "${action}/run.sh"
  regressed=$?
  set -e
  test "$regressed" -eq 1
  grep -q 'exit_code=1' "${tmp}/output.txt"
  grep -q -- '--method POST' "$GH_LOG"
  grep -q 'FAIL' "$GH_BODY"
else
  echo "skipping the install regression check; python venv is unavailable"
fi

echo "action tests passed"
