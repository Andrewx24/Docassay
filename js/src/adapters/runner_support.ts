/**
 * Source hashing and the meta fields the runner, not the adapter, fills.
 *
 * @example
 * ```ts
 * import { runParser } from "./adapters/runner_support.js";
 *
 * const document = await runParser(adapter, "files/a.pdf", "dummy");
 * ```
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

import { version } from "../version.js";
import { AdapterUnavailable, type Adapter, type RawResult } from "./base.js";
import type { Document, Source } from "../models/document.js";

const MAGICS: ReadonlyArray<readonly [string, string]> = [
  ["%PDF", "application/pdf"],
  ["\u0089PNG\r\n\u001a\n", "image/png"],
  ["\u00ff\u00d8\u00ff", "image/jpeg"],
  ["GIF87a", "image/gif"],
  ["GIF89a", "image/gif"],
];

const OFFICE: Record<string, string> = {
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

const EXTENSIONS: Record<string, string> = {
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".tif": "image/tiff",
  ".tiff": "image/tiff",
  ".html": "text/html",
  ".htm": "text/html",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".json": "application/json",
  ".csv": "text/csv",
  ".doc": "application/msword",
  ...OFFICE,
};

const ZIP = "PK\u0003\u0004";
const OCTET = "application/octet-stream";

/**
 * Return the installed docassay version, used as `adapterVersion`.
 *
 * @example
 * ```ts
 * console.log(packageVersion());
 * ```
 */
export function packageVersion(): string {
  return version;
}

/**
 * Return a mime type from magic bytes, then the file extension.
 *
 * @example
 * ```ts
 * detectMime(Buffer.from("%PDF-1.7"), "scan.pdf");
 * ```
 */
export function detectMime(data: Uint8Array, filePath: string): string {
  const extension = path.extname(filePath).toLowerCase();
  if (startsWith(data, ZIP) && extension in OFFICE) {
    return OFFICE[extension] ?? OCTET;
  }
  if (
    data.length >= 12 &&
    bytesAt(data, 0, 4) === "RIFF" &&
    bytesAt(data, 8, 4) === "WEBP"
  ) {
    return "image/webp";
  }
  for (const [magic, mime] of MAGICS) {
    if (startsWith(data, magic)) return mime;
  }
  if (startsWith(data, ZIP)) return "application/zip";
  return EXTENSIONS[extension] ?? OCTET;
}

/**
 * Hash `filePath` and detect its mime type.
 *
 * `storedPath` is the path written on the Document (relative to the config
 * file). When it is omitted, the filesystem path is stored with `/`.
 *
 * @example
 * ```ts
 * const source = buildSource("files/invoice.pdf", "files/invoice.pdf");
 * ```
 */
export function buildSource(filePath: string, storedPath?: string): Source {
  const data = readFileSync(filePath);
  return {
    path: storedPath ?? filePath.split(path.sep).join("/"),
    sha256: createHash("sha256").update(data).digest("hex"),
    mime: detectMime(data, filePath),
  };
}

/**
 * Copy parser id, parser version, latency, cost, and adapter version onto `document`.
 *
 * @example
 * ```ts
 * applyRunMeta(document, raw, "file", "0.0.0");
 * ```
 */
export function applyRunMeta(
  document: Document,
  raw: RawResult,
  parserId: string,
  adapterVersion: string,
): Document {
  document.meta.parser = parserId;
  document.meta.parserVersion = raw.parserVersion;
  document.meta.adapterVersion = adapterVersion;
  document.meta.latencyMs = raw.latencyMs;
  if (raw.costUsd !== undefined) document.meta.costUsd = raw.costUsd;
  return document;
}

/**
 * Fetch one file and return a Document with runner-owned meta filled in.
 *
 * @example
 * ```ts
 * const document = await runParser(adapter, "files/a.pdf", "dummy");
 * ```
 */
export async function runParser(
  adapter: Adapter,
  filePath: string,
  parserId: string,
  storedPath?: string,
  adapterVersion?: string,
): Promise<Document> {
  const raw = await adapter.fetch(filePath);
  const source = buildSource(filePath, storedPath);
  const document = adapter.toDocument(raw, source);
  return applyRunMeta(document, raw, parserId, adapterVersion ?? packageVersion());
}

/**
 * Call `available()` on every parser. Throw when any of them cannot run.
 *
 * The exception's `exitCode` is 3 and the message includes each reason.
 *
 * @example
 * ```ts
 * await ensureAvailable([["docling", resolve("docling")]]);
 * ```
 */
export async function ensureAvailable(
  adapters: ReadonlyArray<readonly [string, Adapter]>,
): Promise<void> {
  const failures: string[] = [];
  for (const [parserId, adapter] of adapters) {
    const result = await adapter.available();
    if (!result.ok) failures.push(`${parserId}: ${result.reason ? result.reason : "unavailable"}`);
  }
  if (failures.length > 0) throw new AdapterUnavailable(failures.join("\n"));
}

function startsWith(data: Uint8Array, text: string): boolean {
  const magic = Buffer.from(text, "latin1");
  if (data.length < magic.length) return false;
  for (let index = 0; index < magic.length; index += 1) {
    if (data[index] !== magic[index]) return false;
  }
  return true;
}

function bytesAt(data: Uint8Array, offset: number, length: number): string {
  return Buffer.from(data.subarray(offset, offset + length)).toString("latin1");
}
