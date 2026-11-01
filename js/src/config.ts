/**
 * Load `docassay.yaml` and write a starter project.
 *
 * @example
 * ```ts
 * import { loadConfig } from "docassay";
 *
 * const config = loadConfig("docassay.yaml");
 * console.log(config.golden);
 * ```
 */

import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Ajv2020, type ErrorObject } from "ajv/dist/2020.js";
import {
  LineCounter,
  parseDocument,
  isMap,
  isScalar,
  isSeq,
  type Node,
  type YAMLParseError,
} from "yaml";

import type { Config } from "./models/config.js";

/** Built-in adapters from the Adapters doc. Variants are listed in documented order. */
const PARSERS: Record<string, readonly string[]> = {
  docling: [],
  file: [],
  llamaparse: ["fast", "cost_effective", "agentic", "agentic_plus"],
  reducto: [],
};
const VALID_IDS = "docling, file, llamaparse, reducto";

const DEFAULT_CACHE = ".docassay/cache";
const DEFAULT_BASELINE = ".docassay/baseline.json";
const DEFAULT_DROP = 0.02;

const CONFIG_TEMPLATE = `# Score document parsers against labeled golden files.
version: 1
golden: golden/                       # folder of *.yaml golden files
cache: .docassay/cache                # optional; this is the default
baseline: .docassay/baseline.json     # optional; this is the default
parsers:
  - id: file                          # score output your pipeline already produced
    name: my-pipeline                 # display name when one adapter is used twice
    options: {dir: out/parsed}        # <dir>/<golden id>.json, then <dir>/<golden id>.md
thresholds:
  default: {drop: 0.02}               # fail if a score falls more than this far below baseline
normalize:
  date_order: mdy                     # mdy | dmy, for ambiguous dates like 03/04/2026
  decimal: "."                        # "." | ","
tokenizer: words                      # words | tiktoken:<encoding>
`;

const GOLDEN_TEMPLATE = `# Label what correct parser output looks like. Every section under expect is optional.
file: files/example.pdf               # relative to this golden file
id: example
expect:
  text_contains:
    - "Replace this with a sentence from the document"
`;

const PARSER_ID = /^[a-z][a-z0-9_-]*(:[a-z0-9_]+)?$/;

interface CachedValidator {
  validate: (data: unknown) => boolean;
  errors: () => ErrorObject[];
}

let cachedValidator: CachedValidator | null = null;

/** One problem in a config file. */
export class ConfigIssue {
  /**
   * @example
   * ```ts
   * console.log(new ConfigIssue("docassay.yaml", 4, 5, 'unknown parser id "nope"').toString());
   * ```
   */
  constructor(
    readonly path: string,
    readonly line: number,
    readonly column: number,
    readonly message: string,
  ) {}

  toString(): string {
    return `${this.path}:${this.line}:${this.column}: ${this.message}`;
  }
}

/** Raised by {@link loadConfig} when the file is invalid. CLI commands exit 2. */
export class ConfigError extends Error {
  /** CLI exit code for a config error. */
  readonly exitCode = 2;
  readonly issues: ConfigIssue[];

  /**
   * @example
   * ```ts
   * try {
   *   loadConfig("docassay.yaml");
   * } catch (err) {
   *   if (err instanceof ConfigError) process.exitCode = err.exitCode;
   * }
   * ```
   */
  constructor(issues: ConfigIssue[]) {
    const ordered = [...issues].sort(compareIssues);
    super(ordered.map((issue) => issue.toString()).join("\n"));
    this.name = "ConfigError";
    this.issues = ordered;
  }
}

function compareIssues(left: ConfigIssue, right: ConfigIssue): number {
  return (
    compareText(left.path, right.path) ||
    left.line - right.line ||
    left.column - right.column ||
    compareText(left.message, right.message)
  );
}

