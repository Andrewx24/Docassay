/**
 * Parser output cache.
 *
 * The cache key is the sha256 of the file bytes, the adapter id (with an
 * optional `:variant`), and the first 16 hex characters of the sha256 of the
 * canonical JSON of `cacheIdentity()`. Keys in that JSON are sorted at every
 * object and there is no whitespace.
 *
 * @example
 * ```ts
 * import { cachePath, fileSha256 } from "./cache.js";
 *
 * cachePath(".docassay/cache", fileSha256(Buffer.from("hello")), "file", { dir: "out" });
 * ```
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020, type AnySchema } from "ajv/dist/2020.js";

import type { Document } from "./models/document.js";

export type CachePolicy = "use" | "no-cache" | "refresh";

const HEX = "0123456789abcdef";

/** A cache key or directory that cannot be used. */
export class CacheError extends Error {
  /**
   * @example
   * ```ts
   * throw new CacheError("file sha256 must be 64 lowercase hex characters");
   * ```
   */
  constructor(message: string) {
    super(message);
    this.name = "CacheError";
  }
}

/** Result of one cache read. */
export interface CacheLookup {
  document: Document | null;
  /** The line `-v` prints on a hit. */
  message: string | null;
  /** Set when a corrupt or schema-invalid entry was ignored. */
  warning: string | null;
}

/**
 * Return the lowercase hex sha256 of file bytes.
 *
 * @example
 * ```ts
 * fileSha256(Buffer.from("hello"));
 * ```
 */
export function fileSha256(data: Buffer | Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

/**
 * Return the JSON path for one file, adapter, and cache identity.
 *
 * @example
 * ```ts
 * cachePath(".docassay/cache", fileSha256(Buffer.from("hello")), "file", {});
 * ```
 */
export function cachePath(
  root: string,
  digest: string,
  adapterId: string,
  identity: Record<string, unknown>,
): string {
  requireSha(digest);
  const name = `${adapterStem(adapterId)}__${optionsHash(identity)}.json`;
  return path.join(root, digest.slice(0, 2), digest, name);
}

/**
 * Return the first 16 hex characters of the identity's canonical JSON.
 *
 * @example
 * ```ts
 * optionsHash({ dir: "out" });
 * ```
 */
export function optionsHash(identity: Record<string, unknown>): string {
  return createHash("sha256").update(canonicalJson(identity), "utf8").digest("hex").slice(0, 16);
}

/**
 * Encode `value` as compact JSON with sorted object keys.
 *
 * @example
 * ```ts
 * canonicalJson({ b: 1, a: null }) === '{"a":null,"b":1}';
 * ```
 */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

/**
 * Read one cache entry.
 *
 * `policy` `"no-cache"` skips the read and still allows a later write.
 * `policy` `"refresh"` skips the read so the next write overwrites the entry.
 * A hit with `verbose` writes `cache hit <id> <sha>` to stderr and returns
 * that line. Corrupt JSON and documents that fail `document.schema.json` are
 * ignored: a warning is written to stderr and the read is a miss.
 *
 * @example
 * ```ts
 * lookupCache(".docassay/cache", sha, "file", { dir: "out" }, { policy: "no-cache" });
 * ```
 */
export function lookupCache(
  root: string,
  digest: string,
  adapterId: string,
  identity: Record<string, unknown>,
  options: { policy?: CachePolicy; verbose?: boolean } = {},
): CacheLookup {
  const policy: string = options.policy ?? "use";
  if (policy !== "use" && policy !== "no-cache" && policy !== "refresh") {
    throw new CacheError(
      `unknown cache policy ${JSON.stringify(policy)}. Use "use", "no-cache", or "refresh".`,
    );
  }
  if (policy !== "use") return { document: null, message: null, warning: null };
  const entry = cachePath(root, digest, adapterId, identity);
  if (!existsSync(entry) || !statSync(entry).isFile()) {
    return { document: null, message: null, warning: null };
  }
  const read = readEntry(entry);
  if (read.warning !== null) {
    process.stderr.write(`${read.warning}\n`);
    return { document: null, message: null, warning: read.warning };
  }
  if (options.verbose === true && read.document !== null) {
    const message = hitMessage(adapterId, digest);
    process.stderr.write(`${message}\n`);
    return { document: read.document, message, warning: null };
  }
  return { document: read.document, message: null, warning: null };
}

/**
 * Store `document` atomically: write a temp file in the same directory, then rename.
 *
 * @example
 * ```ts
 * writeCache(".docassay/cache", sha, "file", { dir: "out" }, document);
 * ```
 */
export function writeCache(
  root: string,
  digest: string,
  adapterId: string,
  identity: Record<string, unknown>,
  document: Document,
): string {
  const entry = cachePath(root, digest, adapterId, identity);
  mkdirSync(path.dirname(entry), { recursive: true });
  const temporary = path.join(path.dirname(entry), `.${path.basename(entry)}.${process.pid}.tmp`);
  try {
    writeFileSync(temporary, `${JSON.stringify(document, null, 2)}\n`, "utf8");
    renameSync(temporary, entry);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
  return entry;
}

/**
 * Delete cached documents. `parserId` limits the delete to that id and its variants.
 *
 * @example
 * ```ts
 * clearCache(".docassay/cache", "llamaparse");
 * ```
 */
export function clearCache(root: string, parserId?: string): number {
  if (!existsSync(root)) return 0;
  if (!statSync(root).isDirectory()) {
    throw new CacheError(
      `cache directory ${root} is not a directory. Pass a folder such as .docassay/cache.`,
    );
  }
  const prefix = parserId === undefined ? null : `${adapterStem(parserId)}__`;
  let removed = 0;
  for (const entry of listJson(root)) {
    const name = path.basename(entry);
    if (!isEntryName(name)) continue;
    if (prefix !== null && !name.startsWith(prefix)) continue;
    unlinkSync(entry);
    removed += 1;
  }
  pruneEmpty(root);
  return removed;
}

/**
 * Return the `-v` line for a cache hit.
 *
 * @example
 * ```ts
 * hitMessage("file", "ab".repeat(32));
 * ```
 */
export function hitMessage(adapterId: string, digest: string): string {
  return `cache hit ${adapterId} ${digest}`;
}

function readEntry(entry: string): { document: Document | null; warning: string | null } {
  let loaded: unknown;
  try {
    loaded = JSON.parse(readFileSync(entry, "utf8")) as unknown;
  } catch {
    return { document: null, warning: `cache ignore ${posix(entry)}: invalid JSON` };
  }
  if (!documentValidator()(loaded)) {
    return {
      document: null,
      warning: `cache ignore ${posix(entry)}: does not match document.schema.json`,
    };
  }
  return { document: loaded as Document, warning: null };
}

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new CacheError("cache identity numbers must be finite.");
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => canonicalize(item));
  if (isRecord(value)) {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) sorted[key] = canonicalize(value[key]);
    return sorted;
  }
  const kind = value === undefined ? "undefined" : typeof value;
  throw new CacheError(
    "cache identity must be JSON objects, arrays, strings, numbers, booleans, or null. " +
      `Got ${kind}.`,
  );
}

