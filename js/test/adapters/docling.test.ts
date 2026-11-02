import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import type { RawResult } from "../../src/adapters/base.js";
import { DoclingAdapter, doclingToDocument, parseServeResponse } from "../../src/adapters/docling.js";
import type { Document } from "../../src/models/document.js";
import { version } from "../../src/version.js";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../spec/fixtures/adapters/docling",
);
const SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
const SOURCE = { path: "files/sample.pdf", sha256: SHA, mime: "application/pdf" };
const CASES = ["digital-table", "scanned-page", "spans", "edges"];
const SERVE =
  "Set DOCLING_BASE_URL to the docling-serve base URL, for example http://localhost:5001.";

const temps: string[] = [];

afterEach(() => {
  for (const dir of temps.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function canonical(document: Document): string {
  return `${JSON.stringify(document, null, 2)}\n`;
}

function load(name: string): unknown {
  return JSON.parse(readText(`${name}.raw.json`));
}

function readText(name: string): string {
  return readFileSync(path.join(ROOT, name), "utf8");
}

describe("docling", () => {
  it("matches the recorded documents", () => {
    for (const name of CASES) {
      const document = doclingToDocument(load(name), SOURCE, "2.87.0");
      expect(canonical(document)).toBe(readText(`${name}.doc.json`));
      expect(document.meta.parser).toBe("docling");
      expect(document.meta.parserVersion).toBe("2.87.0");
      expect(document.meta.adapterVersion).toBe(version);
    }
  });

  it("keeps headers, the caption, and spanned header cells", () => {
    const document = doclingToDocument(load("digital-table"), SOURCE, "2.87.0");
    expect(document.blocks.slice(0, 2).map((block) => block.type)).toEqual(["header", "header"]);
    expect(document.blocks[0]?.text.startsWith("Optimized Table Tokenization")).toBe(true);
    expect(document.blocks[0]?.bbox).toEqual([194.48, 93.66, 447.54, 102.04]);
    expect(document.blocks.some((block) => block.type === "caption" && block.text.startsWith("Table 1."))).toBe(
      true,
    );
    const table = document.tables[0];
    expect(table?.header).toEqual([
      "# enc-layers",
      "# dec-layers",
      "Language",
      "TEDs simple",
      "TEDs complex",
      "TEDs all",
      "mAP (0.75)",
      "Inference time (secs)",
    ]);
    expect(table?.rows[0]?.[0]).toBe("6");
    expect(table?.rows[0]?.[3]).toBe("0.965 0.969");
    expect(table?.bbox?.[1]).toBe(337);
    expect(document.pages[0]).toEqual({ number: 1, width: 612, height: 792 });
  });

  it("scales normalized boxes and keeps unknown labels", () => {
    const document = doclingToDocument(load("edges"), SOURCE, "2.87.0");
    expect(document.blocks.map((block) => block.type)).toEqual(["other", "formula", "code", "footer"]);
    expect(document.blocks[0]?.bbox).toEqual([20, 80, 100, 100]);
    expect(document.blocks[1]?.bbox).toBeUndefined();
    expect(document.blocks[1]?.page).toBe(2);
    expect(document.blocks[3]?.bbox).toEqual([1, 380, 30, 390]);
    expect(document.pages.map((page) => page.number)).toEqual([1, 2]);
  });

  it("repeats a merged header across the columns it spans", () => {
    const document = doclingToDocument(load("spans"), SOURCE, "2.87.0");
    expect(document.tables[0]?.header).toEqual(["H", "H"]);
    expect(document.tables[0]?.rows).toEqual([["a", "b"]]);
    expect(document.tables[0]?.bbox).toEqual([1, 2, 3, 4]);
  });

  it("rejects a payload that is not a DoclingDocument and a missing file", async () => {
    const adapter = new DoclingAdapter(null, { doOcr: true });
    expect(adapter.id).toBe("docling");
    expect(adapter.cacheIdentity()).toEqual({ options: { doOcr: true } });
    const raw: RawResult = { payload: { text: "no" }, parserVersion: "2.87.0", latencyMs: 0 };
    expect(() => adapter.toDocument(raw, SOURCE)).toThrow(/DoclingDocument/);
    expect(() => doclingToDocument({ schema_name: "DoclingDocument" }, SOURCE, "")).toThrow(/parserVersion/);
    const previous = process.env.DOCLING_BASE_URL;
    delete process.env.DOCLING_BASE_URL;
    try {
      await expect(adapter.available()).resolves.toEqual({ ok: false, reason: SERVE });
      const root = mkdtempSync(path.join(tmpdir(), "docassay-docling-"));
      temps.push(root);
      await expect(adapter.fetch(path.join(root, "gone.pdf"))).rejects.toThrow(/path not found/);
      const withBase = new DoclingAdapter(null, { baseUrl: "http://localhost:5001/" });
      await expect(withBase.available()).resolves.toEqual({ ok: true });
      expect(withBase.cacheIdentity().options).toEqual({ baseUrl: "http://localhost:5001/" });
    } finally {
      if (previous === undefined) delete process.env.DOCLING_BASE_URL;
      else process.env.DOCLING_BASE_URL = previous;
    }
  });

  it("reads a docling-serve JSON body", () => {
    const raw = parseServeResponse(
      {
        status: "success",
        processing_time: 1.5,
        document: { json_content: { schema_name: "DoclingDocument", version: "1.10.0" } },
      },
      "a.pdf",
    );
    expect(raw.parserVersion).toBe("docling-document-1.10.0");
    expect(raw.latencyMs).toBe(1500);
    expect(raw.costUsd).toBe(0);
    const named = parseServeResponse(
      {
        status: "success",
        version: "2.87.0",
        document: { json_content: { schema_name: "DoclingDocument", version: "1.10.0" } },
      },
      "a.pdf",
    );
    expect(named.parserVersion).toBe("2.87.0");
    expect(() => parseServeResponse({ status: "failure", errors: ["ocr crashed"] }, "a.pdf")).toThrow(
      /ocr crashed/,
    );
    expect(() => parseServeResponse({ status: "success", document: {} }, "a.pdf")).toThrow(/json_content/);
  });

  it.skipIf(process.env.LIVE !== "1")("parses one file through docling-serve", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-docling-live-"));
    temps.push(root);
    const sample = path.join(root, "note.pdf");
    writeFileSync(sample, "%PDF-1.4\n");
    const raw = await new DoclingAdapter(null, {}).fetch(sample);
    const document = doclingToDocument(raw.payload, SOURCE, raw.parserVersion);
    expect(document.meta.parserVersion.length).toBeGreaterThan(0);
    expect(raw.latencyMs).toBeGreaterThanOrEqual(0);
  });
});
