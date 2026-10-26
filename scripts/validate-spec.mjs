#!/usr/bin/env node
/**
 * Validate spec/examples against the JSON Schemas, and require spec/invalid
 * cases to fail. YAML is read with the YAML 1.2 core schema so unquoted dates
 * stay strings.
 *
 *   node scripts/validate-spec.mjs
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { parse } from "yaml";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const specDir = join(root, "spec");
const names = ["document", "golden", "config", "scorecard", "baseline"];

const ajv = new Ajv({ allErrors: true, strict: true, allowUnionTypes: true });
addFormats(ajv);

for (const name of names) {
  const schema = JSON.parse(readFileSync(join(specDir, `${name}.schema.json`), "utf8"));
  ajv.addSchema(schema);
}

const failures = [];

function load(path) {
  const text = readFileSync(path, "utf8");
  if (path.endsWith(".json")) return JSON.parse(text);
  return parse(text, { schema: "core" });
}

function errorsOf(validate) {
  return (validate.errors ?? [])
    .map((err) => `${err.instancePath || "/"} ${err.message}`)
    .join("; ");
}

for (const name of names) {
  const validate = ajv.getSchema(`https://docassay.dev/spec/1.0/${name}.schema.json`);
  if (!validate) {
    failures.push(`${name}: schema was not registered`);
    continue;
  }

  for (const ext of ["json", "yaml"]) {
    const path = join(specDir, "examples", `${name}.example.${ext}`);
    let data;
    try {
      data = load(path);
    } catch (err) {
      failures.push(`${path}: ${err.message}`);
      continue;
    }
    if (!validate(data)) {
      failures.push(`${path}: expected valid: ${errorsOf(validate)}`);
    }
  }

  const invalidDir = join(specDir, "invalid", name);
  const files = readdirSync(invalidDir).filter((file) => /\.(json|ya?ml)$/.test(file));
  if (files.length < 3) {
    failures.push(`${invalidDir}: need at least 3 invalid examples, found ${files.length}`);
  }
  for (const file of files) {
    const path = join(invalidDir, file);
    let data;
    try {
      data = load(path);
    } catch (err) {
      failures.push(`${path}: ${err.message}`);
      continue;
    }
    if (validate(data)) {
      failures.push(`${path}: expected this example to fail validation`);
    }
  }
}

if (failures.length > 0) {
  for (const failure of failures) console.error(failure);
  process.exit(1);
}

console.log(`spec ok (${names.length} schemas, examples valid, invalid examples rejected)`);
