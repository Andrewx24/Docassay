import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { describe, expect, it } from "vitest";

import type { Document } from "../../src/models/document.js";
import type { ChunkExpect } from "../../src/models/golden.js";
import { scoreChunks } from "../../src/scoring/index.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../spec/fixtures");
const REQUIRED = [
  "chunks-absent",
  "chunks-split",
  "chunks-split-repeated",
  "chunks-min-last",
  "chunks-heading-mismatch",
  "chunks-tokens-set",
];

interface ExpectedFile {
  scores: { chunks: number };
  failures: unknown[];
  extra?: unknown;
}

function cases(): string[] {
  return readdirSync(ROOT)
    .filter((name) => name.startsWith("chunks-"))
    .sort();
}

function readJson<T>(filePath: string): T {
  return JSON.parse(readFileSync(filePath, "utf8")) as T;
}

function dec4(value: number): number {
  return Math.floor(value * 10000 + 0.5);
}

function sample(chunks?: Document["chunks"], tables: Document["tables"] = []): Document {
  return {
    specVersion: "1.0.0",
    source: {
      path: "files/invoice.pdf",
      sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      mime: "application/pdf",
    },
    meta: { parser: "file", parserVersion: "file", adapterVersion: "0.0.0" },
    pages: [{ number: 1 }],
    blocks: [],
    tables,
    ...(chunks === undefined ? {} : { chunks }),
  };
}

describe("scoreChunks", () => {
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
        expect: { chunks: ChunkExpect };
      };
      const result = scoreChunks(document, golden.expect.chunks);
      const expected = readJson<ExpectedFile>(path.join(folder, "expected.json"));
      expect(dec4(result.score)).toBe(dec4(expected.scores.chunks));
      expect(result.failures).toEqual(expected.failures);
      expect(result.extra ?? null).toEqual(expected.extra ?? null);
      if (result.score < 1) expect(result.failures.length).toBeGreaterThan(0);
    }
  });

  it("ignores a split table when the rule is off", () => {
    const result = scoreChunks(
      sample(
        [
          { id: "c1", text: "body", blockIds: ["t1"], pages: [1], tokens: 1 },
          { id: "c2", text: "more", blockIds: ["t1"], pages: [1], tokens: 1 },
        ],
        [{ id: "t1", page: 1, header: ["Item"], rows: [["A"]] }],
      ),
      { no_split_tables: false, max_tokens: 5 },
    );
    expect(result.score).toBe(1);
    expect(result.failures).toEqual([]);
    expect(result.extra).toEqual({ tokens: { min: 1, median: 1, max: 1 } });
  });

  it("reports a distribution when no rules are set", () => {
    const result = scoreChunks(sample([{ id: "c1", text: "one two", blockIds: [], pages: [1] }]), {});
    expect(result.score).toBe(1);
    expect(result.failures).toEqual([]);
    expect(result.extra).toEqual({ tokens: { min: 2, median: 2, max: 2 } });
  });
});
