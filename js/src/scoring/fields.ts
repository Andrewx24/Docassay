/**
 * Extracted-field score.
 *
 * @example
 * ```ts
 * import { scoreFields } from "./scoring/fields.js";
 *
 * scoreFields(document, { total: 4120, vendor: "Acme Corp" });
 * ```
 */

import type { Document } from "../models/document.js";
import { normText, parseNumber, round4, valuesEqual } from "../normalize/index.js";
import type { Failure, MetricResult, ScoreOptions } from "./index.js";

/** A golden field value: string, number, boolean, or null. */
export type FieldValue = string | number | boolean | null;

const TRUE_WORDS = new Set(["true", "yes"]);
const FALSE_WORDS = new Set(["false", "no"]);

/**
 * Score extracted fields against the golden map, in golden key order.
 *
 * A missing `doc.fields` fails every key with `"parser returned no fields"`.
 * A missing key fails with `"field missing"`, except a null expectation,
 * which also passes when the value is null or blank after normalization.
 * Booleans accept the same boolean or the strings true/false/yes/no.
 * A golden number uses the numeric rule on that number and on the parsed
 * actual text. Strings use `valuesEqual`. A compared value that fails is
 * `"value does not match"`. The score is the fraction that pass, rounded
 * to 4 decimals. An empty map scores 1.
 *
 * @example
 * ```ts
 * scoreFields(document, { total: 4120 }, { decimal: "." });
 * ```
 */
export function scoreFields(
  doc: Document,
  expect: Readonly<Record<string, FieldValue>>,
  opts: ScoreOptions = {},
): MetricResult {
  const keys = Object.keys(expect);
  if (keys.length === 0) return { score: round4(1), failures: [] };
  const failures: Failure[] = [];
  let passed = 0;
  for (const key of keys) {
    const expected = expect[key] ?? null;
    if (doc.fields == null) {
      failures.push(fieldFailure(key, "parser returned no fields", expected, null));
      continue;
    }
    if (!Object.hasOwn(doc.fields, key)) {
      if (expected === null) {
        passed += 1;
        continue;
      }
      failures.push(fieldFailure(key, "field missing", expected, null));
      continue;
    }
    const actual = doc.fields[key]?.value ?? null;
    if (matches(expected, actual, opts)) {
      passed += 1;
      continue;
    }
    failures.push(fieldFailure(key, "value does not match", expected, actual));
  }
  return { score: round4(passed / keys.length), failures };
}

function matches(expected: FieldValue, actual: FieldValue, opts: ScoreOptions): boolean {
  if (expected === null) {
    if (actual === null) return true;
    return normText(stringify(actual)) === "";
  }
  if (typeof expected === "boolean") {
    if (typeof actual === "boolean") return actual === expected;
    if (typeof actual === "string") {
      const token = normText(actual);
      return expected ? TRUE_WORDS.has(token) : FALSE_WORDS.has(token);
    }
    return false;
  }
  if (typeof expected === "number") {
    return numbersEqual(expected, actual, opts);
  }
  if (actual === null) return false;
  return valuesEqual(stringify(expected), stringify(actual), {
    decimal: opts.decimal ?? ".",
    dateOrder: opts.dateOrder ?? "mdy",
  });
}

function numbersEqual(expected: number, actual: FieldValue, opts: ScoreOptions): boolean {
  if (typeof actual === "boolean" || actual === null) return false;
  if (typeof actual !== "number" && typeof actual !== "string") return false;
  const actualNumber =
    typeof actual === "number" ? actual : parseNumber(actual, opts.decimal ?? ".");
  if (actualNumber === null) return false;
  return Math.abs(expected - actualNumber) <= 1e-6 * Math.max(1, Math.abs(actualNumber));
}

function stringify(value: FieldValue): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (value === null) return "";
  return value;
}

function fieldFailure(key: string, reason: string, expected: FieldValue, actual: FieldValue): Failure {
  return {
    metric: "fields",
    check: `fields[${key}]`,
    reason,
    expected,
    actual,
  };
}
