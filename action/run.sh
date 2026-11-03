#!/usr/bin/env bash
# Install the selected docassay package, run check or run, then comment.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT_ACTION_PATH="${INPUT_ACTION_PATH:-$here}"
INPUT_COMMAND="${INPUT_COMMAND:-check}"
INPUT_RUNTIME="${INPUT_RUNTIME:-python}"
INPUT_CONFIG="${INPUT_CONFIG:-docassay.yaml}"
INPUT_COMMENT="${INPUT_COMMENT:-false}"
INPUT_VERSION="${INPUT_VERSION:-}"
INPUT_ARGS="${INPUT_ARGS:-}"

finish() {
  local code="$1"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "exit_code=${code}" >>"$GITHUB_OUTPUT"
  fi
  exit "$code"
}

case "$INPUT_COMMAND" in
  run | check) ;;
  *)
    echo "command must be run or check (got ${INPUT_COMMAND})." >&2
    finish 2
    ;;
esac

case "$INPUT_RUNTIME" in
  python | node) ;;
  *)
    echo "python-or-node must be python or node (got ${INPUT_RUNTIME})." >&2
    finish 2
    ;;
esac

if [[ -n "$INPUT_VERSION" && "$INPUT_VERSION" != "local" && ! "$INPUT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "version must be empty, local, or a release like 0.1.0 (got ${INPUT_VERSION})." >&2
  finish 2
fi

repo_root="$(cd "${INPUT_ACTION_PATH}/.." && pwd)"

install_python() {
  if [[ "$INPUT_VERSION" == "local" ]]; then
    # Editable so schema files resolve from this checkout. A published wheel
    # does not carry spec/ until the package layout includes it.
    python -m pip install --disable-pip-version-check --editable "${repo_root}/python"
    return
  fi
  local spec="docassay"
  if [[ -n "$INPUT_VERSION" ]]; then
    spec="docassay==${INPUT_VERSION}"
  fi
  python -m pip install --disable-pip-version-check "$spec"
}

install_node() {
  local prefix="${RUNNER_TEMP:-$(mktemp -d)}/docassay-prefix"
  local spec="docassay"
  mkdir -p "$prefix"
  if [[ "$INPUT_VERSION" == "local" ]]; then
    npm ci --prefix "${repo_root}/js"
    npm run build --prefix "${repo_root}/js"
    spec="${repo_root}/js"
  elif [[ -n "$INPUT_VERSION" ]]; then
    spec="docassay@${INPUT_VERSION}"
  fi
  npm install -g --prefix "$prefix" "$spec"
  export PATH="${prefix}/bin:${PATH}"
}

if [[ "$INPUT_RUNTIME" == "python" ]]; then
  install_python
else
  install_node
fi
hash -r
echo "docassay $(docassay --version)"

extra=()
if [[ -n "$INPUT_ARGS" ]]; then
  read -r -a extra <<<"$INPUT_ARGS"
fi

workdir="${RUNNER_TEMP:-$(mktemp -d)}/docassay-run"
mkdir -p "$workdir"
stdout_file="${workdir}/stdout.txt"
md_file="${workdir}/report.md"
comment_file="${workdir}/comment.md"

set +e
if [[ "$INPUT_COMMAND" == "check" ]]; then
  docassay check -c "$INPUT_CONFIG" "${extra[@]}" >"$stdout_file"
else
  docassay run -c "$INPUT_CONFIG" "${extra[@]}" >"$stdout_file"
fi
code=$?
set -e
cat "$stdout_file"

if [[ "$INPUT_COMMENT" == "true" ]]; then
  report_args=()
  skip_value=0
  for arg in "${extra[@]}"; do
    if [[ "$skip_value" -eq 1 ]]; then
      skip_value=0
      continue
    fi
    case "$arg" in
      --refresh | --no-cache | --summary-only) ;;
      --baseline | --format | -o | --out)
        skip_value=1
        ;;
      --baseline=* | --format=* | --out=*) ;;
      *) report_args+=("$arg") ;;
    esac
  done
  set +e
  docassay run -c "$INPUT_CONFIG" "${report_args[@]}" --format md >"$md_file"
  md_code=$?
  set -e
  gate_arg="-"
  md_arg="-"
  if [[ "$INPUT_COMMAND" == "check" ]]; then
    gate_arg="$stdout_file"
  fi
  if [[ "$md_code" -eq 0 ]]; then
    md_arg="$md_file"
  elif [[ "$INPUT_COMMAND" == "run" ]]; then
    md_arg="$stdout_file"
  fi
  python3 "${here}/render_comment.py" "$gate_arg" "$md_arg" "$comment_file"
  comment_code=0
  REPORT_FILE="$comment_file" bash "${here}/comment.sh" || comment_code=$?
  if [[ "$code" -eq 0 && "$comment_code" -ne 0 ]]; then
    code=$comment_code
  fi
fi

finish "$code"
