import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { AdapterError, type RawResult } from "../../src/adapters/base.js";
import { FileAdapter, markdownToDocument } from "../../src/adapters/file.js";
import { runParser } from "../../src/adapters/runner_support.js";
import type { Document } from "../../src/models/document.js";
import { version } from "../../src/version.js";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../spec/fixtures/file-adapter",
);
const SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
const SOURCE = { path: "files/invoice.pdf", sha256: SHA, mime: "application/pdf" };
const DIR_REASON =
  "file adapter requires options.dir, a folder of <golden id>.json or .md files";

const temps: string[] = [];

afterEach(() => {
  for (const dir of temps.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function tempDir(): string {
  const dir = mkdtempSync(path.join(tmpdir(), "docassay-file-"));
  temps.push(dir);
  return dir;
}

function canonical(document: Document): string {
  return `${JSON.stringify(document, null, 2)}\n`;
}

function requireText(filePath: string): string {
  return readFileSync(filePath, "utf8");
}

function staleDocument(): Record<string, unknown> {
  return {
    specVersion: "1.0.0",
    source: { path: "old/path.pdf", sha256: "ab".repeat(32), mime: "application/pdf" },
    meta: {
      parser: "llamaparse:agentic",
      parserVersion: "2026-09-24",
      adapterVersion: "9.9.9",
      latencyMs: 840,
      costUsd: 0.012,
      createdAt: "2026-10-26T12:00:00Z",
      extra: { jobId: "job_123" },
    },
    pages: [{ number: 1 }],
    blocks: [{ id: "b1", type: "paragraph", text: "Kept", page: 1 }],
    tables: [],
    warnings: ["check total"],
    fields: { vendor: { value: "Acme" } },
  };
}

describe("file adapter", () => {
  it("matches every Markdown fixture", () => {
    const names = ["edges", "empty", "mixed"];
    for (const name of names) {
      const text = requireText(path.join(ROOT, name, "input.md"));
      const expected = requireText(path.join(ROOT, name, "expected-document.json"));
      expect(canonical(markdownToDocument(text, SOURCE))).toBe(expected);
      expect(canonical(markdownToDocument(text.replaceAll("\n", "\r\n"), SOURCE))).toBe(expected);
    }
  });

  it("requires options.dir and caches nothing", async () => {
    const missing = new FileAdapter(null, {});
    expect(missing.cacheable).toBe(false);
    expect(missing.cacheIdentity()).toEqual({ dir: "" });
    await expect(missing.available()).resolves.toEqual({ ok: false, reason: DIR_REASON });
    const numbered = new FileAdapter(null, { dir: 3 });
    await expect(numbered.available()).resolves.toEqual({ ok: false, reason: DIR_REASON });
    await expect(missing.fetch("files/invoice.pdf")).rejects.toThrow(DIR_REASON);

    const adapter = new FileAdapter("ignored", { dir: "out/parsed" });
    expect(adapter.id).toBe("file");
    expect(adapter.variants).toEqual([]);
    await expect(adapter.available()).resolves.toEqual({ ok: true, reason: "" });
    expect(adapter.cacheIdentity()).toEqual({ dir: "out/parsed" });
    expect(adapter.cacheable).toBe(false);
  });

  it("prefers JSON and lets the runner overwrite source and meta", async () => {
    const root = tempDir();
    const folder = path.join(root, "parsed");
    mkdirSync(folder);
    writeFileSync(path.join(folder, "invoice.json"), JSON.stringify(staleDocument()));
    writeFileSync(path.join(folder, "invoice.md"), "# Ignored\n");
    const sample = path.join(root, "invoice.pdf");
    writeFileSync(sample, Buffer.from("%PDF-1.4\n"));
    const adapter = new FileAdapter(null, { dir: folder });
    const document = await runParser(adapter, sample, "file", "files/invoice.pdf");
    expect(document.source.path).toBe("files/invoice.pdf");
    expect(document.source.sha256).toBe(createHash("sha256").update("%PDF-1.4\n").digest("hex"));
    expect(document.source.mime).toBe("application/pdf");
    expect(document.meta.parser).toBe("file");
    expect(document.meta.parserVersion).toBe("file");
    expect(document.meta.adapterVersion).toBe(version);
    expect(document.meta.latencyMs).toBe(0);
    expect(document.meta.costUsd).toBe(0);
    expect(document.meta.extra).toEqual({ jobId: "job_123" });
    expect(document.meta.createdAt).toBe("2026-10-26T12:00:00Z");
    expect(document.blocks[0]?.text).toBe("Kept");
    expect(document.warnings).toEqual(["check total"]);
    expect(document.fields?.vendor?.value).toBe("Acme");
  });

  it("falls back to Markdown and reports a missing file", async () => {
    const root = tempDir();
    const folder = path.join(root, "parsed");
    mkdirSync(folder);
    const text = requireText(path.join(ROOT, "mixed", "input.md"));
    writeFileSync(path.join(folder, "invoice.final.md"), text);
    const adapter = new FileAdapter(null, { dir: folder });
    const raw = await adapter.fetch(path.join(root, "invoice.final.pdf"));
    expect(raw.parserVersion).toBe("file");
    expect(raw.latencyMs).toBe(0);
    expect(raw.costUsd).toBe(0);
    const document = adapter.toDocument(raw, SOURCE);
    const expected = requireText(path.join(ROOT, "mixed", "expected-document.json"));
    expect(canonical(document)).toBe(expected);
    await expect(adapter.fetch(path.join(root, "gone.pdf"))).rejects.toThrow(
      `no output for gone in ${folder}`,
    );
  });

  it("does not use Markdown when the JSON file is unusable", async () => {
    const root = tempDir();
    const folder = path.join(root, "parsed");
    mkdirSync(folder);
    writeFileSync(path.join(folder, "bad.json"), "{");
    writeFileSync(path.join(folder, "bad.md"), "# Hello\n");
    writeFileSync(path.join(folder, "list.json"), "[]");
    writeFileSync(path.join(folder, "partial.json"), '{"specVersion": "1.0.0"}');
    writeFileSync(path.join(folder, "binary.json"), Buffer.from([0xff]));
    const adapter = new FileAdapter(null, { dir: folder });
    await expect(adapter.fetch(path.join(root, "bad.pdf"))).rejects.toThrow(/is not valid JSON/);
    await expect(adapter.fetch(path.join(root, "list.pdf"))).rejects.toThrow(/must be object/);
    await expect(adapter.fetch(path.join(root, "partial.pdf"))).rejects.toThrow(
      /does not match document.schema.json/,
    );
    await expect(adapter.fetch(path.join(root, "binary.pdf"))).rejects.toThrow(/not valid UTF-8/);
  });

  it("rejects a payload that is neither a Document nor markdown", () => {
    const adapter = new FileAdapter(null, { dir: "out" });
    const raw: RawResult = { payload: { markdown: 1 }, parserVersion: "file", latencyMs: 0 };
    expect(() => adapter.toDocument(raw, SOURCE)).toThrow(AdapterError);
    expect(() => adapter.toDocument(raw, SOURCE)).toThrow(/must be a Document or markdown text/);
  });
});
