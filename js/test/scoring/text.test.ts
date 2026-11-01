import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { describe, expect, it } from "vitest";

import type { Document } from "../../src/models/document.js";
import { documentText, scoreText, type ScoreOptions } from "../../src/scoring/index.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../spec/fixtures");
const REQUIRED = [
  "text-hyphen",
  "text-table-cell",
  "text-missing",
  "text-partial",
  "text-across-blocks",
  "text-markdown-ignored",
];
const MISSING_TEXT = "Alpha\n\n\n\nBeta\n\nItem | Qty\nWidget | 2\n\nOnly";

interface ExpectedFile {
  scores: { text: number };
  failures: unknown[];
}

interface OptionsFile {
  decimal?: "." | ",";
  dateOrder?: "mdy" | "dmy";
  date_order?: "mdy" | "dmy";
}

function cases(): string[] {
  return readdirSync(ROOT)
    .filter((name) => name.startsWith("text-"))
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

function sample(overrides: Partial<Document> = {}): Document {
  return {
    specVersion: "1.0.0",
    source: {
      path: "files/invoice.pdf",
      sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      mime: "application/pdf",
    },
    meta: { parser: "file", parserVersion: "file", adapterVersion: "0.0.0" },
    pages: [{ number: 1 }],
    blocks: [{ id: "b1", type: "paragraph", text: "Hello", page: 1 }],
    tables: [],
    ...overrides,
  };
}

describe("scoreText", () => {
  it("covers the required cases", () => {
    const names = cases();
    for (const name of REQUIRED) expect(names).toContain(name);
    expect(names.length).toBeGreaterThanOrEqual(6);
  });

  it("matches every fixture to 4 decimals", () => {
    for (const name of cases()) {
      const folder = path.join(ROOT, name);
      const document = readJson<Document>(path.join(folder, "document.json"));
      const golden = parse(readFileSync(path.join(folder, "golden.yaml"), "utf8")) as {
        expect: { text_contains: string[] };
      };
      const result = scoreText(document, golden.expect.text_contains, options(folder));
      const expected = readJson<ExpectedFile>(path.join(folder, "expected.json"));
      expect(dec4(result.score)).toBe(dec4(expected.scores.text));
      expect(result.failures).toEqual(expected.failures);
      if (result.score < 1) expect(result.failures.length).toBeGreaterThan(0);
    }
  });

  it("locks the block and table join on the missing fixture", () => {
    const folder = path.join(ROOT, "text-missing");
    const document = readJson<Document>(path.join(folder, "document.json"));
    expect(documentText(document)).toBe(MISSING_TEXT);
    const result = scoreText(document, ["nowhere"]);
    expect(result.failures[0]?.actual).toBe(MISSING_TEXT);
    expect(result.failures[0]?.reason).toBe("text not found");
  });

  it("scores an empty expectation list as 1", () => {
    expect(scoreText(sample(), [])).toEqual({ score: 1, failures: [] });
  });

  it("keeps an empty header row and leaves markdown out", () => {
    const document = sample({
      blocks: [
        { id: "b1", type: "paragraph", text: "", page: 1 },
        { id: "b2", type: "paragraph", text: "", page: 1 },
      ],
      tables: [
        { id: "t1", page: 1, header: [], rows: [["A"]] },
        { id: "t2", page: 1, rows: [["B"]], markdown: "SKIP" },
        { id: "t3", page: 1, rows: [] },
      ],
    });
    expect(documentText(document)).toBe("\n\n\n\n\nA\n\nB\n\n");
  });

  it("renders tables alone without a leading separator", () => {
    const document = sample({
      blocks: [],
      tables: [{ id: "t1", page: 1, header: ["Item"], rows: [["Widget"]] }],
    });
    expect(documentText(document)).toBe("Item\nWidget");
  });
});
