/**
 * Chunk-quality score.
 *
 * @example
 * ```ts
 * import { scoreChunks } from "./scoring/chunks.js";
 *
 * scoreChunks(document, expectations, { tokenizer: "words" });
 * ```
 */

import type { Chunk, Document, Table } from "../models/document.js";
import type { ChunkExpect } from "../models/golden.js";
import { normText, round4 } from "../normalize/index.js";
import { tokenCount } from "../tokenize/index.js";
import type { Failure, MetricResult, ScoreOptions } from "./index.js";

/**
 * Score chunk size, split tables, and heading paths.
 *
 * Absent `doc.chunks` scores 0 with `"parser produced no chunks"`.
 * Otherwise each present rule adds checks and the score is the fraction
 * that pass, rounded to 4 decimals. `min_tokens` skips the last chunk.
 * `chunk.tokens` is used when it is set. `extra.tokens` is the min, median,
 * and max of those counts and does not change the score. An even count uses
 * the mean of the two central values. No checks scores 1.
 *
 * @example
 * ```ts
 * scoreChunks(document, expectations, { tokenizer: "words" });
 * ```
 */
export function scoreChunks(
  doc: Document,
  expect: ChunkExpect,
  opts: ScoreOptions = {},
): MetricResult {
  if (doc.chunks == null) {
    return {
      score: round4(0),
      failures: [{ metric: "chunks", check: "chunks", reason: "parser produced no chunks" }],
    };
  }
  const tokenizer = opts.tokenizer ?? "words";
  const chunks = doc.chunks;
  const counts = chunks.map((chunk) => tokens(chunk, tokenizer));
  const failures: Failure[] = [];
  let passed = 0;
  let total = 0;
  const maxTokens = expect.max_tokens;
  if (maxTokens != null) {
    counts.forEach((count, index) => {
      total += 1;
      if (count <= maxTokens) {
        passed += 1;
        return;
      }
      failures.push(
        failure(`chunks[${index}].max_tokens`, "chunk exceeds max_tokens", maxTokens, count),
      );
    });
  }
  const minTokens = expect.min_tokens;
  if (minTokens != null) {
    counts.slice(0, -1).forEach((count, index) => {
      total += 1;
      if (count >= minTokens) {
        passed += 1;
        return;
      }
      failures.push(
        failure(`chunks[${index}].min_tokens`, "chunk below min_tokens", minTokens, count),
      );
    });
  }
  if (expect.no_split_tables) {
    doc.tables.forEach((table, index) => {
      total += 1;
      const owners = chunks.filter((chunk) => chunk.blockIds.includes(table.id));
      const cells = headerCells(table);
      if (owners.length <= 1 || owners.every((chunk) => containsCells(chunk, cells))) {
        passed += 1;
        return;
      }
      failures.push(
        failure(
          `tables[${index}]`,
          "table split without repeated header",
          cells,
          owners.map((chunk) => chunk.id),
        ),
      );
    });
  }
  expect.heading_paths?.forEach((item, index) => {
    total += 1;
    const found = heading(chunks, index, item.text, item.path);
    if (found === null) {
      passed += 1;
      return;
    }
    failures.push(found);
  });
  const score = total === 0 ? round4(1) : round4(passed / total);
  const extra = counts.length > 0 ? { tokens: distribution(counts) } : undefined;
  return extra === undefined ? { score, failures } : { score, failures, extra };
}

function tokens(chunk: Chunk, tokenizer: string): number {
  if (chunk.tokens != null) return chunk.tokens;
  return tokenCount(chunk.text, tokenizer);
}

function headerCells(table: Table): string[] {
  if (table.header != null) return [...table.header];
  const first = table.rows[0];
  return first === undefined ? [] : [...first];
}

function containsCells(chunk: Chunk, cells: readonly string[]): boolean {
  const haystack = normText(chunk.text);
  return cells.every((cell) => haystack.includes(normText(cell)));
}

function heading(
  chunks: readonly Chunk[],
  index: number,
  text: string,
  path: readonly string[],
): Failure | null {
  const needle = normText(text);
  for (const chunk of chunks) {
    if (!normText(chunk.text).includes(needle)) continue;
    const actual = chunk.headingPath === undefined ? null : [...chunk.headingPath];
    if (samePath(actual, path)) return null;
    return failure(`heading_paths[${index}]`, "heading path does not match", [...path], actual);
  }
  return failure(`heading_paths[${index}]`, "text not in any chunk", text, null);
}

function samePath(actual: readonly string[] | null, expected: readonly string[]): boolean {
  if (actual === null) return false;
  if (actual.length !== expected.length) return false;
  return actual.every((item, index) => normText(item) === normText(expected[index] ?? ""));
}

function distribution(counts: readonly number[]): { min: number; median: number; max: number } {
  const ordered = [...counts].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  const median =
    ordered.length % 2 === 1
      ? (ordered[middle] ?? 0)
      : ((ordered[middle - 1] ?? 0) + (ordered[middle] ?? 0)) / 2;
  return {
    min: ordered[0] ?? 0,
    median,
    max: ordered[ordered.length - 1] ?? 0,
  };
}

function failure(check: string, reason: string, expected: unknown, actual: unknown): Failure {
  return { metric: "chunks", check, reason, expected, actual };
}