function compareText(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

/**
 * Return `filePath` relative to the working directory, using `/`.
 *
 * @example
 * ```ts
 * displayPath("docassay.yaml");
 * ```
 */
export function displayPath(filePath: string): string {
  const relative = path.relative(path.resolve(process.cwd()), path.resolve(filePath));
  if (relative === "") return ".";
  return relative.split(path.sep).join("/");
}

/**
 * Load `docassay.yaml`, apply defaults, and resolve paths.
 *
 * Paths are resolved relative to the config file and stored relative to that
 * file. Throws {@link ConfigError} with every problem. Callers should exit 2.
 *
 * @example
 * ```ts
 * const config = loadConfig("docassay.yaml");
 * console.log(config.parsers[0].id);
 * ```
 */
export function loadConfig(filePath: string): Config {
  const [issues, config] = inspect(filePath);
  if (issues.length > 0 || config === null) throw new ConfigError(issues);
  return config;
}

/**
 * Write `docassay.yaml` and `golden/example.yaml`. Return the exit code.
 *
 * Refuses to overwrite an existing file unless `force` is set.
 *
 * @example
 * ```ts
 * process.exitCode = initCommand(undefined, false);
 * ```
 */
export function initCommand(directory?: string, force = false): number {
  const root = directory === undefined ? process.cwd() : directory;
  const configPath = path.join(root, "docassay.yaml");
  const goldenPath = path.join(root, "golden", "example.yaml");
  const existing = [configPath, goldenPath].filter((item) => existsSync(item));
  if (existing.length > 0 && !force) {
    for (const item of existing) {
      console.log(`${displayPath(item)}:1:1: file already exists; rerun with --force`);
    }
    return 2;
  }
  try {
    mkdirSync(path.dirname(goldenPath), { recursive: true });
    writeFileSync(configPath, CONFIG_TEMPLATE, "utf8");
    writeFileSync(goldenPath, GOLDEN_TEMPLATE, "utf8");
  } catch (err) {
    const detail = err instanceof Error ? err.message : "could not write files";
    console.log(`${displayPath(root)}:1:1: ${detail}`);
    return 2;
  }
  console.log(`wrote ${displayPath(configPath)}`);
  console.log(`wrote ${displayPath(goldenPath)}`);
  return 0;
}

/**
 * Serialize a config to the JSON both language ports must match.
 *
 * @example
 * ```ts
 * const text = canonicalConfigJson(loadConfig("docassay.yaml"));
 * ```
 */
export function canonicalConfigJson(config: Config): string {
  return `${JSON.stringify(canonicalConfig(config), null, 2)}\n`;
}

function canonicalConfig(config: Config): Record<string, unknown> {
  return omitUndefined({
    version: config.version,
    golden: config.golden,
    cache: config.cache,
    baseline: config.baseline,
    parsers: config.parsers.map((parser) =>
      omitUndefined({
        id: parser.id,
        name: parser.name,
        options:
          parser.options === undefined ? undefined : canonicalizeValue(parser.options),
      }),
    ),
    thresholds:
      config.thresholds === undefined
        ? undefined
        : omitUndefined({
            default: canonicalThreshold(config.thresholds.default),
            tables: canonicalThreshold(config.thresholds.tables),
            text: canonicalThreshold(config.thresholds.text),
            order: canonicalThreshold(config.thresholds.order),
            fields: canonicalThreshold(config.thresholds.fields),
            chunks: canonicalThreshold(config.thresholds.chunks),
            overall: canonicalThreshold(config.thresholds.overall),
          }),
    normalize:
      config.normalize === undefined
        ? undefined
        : omitUndefined({
            date_order: config.normalize.date_order,
            decimal: config.normalize.decimal,
          }),
    tokenizer: config.tokenizer,
  });
}

function canonicalThreshold(value: { drop?: number; min?: number } | undefined): unknown {
  if (value === undefined) return undefined;
  return omitUndefined({ drop: value.drop, min: value.min });
}

function omitUndefined(record: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (value !== undefined && value !== null) out[key] = value;
  }
  return out;
}

function canonicalizeValue(value: unknown): unknown {
  if (typeof value === "number" && Number.isFinite(value) && Number.isInteger(value)) return value;
  if (Array.isArray(value)) return value.map((item) => canonicalizeValue(item));
  if (isRecord(value)) {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) out[key] = canonicalizeValue(item);
    return out;
  }
  return value;
}

