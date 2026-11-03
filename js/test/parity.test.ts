import { mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it } from "vitest";

import { round4 } from "../src/normalize/index.js";
import { ParityError, main, render, resolveCase, scoreCase } from "../src/parity.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../spec/fixtures");
const METRICS = ["tables", "text", "order", "fields", "chunks"] as const;

interface ExpectedFile {
  scores: Record<string, number>;
  failures: { metric: string; check: string; reason: string }[];
  extra?: unknown;
}

function cases(): string[] {
  return readdirSync(ROOT)
    .filter((name) => {
      try {
        readFileSync(path.join(ROOT, name, "document.json"));
        return true;
      } catch {
        return false;
      }
    })
    .sort();
}

function dec4(value: number): number {
  return Math.floor(value * 10000 + 0.5);
}

describe("scoreCase", () => {
  it("matches every fixture to 4 decimals", () => {
    const names = cases();
    expect(names.length).toBeGreaterThanOrEqual(20);
    for (const name of names) {
      const folder = path.join(ROOT, name);
      const result = scoreCase(folder);
      const expected = JSON.parse(readFileSync(path.join(folder, "expected.json"), "utf8")) as ExpectedFile;
      for (const [metric, value] of Object.entries(expected.scores)) {
        expect(dec4(result.scores[metric] ?? Number.NaN), name).toBe(dec4(value));
      }
      const present = METRICS.flatMap((metric) =>
        expected.scores[metric] === undefined ? [] : [expected.scores[metric]],
      );
      expect(dec4(result.scores.overall ?? Number.NaN), name).toBe(
        dec4(round4(present.reduce((sum, value) => sum + value, 0) / present.length)),
      );
      expect(result.failures, name).toEqual(expected.failures);
      for (const failure of expected.failures) {
        expect(failure.metric).toBeTypeOf("string");
        expect(failure.check).toBeTypeOf("string");
        expect(failure.reason).toBeTypeOf("string");
      }
      if (expected.extra === undefined) expect(result.extra, name).toBeUndefined();
      else expect(result.extra, name).toEqual(expected.extra);
    }
  });

  it("prints one case and rejects a missing name", () => {
    let out = "";
    const write = process.stdout.write.bind(process.stdout);
    process.stdout.write = ((chunk: string | Uint8Array) => {
      out += String(chunk);
      return true;
    }) as typeof process.stdout.write;
    try {
      expect(main(["tables-exact"])).toBe(0);
    } finally {
      process.stdout.write = write;
    }
    const body = JSON.parse(out) as { scores: { tables: number }; failures: unknown[] };
    expect(body.scores.tables).toBe(1);
    expect(body.failures).toEqual([]);
    expect(render(scoreCase(path.join(ROOT, "tables-exact")))).toBe(out);

    let err = "";
    const writeErr = process.stderr.write.bind(process.stderr);
    process.stderr.write = ((chunk: string | Uint8Array) => {
      err += String(chunk);
      return true;
    }) as typeof process.stderr.write;
    try {
      expect(main([])).toBe(1);
      expect(main(["missing-case"])).toBe(1);
    } finally {
      process.stderr.write = writeErr;
    }
    expect(err).toContain("usage:");
    expect(err).toContain("unknown fixture");
    expect(() => resolveCase("missing-case")).toThrow(ParityError);
    expect(resolveCase(path.join(ROOT, "text-exact"))).toBe(path.resolve(ROOT, "text-exact"));
  });
});

describe("fixture errors", () => {
  const created: string[] = [];

  afterEach(() => {
    for (const dir of created.splice(0)) rmSync(dir, { recursive: true, force: true });
  });

  it("names the broken file and how to fix it", () => {
    const dir = mkdtempSync(path.join(tmpdir(), "parity-"));
    created.push(dir);
    const document = {
      specVersion: "1.0.0",
      source: {
        path: "files/a.pdf",
        sha256: "ab".repeat(32),
        mime: "application/pdf",
      },
      meta: { parser: "file", parserVersion: "file", adapterVersion: "0.0.0" },
      pages: [{ number: 1 }],
      blocks: [],
      tables: [],
    };
    writeFileSync(path.join(dir, "document.json"), "{");
    writeFileSync(path.join(dir, "golden.yaml"), "file: files/a.pdf\n");
    expect(() => scoreCase(dir)).toThrow(/document\.json/);
    writeFileSync(path.join(dir, "document.json"), "[]");
    expect(() => scoreCase(dir)).toThrow(/document\.json/);
    writeFileSync(path.join(dir, "document.json"), JSON.stringify(document));
    writeFileSync(path.join(dir, "golden.yaml"), "[\n");
    expect(() => scoreCase(dir)).toThrow(/invalid YAML/);
    writeFileSync(path.join(dir, "golden.yaml"), "- just-a-list\n");
    expect(() => scoreCase(dir)).toThrow(/mapping/);
    writeFileSync(path.join(dir, "golden.yaml"), "file: files/a.pdf\nexpect:\n  reading_order:\n    - [only]\n");
    expect(() => scoreCase(dir)).toThrow(/reading_order/);
    writeFileSync(path.join(dir, "golden.yaml"), "file: files/a.pdf\nexpect:\n  fields:\n    vendor:\n      nested: true\n");
    expect(() => scoreCase(dir)).toThrow(/fields\.vendor/);
    writeFileSync(path.join(dir, "golden.yaml"), "file: files/a.pdf\nexpect: {}\n");
    expect(scoreCase(dir)).toEqual({ scores: {}, failures: [] });
    writeFileSync(path.join(dir, "options.json"), "{");
    expect(() => scoreCase(dir)).toThrow(/options\.json/);
    writeFileSync(path.join(dir, "options.json"), "[]");
    expect(() => scoreCase(dir)).toThrow(/JSON object/);
    writeFileSync(path.join(dir, "options.json"), JSON.stringify({ decimal: "x" }));
    expect(() => scoreCase(dir)).toThrow(/decimal/);
    writeFileSync(path.join(dir, "options.json"), JSON.stringify({ dateOrder: "ymd" }));
    expect(() => scoreCase(dir)).toThrow(/dateOrder/);
    writeFileSync(path.join(dir, "options.json"), JSON.stringify({ tokenizer: "" }));
    expect(() => scoreCase(dir)).toThrow(/tokenizer/);
    writeFileSync(
      path.join(dir, "options.json"),
      JSON.stringify({ decimal: ",", date_order: "dmy", tokenizer: "words" }),
    );
    expect(scoreCase(dir).scores).toEqual({});
    mkdirSync(path.join(dir, "nested"));
    expect(() => scoreCase(path.join(dir, "nested"))).toThrow(/could not read JSON/);
  });
});
