import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { verify, type FieldCheck } from "../src/verify.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const FIXTURE = path.join(ROOT, "spec/fixtures/verify/cases.json");
const REQUIRED = [
  "uncited",
  "bad-page",
  "block-wrong-page",
  "table",
  "bbox-overlap",
  "page-only",
  "number-currency",
  "number-missing",
  "date-other-format",
  "hyphen-string",
  "boolean",
];

interface CaseFile {
  document: Record<string, unknown>;
  cases: {
    name: string;
    fields: Record<string, unknown>;
    ok: boolean;
    checks: FieldCheck[];
  }[];
}

describe("verify", () => {
  const loaded = JSON.parse(readFileSync(FIXTURE, "utf8")) as CaseFile;

  it("matches the shared fixture", () => {
    const names = new Set(loaded.cases.map((item) => item.name));
    for (const name of REQUIRED) expect(names.has(name)).toBe(true);
    expect(loaded.cases.length).toBeGreaterThanOrEqual(20);
    for (const item of loaded.cases) {
      const result = verify(loaded.document, item.fields);
      expect(result.checks, item.name).toEqual(item.checks);
      expect(result.ok, item.name).toBe(item.ok);
      expect(result.unverified, item.name).toEqual(
        item.checks.filter((check) => check.status !== "verified"),
      );
    }
  });

  it("derives pages from blocks when the document omits them", () => {
    const doc = { blocks: [{ id: "b", text: "Hello", page: 1 }], tables: [] };
    expect(verify(doc, { a: { value: "Hello", citations: [{ page: 1, blockId: "b" }] } }).ok).toBe(
      true,
    );
  });

  it("does not throw on malformed input", () => {
    const boom = {
      get pages(): never {
        throw new Error("boom");
      },
    };
    const blown = verify(boom, { a: { value: "x", citations: [{ page: 1 }] } });
    expect(blown.checks[0]?.status).toBe("citation_invalid");
    expect(blown.checks[0]?.reason).toContain("could not be read");
    expect(verify(null, null).checks[0]?.status).toBe("citation_invalid");
    expect(verify([], { a: 1 }).checks[0]?.status).toBe("citation_invalid");
    const doc = { pages: [{ number: 1 }], blocks: [{ id: "b1", text: "Hello", page: 1 }], tables: [] };
    expect(
      verify(doc, { a: { value: { nested: 1 }, citations: [{ page: 1, blockId: "b1" }] } }).checks[0]
        ?.status,
    ).toBe("citation_invalid");
    expect(
      verify(doc, { a: { value: "Hello", citations: [{ page: 1, blockId: "" }] } }).checks[0]?.status,
    ).toBe("citation_invalid");
    expect(verify(doc, { a: { value: "Hello", citations: "b1" } }).checks[0]?.status).toBe(
      "citation_invalid",
    );
  });

  it("joins an empty header table from its rows", () => {
    const doc = {
      pages: [{ number: 1 }],
      blocks: [],
      tables: [{ id: "t", page: 1, header: [], rows: [["Only"]] }],
    };
    expect(verify(doc, { a: { value: "Only", citations: [{ page: 1, blockId: "t" }] } }).ok).toBe(
      true,
    );
  });

  it("compiles regexes once and imports only normalization", () => {
    const source = readFileSync(path.join(ROOT, "js/src/verify.ts"), "utf8");
    const body = source.slice(source.indexOf("export function verify"));
    expect(source.indexOf("const NUMBER_RE")).toBeGreaterThan(-1);
    expect(source.indexOf("const DATE_RE")).toBeLessThan(source.indexOf("export function verify"));
    expect(body).not.toContain("new RegExp");
    const forbidden = ["cli", "adapters", "golden", "config", "yaml", "commander"];
    const seen = new Set<string>();
    const walk = (file: string): void => {
      if (seen.has(file)) return;
      seen.add(file);
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(/from "([^"]+)"/g)) {
        const spec = match[1];
        if (spec === undefined) continue;
        for (const part of spec.split("/")) expect(forbidden).not.toContain(part);
        if (spec.startsWith(".")) {
          const resolved = path.resolve(path.dirname(file), spec);
          const ts = resolved.endsWith(".js") ? `${resolved.slice(0, -3)}.ts` : resolved;
          walk(existsSync(ts) ? ts : resolved);
        }
      }
    };
    walk(path.join(ROOT, "js/src/verify.ts"));
  });

  it("checks 1000 fields on a 50-page document in under a second", () => {
    const pages = Array.from({ length: 50 }, (_, index) => ({ number: index + 1 }));
    const blocks = Array.from({ length: 50 }, (_, index) => ({
      id: `b${index + 1}`,
      text: "alpha November 1, 2026 total $4,120.00 end",
      page: index + 1,
      bbox: [0, 0, 1, 1],
    }));
    const doc = { pages, blocks, tables: [] };
    const fields: Record<string, unknown> = {};
    for (let index = 0; index < 1000; index += 1) {
      const page = (index % 50) + 1;
      const citations = [{ page, blockId: `b${page}` }];
      if (index % 5 === 0) fields[`f${index}`] = { value: "2026-11-01", citations };
      else if (index % 2 === 0) fields[`f${index}`] = { value: "alpha", citations };
      else fields[`f${index}`] = { value: 4120, citations };
    }
    const started = Date.now();
    const result = verify(doc, fields);
    expect(result.ok).toBe(true);
    expect(result.checks).toHaveLength(1000);
    expect(Date.now() - started).toBeLessThan(1000);
  });
});