function inspect(filePath: string): [ConfigIssue[], Config | null] {
  const shown = displayPath(filePath);
  if (!existsSync(filePath)) return [[new ConfigIssue(shown, 1, 1, "path not found")], null];
  let text: string;
  try {
    if (!statSync(filePath).isFile()) {
      return [[new ConfigIssue(shown, 1, 1, "path is not a file")], null];
    }
    text = new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(filePath));
  } catch (err) {
    if (err instanceof TypeError) {
      return [[new ConfigIssue(shown, 1, 1, "file is not valid UTF-8")], null];
    }
    const detail = err instanceof Error ? err.message : "could not read file";
    return [[new ConfigIssue(shown, 1, 1, detail)], null];
  }
  const composed = compose(text);
  if (composed.errors.length > 0) {
    return [[yamlIssue(shown, composed.errors[0], composed.lineCounter)], null];
  }
  if (!isMap(composed.node) || !isRecord(composed.data)) {
    const [line, column] = position(composed.node, composed.lineCounter);
    return [[new ConfigIssue(shown, line, column, "config must be a mapping")], null];
  }
  const issues = duplicateKeys(composed.node, shown, composed.lineCounter);
  issues.push(...schemaIssues(composed.data, composed.node, shown, composed.lineCounter));
  issues.push(...parserIssues(composed.data, composed.node, shown, composed.lineCounter));
  if (issues.some((issue) => isSchemaMessage(issue.message))) return [issues, null];
  applyDefaults(composed.data);
  resolvePaths(composed.data, filePath);
  return [issues, issues.length === 0 ? asConfig(composed.data) : null];
}

function asConfig(data: Record<string, unknown>): Config {
  return data as unknown as Config;
}

function applyDefaults(data: Record<string, unknown>): void {
  if (!("cache" in data) || data.cache === undefined || data.cache === null) {
    data.cache = DEFAULT_CACHE;
  }
  if (!("baseline" in data) || data.baseline === undefined || data.baseline === null) {
    data.baseline = DEFAULT_BASELINE;
  }
  if (!("tokenizer" in data) || data.tokenizer === undefined || data.tokenizer === null) {
    data.tokenizer = "words";
  }
  const thresholds = recordField(data, "thresholds");
  const thresholdDefault = recordField(thresholds, "default");
  if (
    !("drop" in thresholdDefault) ||
    thresholdDefault.drop === undefined ||
    thresholdDefault.drop === null
  ) {
    thresholdDefault.drop = DEFAULT_DROP;
  }
  const normalize = recordField(data, "normalize");
  if (
    !("date_order" in normalize) ||
    normalize.date_order === undefined ||
    normalize.date_order === null
  ) {
    normalize.date_order = "mdy";
  }
  if (!("decimal" in normalize) || normalize.decimal === undefined || normalize.decimal === null) {
    normalize.decimal = ".";
  }
}

function recordField(data: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = data[key];
  if (isRecord(value)) return value;
  const created: Record<string, unknown> = {};
  data[key] = created;
  return created;
}

function resolvePaths(data: Record<string, unknown>, configPath: string): void {
  for (const key of ["golden", "cache", "baseline"]) {
    const value = data[key];
    if (typeof value === "string" && value.length > 0) {
      data[key] = relativeToConfig(configPath, value);
    }
  }
  const parsers = data.parsers;
  if (!Array.isArray(parsers)) return;
  for (const parser of parsers) {
    if (!isRecord(parser)) continue;
    const parserId = parser.id;
    const options = parser.options;
    if (typeof parserId !== "string" || !isRecord(options)) continue;
    const directory = options.dir;
    if (splitId(parserId)[0] === "file" && typeof directory === "string" && directory.length > 0) {
      options.dir = relativeToConfig(configPath, directory);
    }
  }
}

function relativeToConfig(configPath: string, value: string): string {
  const base = path.resolve(path.dirname(configPath));
  const absolute = path.resolve(base, value);
  const relative = path.relative(base, absolute);
  if (relative === "") return ".";
  return relative.split(path.sep).join("/");
}

