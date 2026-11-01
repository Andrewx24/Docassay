/**
 * Reading-order score.
 *
 * @example
 * ```ts
 * import { scoreOrder } from "./scoring/order.js";
 *
 * scoreOrder(document, [["Bill To", "Line Items"]]);
 * ```
 */

import type { Document } from "../models/document.js";
import { normText, round4 } from "../normalize/index.js";
import type { Failure, MetricResult, ScoreOptions } from "./index.js";
import { documentText } from "./text.js";

/**
 * Score pairwise "A before B" checks on the normalized document text.
 *
 * For each pair, the first string must occur in `normText(documentText(doc))`.
 * The second string must then occur at or after the end of that first match.
 * A repeated first string uses the earliest match. The score is the fraction
 * of pairs that pass, rounded to 4 decimals. An empty list scores 1.
 *
 * @example
 * ```ts
 * scoreOrder(document, [["Bill To", "Line Items"]]);
 * ```
 */
export function scoreOrder(
  doc: Document,
  expect: readonly (readonly [string, string])[],
  opts: ScoreOptions = {},
): MetricResult {
  void opts;
  if (expect.length === 0) return { score: round4(1), failures: [] };
  const raw = documentText(doc);
  const haystack = normText(raw);
  const failures: Failure[] = [];
  let passed = 0;
  expect.forEach((pair, index) => {
    const before = pair[0];
    const after = pair[1];
    const first = normText(before);
    const second = normText(after);
    const start = haystack.indexOf(first);
    const check = `reading_order[${index}]`;
    if (start < 0) {
      failures.push(orderFailure(check, "first string not found", before, after, raw));
      return;
    }
    if (haystack.indexOf(second, start + first.length) >= 0) {
      passed += 1;
      return;
    }
    const reason = haystack.indexOf(second) < 0 ? "second string not found" : "out of order";
    failures.push(orderFailure(check, reason, before, after, raw));
  });
  return { score: round4(passed / expect.length), failures };
}

function orderFailure(
  check: string,
  reason: string,
  before: string,
  after: string,
  raw: string,
): Failure {
  return {
    metric: "order",
    check,
    reason,
    expected: [before, after],
    actual: raw,
  };
}
