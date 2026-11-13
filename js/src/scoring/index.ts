/**
 * Shared scorer results.
 *
 * @example
 * ```ts
 * import { type MetricResult } from "./scoring/index.js";
 *
 * const result: MetricResult = { score: 1, failures: [] };
 * ```
 */

export type MetricName = "tables" | "text" | "order" | "fields" | "chunks";
export type DecimalMark = "." | ",";
export type DateOrder = "mdy" | "dmy";

/** Options shared by every scorer. Defaults match the config file. */
export interface ScoreOptions {
  /** Decimal mark, `"."` or `","`. */
  decimal?: DecimalMark;
  /** Numeric date order. `mdy` or `dmy`. */
  dateOrder?: DateOrder;
  /** Tokenizer name. `words` or `tiktoken:<encoding>`. */
  tokenizer?: string;
}

/** One reason a score is below 1. */
export interface Failure {
  metric: MetricName;
  check: string;
  reason: string;
  expected?: unknown;
  actual?: unknown;
  page?: number;
}

/** A score in `[0, 1]` and the failures that explain anything below 1. */
export interface MetricResult {
  score: number;
  failures: Failure[];
  /** Report data that does not affect the score. Chunks use `tokens`. */
  extra?: Record<string, unknown>;
}

export { scoreTables } from "./tables.js";
export { documentText, scoreText } from "./text.js";
export { scoreOrder } from "./order.js";
export { scoreFields } from "./fields.js";
export { scoreChunks } from "./chunks.js";