function parserIssues(
  data: Record<string, unknown>,
  node: Node,
  shown: string,
  lineCounter: LineCounter,
): ConfigIssue[] {
  const issues: ConfigIssue[] = [];
  const parsers = data.parsers;
  if (!Array.isArray(parsers)) return issues;
  const seen = new Map<string, string>();
  parsers.forEach((parser, index) => {
    if (!isRecord(parser)) return;
    const parserId = parser.id;
    if (typeof parserId === "string" && PARSER_ID.test(parserId)) {
      issues.push(...knownParser(parserId, node, index, shown, lineCounter));
    }
    const label = displayName(parser);
    if (label === null) return;
    if (seen.has(label)) {
      const located = parserNode(
        node,
        index,
        typeof parser.name === "string" ? "name" : "id",
      );
      const [line, column] = position(located, lineCounter);
      issues.push(new ConfigIssue(shown, line, column, `duplicate display name "${label}"`));
    } else {
      seen.set(label, typeof parserId === "string" ? parserId : "");
    }
  });
  return issues;
}

function knownParser(
  parserId: string,
  node: Node,
  index: number,
  shown: string,
  lineCounter: LineCounter,
): ConfigIssue[] {
  const [adapter, variant] = splitId(parserId);
  const located = parserNode(node, index, "id");
  const [line, column] = position(located, lineCounter);
  const variants = PARSERS[adapter];
  if (variants === undefined) {
    return [
      new ConfigIssue(
        shown,
        line,
        column,
        `unknown parser id "${parserId}"; valid ids: ${VALID_IDS}`,
      ),
    ];
  }
  if (variant === null) {
    if (variants.length > 0) {
      return [
        new ConfigIssue(
          shown,
          line,
          column,
          `missing variant for "${adapter}"; valid ids: ${VALID_IDS}; valid variants: ${variants.join(", ")}`,
        ),
      ];
    }
    return [];
  }
  if (!variants.includes(variant)) {
    const detail = variants.length > 0 ? `; valid variants: ${variants.join(", ")}` : "";
    return [
      new ConfigIssue(
        shown,
        line,
        column,
        `unknown variant "${variant}" for "${adapter}"; valid ids: ${VALID_IDS}${detail}`,
      ),
    ];
  }
  return [];
}

function splitId(parserId: string): [string, string | null] {
  const colon = parserId.indexOf(":");
  if (colon === -1) return [parserId, null];
  return [parserId.slice(0, colon), parserId.slice(colon + 1)];
}

function displayName(parser: Record<string, unknown>): string | null {
  if (typeof parser.name === "string" && parser.name.length > 0) return parser.name;
  if (typeof parser.id === "string" && parser.id.length > 0) return parser.id;
  return null;
}

function parserNode(node: Node, index: number, key: string): Node | null {
  return nodeAt(node, ["parsers", index, key], null);
}

function moduleFilename(): string {
  if (typeof import.meta.url === "string" && import.meta.url.length > 0) {
    return fileURLToPath(import.meta.url);
  }
  if (typeof __filename === "string" && __filename.length > 0) return __filename;
  return process.argv[1] ?? process.cwd();
}

