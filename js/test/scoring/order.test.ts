import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { describe, expect, it } from "vitest";

import type { Document } from "../../src/models/document.js";
import { scoreOrder } from "../../src/scoring/index.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../spec/fixtures");
const REQUIRED = [
  "order-repeat-first",
  "order-before",
  "order-first-missing",
  "order-second-missing",
  "order-partial",
  "order-once",
];

interface ExpectedFile {
  scores: { order: number };
  failures: unknown[];
}

function cases(): string[] {
  return readdirSync(ROOT)
    .filter((name) => name.startsWith("order-"))
    .sort();
}

function readJson<T>(filePath: string): T {
  return JSON.parse(readFileSync(filePath, "utf8")) as T;
}

function dec4(value: number): number {
  return Math.floor(value * 10000 + 0.5);
}

function sample(text: string): Document {
  return {
    specVersion: "1.0.0",
    source: {
      path: "files/note.pdf",
      sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      mime: "application/pdf",
    },
    meta: { parser: "file", parserVersion: "file", adapterVersion: "0.0.0" },
    pages: [{ number: 1 }],
    blocks: [{ id: "b1", type: "paragraph", text, page: 1 }],
    tables: [],
  };
}

describe("scoreOrder", () => {
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
        expect: { reading_order: [string, string][] };
      };
      const result = scoreOrder(document, golden.expect.reading_order);
      const expected = readJson<ExpectedFile>(path.join(folder, "expected.json"));
      expect(dec4(result.score)).toBe(dec4(expected.scores.order));
      expect(result.failures).toEqual(expected.failures);
      if (result.score < 1) expect(result.failures.length).toBeGreaterThan(0);
    }
  });

  it("scores an empty expectation list as 1", () => {
    expect(scoreOrder(sample("alpha"), [])).toEqual({ score: 1, failures: [] });
  });

  it("searches for the second copy after the first match", () => {
    const once = scoreOrder(sample("alpha"), [["alpha", "alpha"]]);
    expect(once.score).toBe(0);
    expect(once.failures[0]?.reason).toBe("out of order");
    const twice = scoreOrder(sample("alpha alpha"), [["alpha", "alpha"]]);
    expect(twice.score).toBe(1);
    expect(twice.failures).toEqual([]);
  });
});
