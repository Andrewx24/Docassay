import { createHash } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  AdapterConfigError,
  AdapterError,
  AdapterUnavailable,
} from "../src/adapters/base.js";
import {
  clearRegisteredAdapters,
  registerAdapter,
  resolve,
  validIds,
} from "../src/adapters/registry.js";
import { buildSource, detectMime, ensureAvailable, runParser } from "../src/adapters/runner_support.js";
import { version } from "../src/version.js";
import { dummyFactory } from "./fake-adapter.js";

afterEach(() => {
  clearRegisteredAdapters();
});

describe("adapters", () => {
  it("reports unknown ids and variants as config errors", () => {
    expect(() => resolve("nope")).toThrow(AdapterConfigError);
    try {
      resolve("nope");
    } catch (err) {
      expect(err).toBeInstanceOf(AdapterConfigError);
      if (err instanceof AdapterConfigError) {
        expect(err.exitCode).toBe(2);
        expect(err.message).toContain('unknown parser id "nope"');
        expect(err.message).toContain("valid ids: docling, file, llamaparse, reducto");
      }
    }
    expect(() => resolve("llamaparse")).toThrow(/missing variant/);
    expect(() => resolve("llamaparse:turbo")).toThrow(/unknown variant "turbo" for "llamaparse"/);
    expect(() => resolve("llamaparse")).toThrow(/valid variants: fast, cost_effective, agentic, agentic_plus/);
    try {
      resolve("docling:fast");
    } catch (err) {
      expect(err).toBeInstanceOf(AdapterConfigError);
      if (err instanceof AdapterConfigError) {
        expect(err.message).toContain('unknown variant "fast" for "docling"');
        expect(err.message).not.toContain("valid variants");
      }
    }
  });

  it("treats unimplemented built-ins as unavailable", async () => {
    const adapter = resolve("docling", { mode: "http" });
    await expect(adapter.available()).resolves.toEqual({
      ok: false,
      reason: "docling adapter is not available in this build",
    });
    expect(adapter.cacheIdentity().options).toEqual({ mode: "http" });
    await expect(adapter.fetch("missing.pdf")).rejects.toBeInstanceOf(AdapterError);
    expect(() =>
      adapter.toDocument(
        { payload: {}, parserVersion: "x", latencyMs: 0 },
        { path: "a", sha256: "ab".repeat(32), mime: "text/plain" },
      ),
    ).toThrow(AdapterError);
    const file = resolve("file");
    await expect(file.available()).resolves.toMatchObject({ ok: false });
    try {
      await ensureAvailable([
        ["docling", adapter],
        ["file", file],
      ]);
      expect.fail("expected the run to abort");
    } catch (err) {
      expect(err).toBeInstanceOf(AdapterUnavailable);
      if (err instanceof AdapterUnavailable) {
        expect(err.exitCode).toBe(3);
        expect(err.message).toContain("docling: docling adapter is not available in this build");
        expect(err.message).toContain("options.dir");
      }
    }
  });

  it("runs a registered adapter and fills meta", async () => {
    registerAdapter(dummyFactory);
    expect(validIds()).toContain("dummy");
    const root = mkdtempSync(path.join(tmpdir(), "docassay-adapter-"));
    try {
      const sample = path.join(root, "note.txt");
      writeFileSync(sample, "hello");
      const adapter = resolve("dummy", { cost: 1.5 });
      await expect(adapter.available()).resolves.toEqual({ ok: true, reason: "" });
      const document = await runParser(adapter, sample, "dummy", "notes/note.txt");
      expect(document.source.path).toBe("notes/note.txt");
      expect(document.source.sha256).toBe(createHash("sha256").update("hello").digest("hex"));
      expect(document.source.mime).toBe("text/plain");
      expect(document.meta.parser).toBe("dummy");
      expect(document.meta.parserVersion).toBe("dummy-1");
      expect(document.meta.adapterVersion).toBe(version);
      expect(document.meta.latencyMs).toBe(12.5);
      expect(document.meta.costUsd).toBe(1.5);

      const blocked = resolve("dummy", { missing: true });
      await expect(ensureAvailable([["dummy", blocked]])).rejects.toMatchObject({
        exitCode: 3,
        message: expect.stringContaining("dummy: missing token"),
      });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("refuses to replace a built-in id", () => {
    const factory = Object.assign(
      (variant: string | null, options: Record<string, unknown>) => {
        void variant;
        void options;
        return resolve("file");
      },
      { id: "file", variants: [] as readonly string[] },
    );
    expect(() => registerAdapter(factory)).toThrow(/cannot replace built-in adapter "file"/);
  });

  it("detects mime from magic bytes, then the extension", () => {
    const root = mkdtempSync(path.join(tmpdir(), "docassay-mime-"));
    try {
      const pdf = path.join(root, "scan.txt");
      writeFileSync(pdf, Buffer.from("%PDF-1.7\n"));
      expect(detectMime(Buffer.from("%PDF-1.7\n"), pdf)).toBe("application/pdf");
      const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
      expect(detectMime(png, path.join(root, "x.bin"))).toBe("image/png");

      const office = path.join(root, "memo.docx");
      writeFileSync(office, Buffer.from("PK\u0003\u0004rest", "latin1"));
      expect(detectMime(Buffer.from("PK\u0003\u0004rest", "latin1"), office)).toMatch(
        /wordprocessingml.document$/,
      );

      const webp = path.join(root, "pic.bin");
      const webpBytes = Buffer.concat([
        Buffer.from("RIFF"),
        Buffer.from([0, 0, 0, 0]),
        Buffer.from("WEBP"),
        Buffer.from("data"),
      ]);
      writeFileSync(webp, webpBytes);
      expect(detectMime(webpBytes, webp)).toBe("image/webp");

      const plain = path.join(root, "notes.md");
      writeFileSync(plain, "# hi\n");
      const source = buildSource(plain, "notes.md");
      expect(source.mime).toBe("text/markdown");
      expect(source.sha256).toBe(createHash("sha256").update("# hi\n").digest("hex"));
      expect(source.sha256).toHaveLength(64);

      const unknown = path.join(root, "blob");
      writeFileSync(unknown, Buffer.from([0, 1]));
      expect(detectMime(Buffer.from([0, 1]), unknown)).toBe("application/octet-stream");

      const archive = path.join(root, "bundle.bin");
      writeFileSync(archive, Buffer.from("PK\u0003\u0004rest", "latin1"));
      expect(detectMime(Buffer.from("PK\u0003\u0004rest", "latin1"), archive)).toBe("application/zip");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