function schemaFile(): string {
  let dir = path.dirname(moduleFilename());
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = path.join(dir, "spec", "config.schema.json");
    if (existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  const fromCwd = path.resolve(process.cwd(), "spec", "config.schema.json");
  if (existsSync(fromCwd)) return fromCwd;
  throw new ConfigError([
    new ConfigIssue(
      "spec/config.schema.json",
      1,
      1,
      "could not find spec/config.schema.json; run from a docassay checkout",
    ),
  ]);
}

function compiledValidator(): CachedValidator {
  if (cachedValidator === null) {
    const loaded: unknown = JSON.parse(readFileSync(schemaFile(), "utf8"));
    if (!isRecord(loaded)) {
      throw new ConfigError([
        new ConfigIssue(schemaFile(), 1, 1, "config schema must be a JSON object"),
      ]);
    }
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    const validate = ajv.compile(loaded);
    cachedValidator = {
      validate: (data: unknown) => validate(data) === true,
      errors: () => validate.errors ?? [],
    };
  }
  return cachedValidator;
}

function schemaIssues(
  data: Record<string, unknown>,
  node: Node,
  shown: string,
  lineCounter: LineCounter,
): ConfigIssue[] {
  const validate = compiledValidator();
  validate.validate(data);
  const errors = keptErrors(validate.errors());
  const issues: ConfigIssue[] = [];
  for (const error of errors) {
    const parts = pointerParts(error.instancePath);
    if (error.keyword === "additionalProperties") {
      const property = param(error, "additionalProperty");
      if (typeof property === "string") {
        const located = nodeAt(node, parts, property);
        const [line, column] = position(located, lineCounter);
        issues.push(new ConfigIssue(shown, line, column, `unknown property "${property}"`));
      }
      continue;
    }
    const [line, column] = position(nodeAt(node, parts, null), lineCounter);
    issues.push(new ConfigIssue(shown, line, column, schemaMessage(error, parts)));
  }
  return issues;
}

function keptErrors(errors: ErrorObject[]): ErrorObject[] {
  const oneOfPaths = new Set(
    errors.filter((error) => error.keyword === "oneOf").map((error) => error.instancePath),
  );
  return errors.filter(
    (error) => error.keyword === "oneOf" || !oneOfPaths.has(error.instancePath),
  );
}

function schemaMessage(error: ErrorObject, parts: Array<string | number>): string {
  const keyword = error.keyword;
  if (keyword === "required") {
    const missing = param(error, "missingProperty");
    return `missing property "${typeof missing === "string" ? missing : "version"}"`;
  }
  if (keyword === "const") return `must be ${String(param(error, "allowedValue"))}`;
  if (keyword === "enum") {
    const values = param(error, "allowedValues");
    if (Array.isArray(values)) return `must be one of ${values.map((item) => String(item)).join(", ")}`;
    return "must be one of the allowed values";
  }
  if (keyword === "pattern") {
    return parts.at(-1) === "id" ? "must match the parser id pattern" : "must match the pattern";
  }
  if (keyword === "type") return typeMessage(param(error, "type"));
  if (keyword === "minLength") {
    return param(error, "limit") === 1
      ? "must not be empty"
      : `must be at least ${String(param(error, "limit"))} characters`;
  }
  if (keyword === "minimum") return `must be >= ${String(param(error, "limit"))}`;
  if (keyword === "maximum") return `must be <= ${String(param(error, "limit"))}`;
  if (keyword === "minItems") return `must have at least ${String(param(error, "limit"))} items`;
  if (keyword === "oneOf") {
    return parts.at(-1) === "tokenizer"
      ? 'must be "words" or tiktoken:<encoding>'
      : "must match one schema";
  }
  return `schema violation (${keyword})`;
}

function typeMessage(expected: unknown): string {
  if (Array.isArray(expected)) {
    const names = expected.map((item) => String(item));
    const only = names[0];
    if (names.length === 1) return `must be ${only ?? ""}`;
    const last = names[names.length - 1] ?? "";
    return `must be ${names.slice(0, -1).join(", ")}, or ${last}`;
  }
  return `must be ${String(expected)}`;
}

function param(error: ErrorObject, key: string): unknown {
  const value: unknown = error.params;
  if (typeof value === "object" && value !== null && key in value) {
    return (value as Record<string, unknown>)[key];
  }
  return undefined;
}

function isSchemaMessage(message: string): boolean {
  return (
    message.startsWith("unknown property ") ||
    message.startsWith("missing property ") ||
    message.startsWith("must be ") ||
    message.startsWith("must not be empty") ||
    message.startsWith("must have at least ") ||
    message.startsWith("must match ") ||
    message.startsWith("schema violation ")
  );
}

interface Composed {
  node: Node | null;
  data: unknown;
  lineCounter: LineCounter;
  errors: YAMLParseError[];
}

function compose(text: string): Composed {
  const lineCounter = new LineCounter();
  const doc = parseDocument(text, { version: "1.2", uniqueKeys: false, lineCounter });
  const node = isYamlNode(doc.contents) ? doc.contents : null;
  return { node, data: doc.toJS(), lineCounter, errors: doc.errors };
}

function yamlIssue(
  shown: string,
  error: YAMLParseError | undefined,
  lineCounter: LineCounter,
): ConfigIssue {
  const detail = (error?.message ?? "could not parse YAML").split("\n")[0] ?? "could not parse YAML";
  const marked = error?.linePos?.[0];
  if (marked !== undefined && marked.line > 0) {
    return new ConfigIssue(shown, marked.line, marked.col, `invalid YAML: ${detail}`);
  }
  if (error !== undefined) {
    const pos = lineCounter.linePos(error.pos[0]);
    if (pos.line > 0) return new ConfigIssue(shown, pos.line, pos.col, `invalid YAML: ${detail}`);
  }
  return new ConfigIssue(shown, 1, 1, `invalid YAML: ${detail}`);
}

function position(node: Node | null, lineCounter: LineCounter): [number, number] {
  const offset = node?.range?.[0];
  if (offset === undefined) return [1, 1];
  const pos = lineCounter.linePos(offset);
  if (pos.line < 1) return [1, 1];
  return [pos.line, pos.col];
}

function duplicateKeys(node: Node, shown: string, lineCounter: LineCounter): ConfigIssue[] {
  const issues: ConfigIssue[] = [];
  if (isMap(node)) {
    const seen = new Set<string>();
    for (const item of node.items) {
      if (isScalar(item.key)) {
        const key = scalarText(item.key);
        if (seen.has(key)) {
          const [line, column] = position(item.key, lineCounter);
          issues.push(new ConfigIssue(shown, line, column, `duplicate key "${key}"`));
        } else {
          seen.add(key);
        }
      }
      if (item.value !== null && isYamlNode(item.value)) {
        issues.push(...duplicateKeys(item.value, shown, lineCounter));
      }
    }
  } else if (isSeq(node)) {
    for (const item of node.items) {
      if (isYamlNode(item)) issues.push(...duplicateKeys(item, shown, lineCounter));
    }
  }
  return issues;
}

function scalarText(node: Node): string {
  if (!isScalar(node)) return "";
  return typeof node.source === "string" ? node.source : String(node.value);
}

function mappingValue(node: Node, key: string): Node | null {
  if (!isMap(node)) return null;
  for (const item of node.items) {
    if (isScalar(item.key) && scalarText(item.key) === key && item.value !== null && isYamlNode(item.value)) {
      return item.value;
    }
  }
  return null;
}

function mappingKey(node: Node, key: string): Node | null {
  if (!isMap(node)) return null;
  for (const item of node.items) {
    if (isScalar(item.key) && scalarText(item.key) === key) return item.key;
  }
  return null;
}

function nodeAt(node: Node, parts: Array<string | number>, key: string | null): Node | null {
  let current: Node | null = node;
  for (const part of parts) {
    if (current === null) return null;
    if (typeof part === "number" && isSeq(current)) {
      const item: unknown = current.items[part];
      if (isScalar(item) || isMap(item) || isSeq(item)) current = item;
      else return current;
    } else if (typeof part === "string" && isMap(current)) {
      const value = mappingValue(current, part);
      if (value === null) return current;
      current = value;
    } else {
      return current;
    }
  }
  if (key !== null && current !== null) {
    const found = mappingKey(current, key);
    if (found !== null) return found;
  }
  return current;
}

function pointerParts(pointer: string): Array<string | number> {
  if (pointer === "") return [];
  const parts: Array<string | number> = [];
  for (const raw of pointer.split("/").slice(1)) {
    const decoded = raw.replaceAll("~1", "/").replaceAll("~0", "~");
    parts.push(isDigits(decoded) ? Number(decoded) : decoded);
  }
  return parts;
}

function isDigits(text: string): boolean {
  if (text.length === 0) return false;
  for (const char of text) {
    if (char < "0" || char > "9") return false;
  }
  return true;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isYamlNode(value: unknown): value is Node {
  return isScalar(value) || isMap(value) || isSeq(value);
}
