#!/usr/bin/env bash
# Post the docassay report, or edit the comment that already contains the marker.
set -euo pipefail

if [[ "${INPUT_COMMENT:-false}" != "true" ]]; then
  exit 0
fi

if [[ "${GITHUB_EVENT_NAME:-}" != "pull_request" || -z "${PR_NUMBER:-}" ]]; then
  echo "comment: true applies on pull_request events. Skipping the comment."
  exit 0
fi

if [[ -z "${GITHUB_TOKEN:-${GH_TOKEN:-}}" ]]; then
  echo "comment: true needs GITHUB_TOKEN. Grant pull-requests: write on the workflow." >&2
  exit 2
fi
export GH_TOKEN="${GH_TOKEN:-$GITHUB_TOKEN}"

repo="${GITHUB_REPOSITORY:?Set GITHUB_REPOSITORY to owner/name.}"
report="${REPORT_FILE:?Set REPORT_FILE to the comment body.}"
marker="<!-- docassay-report -->"
if ! grep -q "$marker" "$report"; then
  echo "The report is missing ${marker}, so a later run could not find this comment." >&2
  exit 2
fi

payload="$(python3 -c 'import json,sys; print(json.dumps({"body": open(sys.argv[1], encoding="utf-8").read()}))' "$report")"

existing="$(
  gh api --paginate "/repos/${repo}/issues/${PR_NUMBER}/comments" \
    | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
data = json.loads(raw)
if isinstance(data, dict):
    data = [data]
for item in data:
    body = item.get("body") or ""
    if "<!-- docassay-report -->" in body and item.get("id") is not None:
        print(item["id"])
        break
'
)"

if [[ -n "$existing" && ! "$existing" =~ ^[0-9]+$ ]]; then
  echo "GitHub returned a comment id that is not a number: ${existing}" >&2
  exit 2
fi

if [[ -n "$existing" ]]; then
  echo "Updating docassay comment ${existing}."
  printf '%s' "$payload" | gh api --method PATCH --input - "/repos/${repo}/issues/comments/${existing}" >/dev/null
else
  echo "Posting a new docassay comment."
  printf '%s' "$payload" | gh api --method POST --input - "/repos/${repo}/issues/${PR_NUMBER}/comments" >/dev/null
fi
