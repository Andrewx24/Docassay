/**
 * Table structure and cell accuracy.
 *
 * @example
 * ```ts
 * import { scoreTables } from "./scoring/tables.js";
 *
 * const result = scoreTables(document, expectations, {});
 * ```
 */

import type { Document, Table } from "../models/document.js";
import type { CellExpect, TableExpect } from "../models/golden.js";
import { normText, round4, valuesEqual } from "../normalize/index.js";
import type { Failure, MetricResult, ScoreOptions } from "./index.js";

const HEADER_WEIGHT = 0.3;
const SHAPE_WEIGHT = 0.2;
const CELL_WEIGHT = 0.5;

/**
 * Score predicted tables against golden table expectations.
 *
 * Tables are matched in golden order. A labeled header matches the unused
 * table on that page with the highest header similarity, and the first table
 * wins a tie. Similarity below 0.5 does not match. With no header label, the
 * highest cell score wins. The document score is the mean of the per-table
 * scores, rounded to 4 decimals.
 *
 * @example
 * ```ts
 * scoreTables(document, expectations, { decimal: "." });
 * ```
 */
export function scoreTables(
  doc: Document,
  expect: readonly TableExpect[],
  opts: ScoreOptions = {},
): MetricResult {
  if (expect.length === 0) return { score: round4(1), failures: [] };
  const used = new Set<number>();
  const scores: number[] = [];
  const failures: Failure[] = [];
  expect.forEach((item, index) => {
    const scored = scoreOne(doc.tables, used, index, item, opts);
    scores.push(scored.score);
    failures.push(...scored.failures);
  });
  const mean = scores.reduce((sum, score) => sum + score, 0) / scores.length;
  return { score: round4(mean), failures };
}

function scoreOne(
  tables: readonly Table[],
  used: Set<number>,
  index: number,
  item: TableExpect,
  options: ScoreOptions,
): { score: number; failures: Failure[] } {
  const page = pageOf(item);
  const candidates = tables.flatMap((table, position) =>
    used.has(position) || (page !== undefined && table.page !== page) ? [] : [position],
  );
  const check = `tables[${index}]`;
  if (candidates.length === 0) {
    const reason = page === undefined ? "no table found" : `no table found on page ${page}`;
    return { score: 0, failures: [failure(check, reason, page)] };
  }
  const expectedHeader = item.header;
  let best: number;
  if (expectedHeader !== undefined) {
    best = bestBy(candidates, (position) => headerSim(expectedHeader, tables[position]));
    const chosen = tables[best];
    const similarity = headerSim(expectedHeader, chosen);
    if (similarity < 0.5) {
      return {
        score: 0,
        failures: [
          failure(check, "no table with matching header", page, {
            expected: [...expectedHeader],
            actual: chosen === undefined ? [] : effective(chosen).header,
          }),
        ],
      };
    }
  } else {
    best = bestBy(candidates, (position) => cellFraction(tables[position], item, options));
  }
  used.add(best);
  const matched = tables[best];
  if (matched === undefined) {
    return { score: 0, failures: [failure(check, "no table found", page)] };
  }
  return components(matched, index, item, options);
}

function components(
  table: Table,
  index: number,
  item: TableExpect,
  options: ScoreOptions,
): { score: number; failures: Failure[] } {
  const { header, body } = effective(table);
  const page = pageOf(item);
  let total = 0;
  let weight = 0;
  const failures: Failure[] = [];
  if (item.header !== undefined) {
    const similarity = headerSim(item.header, table);
    total += HEADER_WEIGHT * similarity;
    weight += HEADER_WEIGHT;
    if (similarity < 1) {
      failures.push(
        failure(`tables[${index}].header`, "header does not match", page, {
          expected: [...item.header],
          actual: header,
        }),
      );
    }
  }
  const shape = shapeScore(header, body, item);
  if (shape !== null) {
    total += SHAPE_WEIGHT * shape;
    weight += SHAPE_WEIGHT;
    if (shape < 1) {
      failures.push(
        failure(`tables[${index}].shape`, "shape does not match", page, {
          expected: shapeExpected(item),
          actual: shapeActual(header, body, item),
        }),
      );
    }
  }
  if (item.cells !== undefined) {
    const cells = cellScore(header, body, index, item, options);
    total += CELL_WEIGHT * cells.fraction;
    weight += CELL_WEIGHT;
    failures.push(...cells.failures);
  }
  return { score: weight === 0 ? 1 : total / weight, failures };
}

function headerSim(expected: readonly string[], table: Table | undefined): number {
  if (table === undefined) return 0;
  return multisetSim(expected, effective(table).header);
}

