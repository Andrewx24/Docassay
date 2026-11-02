/**
 * Text containment score.
 *
 * @example
 * ```ts
 * import { documentText, scoreText } from "./scoring/text.js";
 *
 * scoreText(document, ["Payment due"]);
 * documentText(document);
 * ```
 */

import type { Document, Table } from "../models/document.js";
import { normText, round4 } from "../normalize/index.js";
import type { Failure, MetricResult, ScoreOptions } from "./index.js";

/**
 * Build the full document text used by text and reading-order scores.
 *
 * Block texts stay in array order and are joined with `"\n\n"`, including
 * blocks whose text is empty. Each table contributes its header row when
 * `header` is present, then its body rows. Cells in a row are joined with
 * `" | "` and rows with `"\n"`. Tables are joined with `"\n\n"`. The block
 * text and the table text are joined with `"\n\n"` when each side is a
 * non-empty string. A table's `markdown` is left out.
 *
 * @example
 * ```ts
 * documentText(document);
 * ```
 */
export function documentText(doc: Document): string {
  const parts: string[] = [];
  if (doc.blocks.length > 0) {
    parts.push(doc.blocks.map((block) => block.text).join("\n\n"));
  }
  if (doc.tables.length > 0) {
    parts.push(doc.tables.map((table) => renderTable(table)).join("\n\n"));
  }
  return parts.filter((part) => part !== "").join("\n\n");
}

/**
 * Score the fraction of strings found in the normalized document text.
 *
 * `normText(s)` must be a substring of `normText(documentText(doc))`.
 * Matching is exact after normalization. The score is that fraction, rounded
 * to 4 decimals. An empty list scores 1.
 *
 * @example
 * ```ts
 * scoreText(document, ["Payment due within 30 days"]);
 * ```
 */
export function scoreText(
  doc: Document,
  expect: readonly string[],
  opts: ScoreOptions = {},
): MetricResult {
  void opts;
  if (expect.length === 0) return { score: round4(1), failures: [] };
  const raw = documentText(doc);
  const haystack = normText(raw);
  const failures: Failure[] = [];
  let passed = 0;
  expect.forEach((item, index) => {
    if (haystack.includes(normText(item))) {
      passed += 1;
      return;
    }
    failures.push({
      metric: "text",
      check: `text_contains[${index}]`,
      reason: "text not found",
      expected: item,
      actual: raw,
    });
  });
  return { score: round4(passed / expect.length), failures };
}

function renderTable(table: Table): string {
  const lines: string[] = [];
  if (table.header != null) lines.push(table.header.join(" | "));
  for (const row of table.rows) lines.push(row.join(" | "));
  return lines.join("\n");
}
