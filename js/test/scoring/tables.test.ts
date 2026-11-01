import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { describe, expect, it } from "vitest";

import type { Document } from "../../src/models/document.js";
import type { TableExpect } from "../../src/models/golden.js";
import { round4 } from "../../src/normalize/index.js";
import { scoreTables, type ScoreOptions } from "../../src/scoring/index.js";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../spec/fixtures",
);
const REQUIRED = [
  "tables-exact",
  "tables-header-in-rows",
  "tables-wrong-page",
  "tables-greedy",
  "tables-header-below",
  "tables-ragged",
  "tables-cell-name",
  "tables-cell-index",
  "tables-cell-out-of-range",
  "tables-numeric",
  "tables-no-header",
];

interface ExpectedFile {
  scores: { tables: number };
  failures: unknown[];
}

interface OptionsFile {
  decimal?: "." | ",";
  dateOrder?: "mdy" | "dmy";
  date_order?: "mdy" | "dmy";
}

function cases(): string[] {
  return readdirSync(ROOT)
    .filter((name) => name.startsWith("tables-"))
    .sort();
}

function readJson<T>(filePath: string): T {
  return JSON.parse(readFileSync(filePath, "utf8")) as T;
}

function dec4(value: number): number {
  return Math.floor(value * 10000 + 0.5);
}

function options(folder: string): ScoreOptions {
  const filePath = path.join(folder, "options.json");
  try {
    const raw = readJson<OptionsFile>(filePath);
    const order = raw.dateOrder ?? raw.date_order;
    return {
      ...(raw.decimal === undefined ? {} : { decimal: raw.decimal }),
      ...(order === undefined ? {} : { dateOrder: order }),
    };
  } catch {
    return {};
  }
}

function sample(): Document {
  return {
    specVersion: "1.0.0",
    source: { path: "files/note.pdf", sha256: "ab".repeat(32), mime: "application/pdf" },
    meta: { parser: "file", parserVersion: "file", adapterVersion: "0.0.0" },
    pages: [{ number: 1 }],
    blocks: [],
    tables: [
      { id: "t1", page: 1, header: ["A"], rows: [["x"]] },
      { id: "t2", page: 1, header: ["B"], rows: [["y"]] },
    ],
  };
}

describe("scoreTables", () => {
  it("covers the required cases", () => {
    const names = cases();
    for (const name of REQUIRED) expect(names).toContain(name);
    expect(names.length).toBeGreaterThanOrEqual(10);
  });

  it("matches every fixture to 4 decimals", () => {
    for (const name of cases()) {
      const folder = path.join(ROOT, name);
      const document = readJson<Document>(path.join(folder, "document.json"));
      const golden = parse(readFileSync(path.join(folder, "golden.yaml"), "utf8")) as {
        expect: { tables: TableExpect[] };
      };
      const result = scoreTables(document, golden.expect.tables, options(folder));
      const expected = readJson<ExpectedFile>(path.join(folder, "expected.json"));
      expect(dec4(result.score)).toBe(dec4(expected.scores.tables));
      expect(result.failures).toEqual(expected.failures);
      if (result.score < 1) expect(result.failures.length).toBeGreaterThan(0);
    }
  });

  it("scores an empty expectation list as 1", () => {
    expect(scoreTables(sample(), [])).toEqual({ score: round4(1), failures: [] });
  });

  it("treats a missing page as every unmatched table", () => {
    const matched = scoreTables(sample(), [
      { header: ["B"], cells: [{ row: 0, col: 0, value: "y" }] } as TableExpect,
    ]);
    expect(matched.score).toBe(1);
    expect(matched.failures).toEqual([]);

    const empty: Document = { ...sample(), tables: [] };
    const missing = scoreTables(empty, [{ header: ["Z"] } as TableExpect]);
    expect(missing.score).toBe(0);
    expect(missing.failures[0]?.reason).toBe("no table found");
    expect(missing.failures[0]?.page).toBeUndefined();
  });
});
