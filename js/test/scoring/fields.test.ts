import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { describe, expect, it } from "vitest";

import type { Document } from "../../src/models/document.js";
import { scoreFields, type ScoreOptions } from "../../src/scoring/index.js";

type FieldValue = string | number | boolean | null;

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../spec/fixtures");
const REQUIRED = [
  "fields-missing-doc",
  "fields-null",
  "fields-boolean-yes",
  "fields-number",
  "fields-date",
  "fields-missing-key",
];

interface ExpectedFile {
  scores: { fields: number };
  failures: unknown[];
}

interface OptionsFile {
  decimal?: "." | ",";
  dateOrder?: "mdy" | "dmy";
  date_order?: "mdy" | "dmy";
}

function cases(): string[] {
  return readdirSync(ROOT)
    .filter((name) => name.startsWith("fields-"))
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

function sample(fields?: Document["fields"]): Document {
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
    tables: [],
    ...(fields === undefined ? {} : { fields }),
  };
}

describe("scoreFields", () => {
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
        expect: { fields: Record<string, FieldValue> };
      };
      const result = scoreFields(document, golden.expect.fields, options(folder));
      const expected = readJson<ExpectedFile>(path.join(folder, "expected.json"));
      expect(dec4(result.score)).toBe(dec4(expected.scores.fields));
      expect(result.failures).toEqual(expected.failures);
      if (result.score < 1) expect(result.failures.length).toBeGreaterThan(0);
    }
  });

  it("scores an empty expectation map as 1", () => {
    expect(scoreFields(sample({}), {})).toEqual({ score: 1, failures: [] });
  });

  it("fails a null expectation when the parser returned no fields", () => {
    const result = scoreFields(sample(), { note: null, total: 10 });
    expect(result.score).toBe(0);
    expect(result.failures.map((failure) => failure.reason)).toEqual([
      "parser returned no fields",
      "parser returned no fields",
    ]);
  });

  it("matches a numeric actual against a formatted string", () => {
    const result = scoreFields(sample({ total: { value: 4120 } }), { total: "4,120.00" });
    expect(result.score).toBe(1);
    expect(result.failures).toEqual([]);
  });

  it("rejects booleans, blanks, and unparsed text for numeric expectations", () => {
    const result = scoreFields(
      sample({
        flag: { value: 1 },
        label: { value: null },
        count: { value: null },
        total: { value: 10 },
        vendor: { value: "n/a" },
        paid: { value: false },
      }),
      { flag: true, label: "Acme", count: 5, total: 10, vendor: 3, paid: 0 },
    );
    expect(result.failures.map((failure) => failure.reason)).toEqual([
      "value does not match",
      "value does not match",
      "value does not match",
      "value does not match",
      "value does not match",
    ]);
    expect(dec4(result.score)).toBe(dec4(1 / 6));
  });

  it("rejects a non-scalar actual for a number", () => {
    const result = scoreFields(sample({ total: { value: { raw: 1 } as unknown as string } }), {
      total: 1,
    });
    expect(result.score).toBe(0);
    expect(result.failures[0]?.reason).toBe("value does not match");
  });

  it("matches boolean values without going through strings", () => {
    const result = scoreFields(sample({ paid: { value: true }, closed: { value: false } }), {
      paid: true,
      closed: false,
    });
    expect(result.score).toBe(1);
  });
});