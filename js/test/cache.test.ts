import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CacheError,
  cachePath,
  clearCache,
  fileSha256,
  lookupCache,
  writeCache,
  type CacheLookup,
} from "../src/cache.js";
import { createProgram } from "../src/cli.js";
import type { Document } from "../src/models/document.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const EXAMPLE = path.join(ROOT, "spec/examples/document.example.json");
const KEY = path.join(ROOT, "spec/fixtures/cache/key.json");
const SHA = "ab".repeat(32);

function document(parserVersion = "2026-09-24"): Document {
  const raw = JSON.parse(readFileSync(EXAMPLE, "utf8")) as Document;
  return { ...raw, meta: { ...raw.meta, parserVersion } };
}

function tempDir(): string {
  return mkdtempSync(path.join(tmpdir(), "docassay-cache-"));
}

function posix(entry: string): string {
  return entry.split(path.sep).join("/");
}

describe("cache", () => {
  const stderr: string[] = [];
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function spyStderr(): void {
    stderr.length = 0;
    vi.spyOn(process.stderr, "write").mockImplementation((chunk) => {
      stderr.push(String(chunk));
      return true;
    });
  }

  it("matches the shared key fixture", () => {
    const loaded = JSON.parse(readFileSync(KEY, "utf8")) as {
      cases: {
        bytes: string;
        adapterId: string;
        identity: Record<string, unknown>;
        cache: string;
        path: string;
      }[];
    };
    for (const item of loaded.cases) {
      const digest = fileSha256(Buffer.from(item.bytes, "utf8"));
      const got = cachePath(item.cache, digest, item.adapterId, item.identity);
      expect(posix(got)).toBe(item.path);
    }
  });

  it("round-trips parser version, latency, and cost", () => {
    const root = tempDir();
    const identity = { dir: "out/parsed" };
    writeCache(root, SHA, "llamaparse:agentic", identity, document());
    const found = lookupCache(root, SHA, "llamaparse:agentic", identity);
    expect(found.document?.meta.parserVersion).toBe("2026-09-24");
    expect(found.document?.meta.latencyMs).toBe(840);
    expect(found.document?.meta.costUsd).toBe(0.012);
    expect(found.warning).toBeNull();
  });

  it("reports a cache hit on stderr when verbose", () => {
    spyStderr();
    const root = tempDir();
    writeCache(root, SHA, "file", {}, document());
    const quiet: CacheLookup = lookupCache(root, SHA, "file", {}, { verbose: false });
    expect(quiet.message).toBeNull();
    const loud = lookupCache(root, SHA, "file", {}, { verbose: true });
    expect(loud.message).toBe(`cache hit file ${SHA}`);
    expect(stderr.join("")).toContain(loud.message);
  });

  it("skips reads for no-cache and still writes", () => {
    const root = tempDir();
    const identity = { dir: "out" };
    writeCache(root, SHA, "file", identity, document("first"));
    const skipped = lookupCache(root, SHA, "file", identity, { policy: "no-cache" });
    expect(skipped.document).toBeNull();
    expect(skipped.warning).toBeNull();
    writeCache(root, SHA, "file", identity, document("second"));
    const found = lookupCache(root, SHA, "file", identity);
    expect(found.document?.meta.parserVersion).toBe("second");
  });

  it("refreshes by skipping the read and overwriting", () => {
    const root = tempDir();
    const identity = { dir: "out" };
    writeCache(root, SHA, "file", identity, document("first"));
    const skipped = lookupCache(root, SHA, "file", identity, { policy: "refresh" });
    expect(skipped.document).toBeNull();
    writeCache(root, SHA, "file", identity, document("second"));
    const found = lookupCache(root, SHA, "file", identity);
    expect(found.document?.meta.parserVersion).toBe("second");
  });

  it("ignores corrupt and schema-invalid entries with a warning", () => {
    spyStderr();
    const root = tempDir();
    const identity = { dir: "out" };
    const entry = cachePath(root, SHA, "file", identity);
    mkdirSync(path.dirname(entry), { recursive: true });
    writeFileSync(entry, "{", "utf8");
    const corrupt = lookupCache(root, SHA, "file", identity);
    expect(corrupt.document).toBeNull();
    expect(corrupt.warning).toContain("invalid JSON");
    writeFileSync(entry, '{"specVersion":"1.0.0"}\n', "utf8");
    const invalid = lookupCache(root, SHA, "file", identity);
    expect(invalid.document).toBeNull();
    expect(invalid.warning).toContain("does not match document.schema.json");
    expect(stderr.join("")).toContain("invalid JSON");
    expect(stderr.join("")).toContain("does not match document.schema.json");
    writeCache(root, SHA, "file", identity, document());
    expect(lookupCache(root, SHA, "file", identity).document?.meta.parserVersion).toBe("2026-09-24");
  });

  it("clears one parser id, including variants", () => {
    const root = tempDir();
    writeCache(root, SHA, "file", {}, document());
    writeCache(root, SHA, "llamaparse:agentic", {}, document());
    expect(clearCache(root, "llamaparse:fast")).toBe(0);
    expect(clearCache(root, "llamaparse")).toBe(1);
    expect(lookupCache(root, SHA, "file", {}).document).not.toBeNull();
    expect(lookupCache(root, SHA, "llamaparse:agentic", {}).document).toBeNull();
    expect(clearCache(root)).toBe(1);
  });

  it("docassay cache clear deletes the named parser", () => {
    const root = tempDir();
    writeCache(root, SHA, "file", {}, document());
    const lines: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((chunk) => {
      lines.push(String(chunk));
      return true;
    });
    const program = createProgram();
    program.exitOverride();
    program.parse(["cache", "clear", "--parser", "file", "--cache", root], { from: "user" });
    expect(lines.join("").trim()).toBe(`removed 1 cache file(s) for file from ${root}`);
    expect(lookupCache(root, SHA, "file", {}).document).toBeNull();
  });

  it("leaves a missing directory and unrelated json alone", () => {
    const root = tempDir();
    expect(clearCache(path.join(root, "absent"))).toBe(0);
    writeCache(root, SHA, "file", {}, document());
    const stray = path.join(root, SHA.slice(0, 2), SHA, "notes.json");
    writeFileSync(stray, "{}", "utf8");
    expect(clearCache(root)).toBe(1);
    expect(existsSync(stray)).toBe(true);
  });

  it("refuses a file as the cache directory and an unknown policy", () => {
    const root = tempDir();
    const target = path.join(root, "not-a-dir");
    writeFileSync(target, "x", "utf8");
    expect(() => clearCache(target)).toThrow(/\.docassay\/cache/);
    expect(() =>
      lookupCache(root, SHA, "file", {}, { policy: "maybe" as "use" }),
    ).toThrow(/unknown cache policy/);
  });

  it("does not warn when no-cache skips a corrupt entry", () => {
    spyStderr();
    const root = tempDir();
    const entry = cachePath(root, SHA, "file", { dir: "out" });
    mkdirSync(path.dirname(entry), { recursive: true });
    writeFileSync(entry, "{", "utf8");
    const skipped = lookupCache(root, SHA, "file", { dir: "out" }, { policy: "no-cache" });
    expect(skipped.document).toBeNull();
    expect(skipped.warning).toBeNull();
    expect(stderr.join("")).toBe("");
  });

  it("names the fix for a bad sha or identity", () => {
    expect(() => cachePath(".", "abc", "file", {})).toThrow(CacheError);
    expect(() => cachePath(".", "abc", "file", {})).toThrow(/64 lowercase hex/);
    expect(() => cachePath(".", SHA, "file", { dir: () => 1 })).toThrow(/JSON objects/);
  });
});