function requireSha(digest: string): void {
  if (digest.length === 64 && [...digest].every((char) => HEX.includes(char))) return;
  throw new CacheError(
    `file sha256 must be 64 lowercase hex characters, got ${JSON.stringify(digest)}. ` +
      "Hash the file bytes with sha256.",
  );
}

function adapterStem(adapterId: string): string {
  if (adapterId === "" || adapterId.includes("/") || adapterId.includes("\\")) {
    throw new CacheError(
      `adapter id ${JSON.stringify(adapterId)} is not a cache key. ` +
        "Use an id such as file or llamaparse:agentic.",
    );
  }
  return adapterId.replaceAll(":", "__");
}

function isEntryName(name: string): boolean {
  const marker = name.lastIndexOf("__");
  if (marker <= 0 || !name.endsWith(".json")) return false;
  const suffix = name.slice(marker + 2, -5);
  return suffix.length === 16 && [...suffix].every((char) => HEX.includes(char));
}

function listJson(root: string): string[] {
  const found: string[] = [];
  const walk = (directory: string): void => {
    for (const name of readdirSync(directory)) {
      const entry = path.join(directory, name);
      const info = statSync(entry);
      if (info.isDirectory()) walk(entry);
      else if (name.endsWith(".json")) found.push(entry);
    }
  };
  walk(root);
  found.sort();
  return found;
}

function pruneEmpty(root: string): void {
  const walk = (directory: string): void => {
    for (const name of readdirSync(directory)) {
      const entry = path.join(directory, name);
      if (statSync(entry).isDirectory()) walk(entry);
    }
    if (directory !== root && readdirSync(directory).length === 0) {
      rmdirSync(directory);
    }
  };
  walk(root);
}

function posix(entry: string): string {
  return entry.split(path.sep).join("/");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

let validateDocument: ((data: unknown) => boolean) | null = null;

function documentValidator(): (data: unknown) => boolean {
  if (validateDocument === null) {
    const schemaPath = findSchema();
    const loaded: unknown = JSON.parse(readFileSync(schemaPath, "utf8"));
    if (!isRecord(loaded)) throw new CacheError(`${schemaPath} must be a JSON object`);
    const ajv = new Ajv2020({ allErrors: true, strict: false, logger: false });
    const validate = ajv.compile(loaded as AnySchema);
    validateDocument = (data: unknown) => validate(data) === true;
  }
  return validateDocument;
}

function findSchema(): string {
  let dir = path.dirname(moduleFilename());
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = path.join(dir, "spec", "document.schema.json");
    try {
      if (readFileSync(candidate)) return candidate;
    } catch {
      // Keep walking toward the repository root.
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new CacheError(
    "could not find spec/document.schema.json. Run from a docassay checkout.",
  );
}

declare const __filename: string | undefined;

function moduleFilename(): string {
  const url = import.meta.url;
  if (typeof url === "string" && url.length > 0) return fileURLToPath(url);
  if (typeof __filename === "string" && __filename.length > 0) return __filename;
  throw new CacheError("could not locate cache.ts to find document.schema.json.");
}
