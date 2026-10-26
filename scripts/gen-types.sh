#!/usr/bin/env bash
# Generate Python and TypeScript models from spec/*.schema.json.
# Safe to run before the packages exist: this script creates the output folders.
#
#   Python:  pip install 'datamodel-code-generator>=0.26'
#   Node:    npm ci --prefix scripts
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
py_out="$root/python/src/docassay/models"
js_out="$root/js/src/models"
json2ts="$root/scripts/node_modules/.bin/json2ts"

mkdir -p "$py_out" "$js_out"

if ! command -v datamodel-codegen >/dev/null 2>&1; then
  echo "datamodel-codegen not found. Install with: pip install 'datamodel-code-generator>=0.26'" >&2
  exit 1
fi
if [[ ! -x "$json2ts" ]]; then
  echo "json2ts not found. Run: npm ci --prefix scripts" >&2
  exit 1
fi

names=(document golden config scorecard baseline)

for name in "${names[@]}"; do
  datamodel-codegen \
    --input "$root/spec/${name}.schema.json" \
    --input-file-type jsonschema \
    --output "$py_out/${name}.py" \
    --output-model-type pydantic_v2.BaseModel \
    --target-python-version 3.10 \
    --use-schema-description \
    --snake-case-field \
    --use-double-quotes \
    --disable-timestamp \
    --formatters black isort \
    --enum-field-as-literal one
  "$json2ts" \
    --input "$root/spec/${name}.schema.json" \
    --output "$js_out/${name}.ts" \
    --bannerComment "/* Generated from spec/${name}.schema.json by scripts/gen-types.sh. Do not edit. */"
done

cat > "$py_out/__init__.py" << 'EOF'
"""Generated from spec/*.schema.json by scripts/gen-types.sh. Do not edit."""

from .baseline import Baseline
from .config import Config
from .document import Document
from .golden import GoldenFile
from .scorecard import Scorecard

__all__ = ["Baseline", "Config", "Document", "GoldenFile", "Scorecard"]
EOF

echo "generated models in python/src/docassay/models and js/src/models"