function multisetSim(expected: readonly string[], actual: readonly string[]): number {
  if (expected.length === 0 && actual.length === 0) return 1;
  const denom = Math.max(expected.length, actual.length);
  const counts = new Map<string, number>();
  for (const cell of expected) {
    const key = normText(cell);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  let matched = 0;
  for (const cell of actual) {
    const key = normText(cell);
    const have = counts.get(key) ?? 0;
    if (have > 0) {
      matched += 1;
      counts.set(key, have - 1);
    }
  }
  return matched / denom;
}

function effective(table: Table): { header: string[]; body: string[][] } {
  if (table.header != null) return { header: [...table.header], body: table.rows };
  const first = table.rows[0];
  if (first === undefined) return { header: [], body: [] };
  return { header: [...first], body: table.rows.slice(1) };
}

function shapeScore(
  header: readonly string[],
  body: readonly (readonly string[])[],
  item: TableExpect,
): number | null {
  const parts: number[] = [];
  if (item.rows !== undefined) parts.push(dimension(body.length, item.rows));
  if (item.cols !== undefined) parts.push(dimension(actualCols(header, body), item.cols));
  if (parts.length === 0) return null;
  return parts.reduce((sum, part) => sum + part, 0) / parts.length;
}

function dimension(actual: number, expected: number): number {
  if (expected === 0) return actual === 0 ? 1 : 0;
  let delta = Math.abs(actual - expected) / expected;
  if (delta > 1) delta = 1;
  return 1 - delta;
}

function actualCols(header: readonly string[], body: readonly (readonly string[])[]): number {
  if (header.length > 0) return header.length;
  let longest = 0;
  for (const row of body) if (row.length > longest) longest = row.length;
  return longest;
}

function shapeExpected(item: TableExpect): { rows?: number; cols?: number } {
  const payload: { rows?: number; cols?: number } = {};
  if (item.rows !== undefined) payload.rows = item.rows;
  if (item.cols !== undefined) payload.cols = item.cols;
  return payload;
}

function shapeActual(
  header: readonly string[],
  body: readonly (readonly string[])[],
  item: TableExpect,
): { rows?: number; cols?: number } {
  const payload: { rows?: number; cols?: number } = {};
  if (item.rows !== undefined) payload.rows = body.length;
  if (item.cols !== undefined) payload.cols = actualCols(header, body);
  return payload;
}

function cellFraction(table: Table | undefined, item: TableExpect, options: ScoreOptions): number {
  if (table === undefined) return 0;
  const { header, body } = effective(table);
  const cells = item.cells ?? [];
  if (cells.length === 0) return 1;
  const passed = cells.filter(
    (cell) => readCell(header, body, cell, item.header, options).ok,
  ).length;
  return passed / cells.length;
}

function cellScore(
  header: readonly string[],
  body: readonly (readonly string[])[],
  index: number,
  item: TableExpect,
  options: ScoreOptions,
): { fraction: number; failures: Failure[] } {
  const cells = item.cells ?? [];
  if (cells.length === 0) return { fraction: 1, failures: [] };
  const page = pageOf(item);
  const failures: Failure[] = [];
  let passed = 0;
  cells.forEach((cell, cellIndex) => {
    const read = readCell(header, body, cell, item.header, options);
    if (read.ok) {
      passed += 1;
      return;
    }
    failures.push(
      failure(
        `tables[${index}].cells[${cellIndex}]`,
        read.actual === null ? "cell out of range" : "value not found in matched table",
        page,
        { expected: cell.value, actual: read.actual },
      ),
    );
  });
  return { fraction: passed / cells.length, failures };
}

function readCell(
  header: readonly string[],
  body: readonly (readonly string[])[],
  cell: CellExpect,
  expectedHeader: readonly string[] | undefined,
  options: ScoreOptions,
): { ok: boolean; actual: string | null } {
  const column = resolveCol(cell.col, header, expectedHeader);
  const row = body[cell.row];
  if (column === null || column < 0 || row === undefined || column >= row.length) {
    return { ok: false, actual: null };
  }
  const actual = row[column] ?? "";
  const ok = valuesEqual(cell.value, actual, {
    decimal: options.decimal ?? ".",
    dateOrder: options.dateOrder ?? "mdy",
  });
  return { ok, actual };
}

function resolveCol(
  col: number | string,
  header: readonly string[],
  expectedHeader: readonly string[] | undefined,
): number | null {
  if (typeof col === "number") return col;
  const target = normText(col);
  for (let index = 0; index < header.length; index += 1) {
    if (normText(header[index] ?? "") === target) return index;
  }
  if (expectedHeader !== undefined) {
    for (let index = 0; index < expectedHeader.length; index += 1) {
      if (normText(expectedHeader[index] ?? "") === target) return index;
    }
  }
  return null;
}

function bestBy(candidates: readonly number[], scoreOf: (index: number) => number): number {
  let best = candidates[0] ?? 0;
  let bestScore = scoreOf(best);
  for (let index = 1; index < candidates.length; index += 1) {
    const position = candidates[index] ?? best;
    const score = scoreOf(position);
    if (score > bestScore) {
      best = position;
      bestScore = score;
    }
  }
  return best;
}

function pageOf(item: TableExpect): number | undefined {
  return typeof item.page === "number" ? item.page : undefined;
}

function failure(
  check: string,
  reason: string,
  page: number | undefined,
  values?: { expected: unknown; actual: unknown },
): Failure {
  const result: Failure = { metric: "tables", check, reason };
  if (values !== undefined) {
    result.expected = values.expected;
    result.actual = values.actual;
  }
  if (page !== undefined) result.page = page;
  return result;
}
