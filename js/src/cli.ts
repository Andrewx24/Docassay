#!/usr/bin/env node
import { realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Command } from "commander";

import { BuiltinError, builtinSetsPath, configForGoldenFlag } from "./builtin.js";
import { clearCache } from "./cache.js";
import { initCommand } from "./config.js";
import { baselineCommand, checkCommand } from "./gate.js";
import { validateCommand } from "./golden.js";
import { labelCommand } from "./label.js";
import { runCommand } from "./runner.js";
import { version } from "./version.js";

/**
 * Build the docassay command.
 *
 * @example
 * ```ts
 * createProgram().parse(["node", "docassay", "--version"]);
 * ```
 */
export function createProgram(): Command {
  const program = new Command();
  program
    .name("docassay")
    .description("Score document parsers against labeled golden files.")
    .version(version, "--version", "Print the version and exit.");

  program
    .command("init")
    .description("Write a starter docassay.yaml and golden/example.yaml.")
    .option("--force", "Overwrite docassay.yaml and golden/example.yaml.")
    .action((options: { force?: boolean }) => {
      process.exitCode = initCommand(undefined, options.force === true);
    });

  program
    .command("validate")
    .description("Check golden files against the schema.")
    .argument("[path]", "golden set directory or one golden file", "golden")
    .option("--strict", "fail if a file still contains # TODO verify")
    .option("--golden <name>", "Pass builtin to validate the packaged golden set.")
    .action((filePath: string, options: { strict?: boolean; golden?: string }) => {
      let target = filePath;
      if (options.golden !== undefined) {
        const resolved = resolveGoldenFlag(filePath, options.golden);
        if (resolved === undefined) return;
        target = builtinSetsPath();
      }
      const code = validateCommand(target, { strict: options.strict === true });
      if (code !== 0) process.exitCode = code;
    });

  program
    .command("label")
    .description("Draft a golden file from a parser, or open the review page.")
    .argument("<file>", "Source file to draft, or a golden file with --review.")
    .option("--from <parser>", "Parser id that drafts the golden file.")
    .option("--out <path>", "Golden file to write. Default: golden/<stem>.yaml.")
    .option("--review", "Open the local review page for a golden file.")
    .option("-c, --config <path>", "Config file.", "docassay.yaml")
    .action(async (filePath: string, options: LabelFlags) => {
      const code = await labelCommand(filePath, {
        parser: options.from,
        out: options.out,
        review: options.review === true,
        config: options.config,
      });
      if (code !== 0) process.exitCode = code;
    });

  program
    .command("baseline")
    .description("Run the suite and write the baseline file.")
    .option("-c, --config <path>", "Config file.", "docassay.yaml")
    .option("--baseline <path>", "Baseline file to write. Default: the config baseline.")
    .option("-p, --parser <id>", "Limit to one parser. Repeatable.", collect, [])
    .option("--tag <tag>", "Limit to golden files with this tag. Repeatable.", collect, [])
    .option("--no-cache", "Ignore the cache for this run (still writes to it).")
    .option("--refresh", "Re-parse and overwrite cache entries.")
    .option("--concurrency <n>", "Parallel parse calls per parser.", "4")
    .option("--golden <name>", "Pass builtin to use the packaged golden set.")
    .action(async (options: GateFlags) => {
      const config = resolveGoldenFlag(options.config, options.golden);
      if (config === undefined) return;
      const code = await baselineCommand(config, gateOptions(options));
      if (code !== 0) process.exitCode = code;
    });

  program
    .command("check")
    .description("Re-run the suite and fail when a score regresses.")
    .option("-c, --config <path>", "Config file.", "docassay.yaml")
    .option("--baseline <path>", "Baseline file to compare. Default: the config baseline.")
    .option("--summary-only", "Skip per-document checks.")
    .option("-p, --parser <id>", "Limit to one parser. Repeatable.", collect, [])
    .option("--tag <tag>", "Limit to golden files with this tag. Repeatable.", collect, [])
    .option("--no-cache", "Ignore the cache for this run (still writes to it).")
    .option("--refresh", "Re-parse and overwrite cache entries.")
    .option("--concurrency <n>", "Parallel parse calls per parser.", "4")
    .option("--golden <name>", "Pass builtin to use the packaged golden set.")
    .action(async (options: GateFlags) => {
      const config = resolveGoldenFlag(options.config, options.golden);
      if (config === undefined) return;
      const code = await checkCommand(config, {
        ...gateOptions(options),
        summaryOnly: options.summaryOnly === true,
      });
      if (code !== 0) process.exitCode = code;
    });

  const cache = program.command("cache").description("Manage the parser output cache.");
  cache
    .command("clear")
    .description("Delete cached parser outputs.")
    .option("--parser <id>", "Delete entries for one parser id, including its variants.")
    .option("--cache <dir>", "Cache directory.", ".docassay/cache")
    .action((options: { parser?: string; cache: string }) => {
      const removed = clearCache(options.cache, options.parser);
      const scope = options.parser === undefined ? "" : ` for ${options.parser}`;
      process.stdout.write(`removed ${removed} cache file(s)${scope} from ${options.cache}\n`);
    });

  program
    .command("run")
    .description("Parse (or read the cache), score, and print a scorecard.")
    .option("-c, --config <path>", "Config file.", "docassay.yaml")
    .option("-p, --parser <id>", "Limit to one parser. Repeatable.", collect, [])
    .option("--tag <tag>", "Limit to golden files with this tag. Repeatable.", collect, [])
    .option("--format <fmt>", "terminal, json, md, or html.", "terminal")
    .option("-o, --out <path>", "Write the report to a file.")
    .option("--no-cache", "Ignore the cache for this run (still writes to it).")
    .option("--refresh", "Re-parse and overwrite cache entries.")
    .option("--concurrency <n>", "Parallel parse calls per parser.", "4")
    .option("-v, --verbose", "Print failures for every document.")
    .option("--golden <name>", "Pass builtin to use the packaged golden set.")
    .action(async (options: RunFlags) => {
      const config = resolveGoldenFlag(options.config, options.golden);
      if (config === undefined) return;
      try {
        const concurrency = Number(options.concurrency);
        const code = await runCommand(config, {
          parsers: options.parser,
          tags: options.tag,
          format: options.format,
          out: options.out,
          noCache: options.noCache === true,
          refresh: options.refresh === true,
          concurrency: Number.isInteger(concurrency) ? concurrency : 0,
          verbose: options.verbose === true,
        });
        if (code !== 0) process.exitCode = code;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        process.stderr.write(`${message}\n`);
        process.exitCode = 2;
      }
    });

  return program;
}

function resolveGoldenFlag(config: string, golden: string | undefined): string | undefined {
  try {
    return configForGoldenFlag(config, golden);
  } catch (err) {
    const message = err instanceof BuiltinError ? err.message : String(err);
    process.stdout.write(`${message}\n`);
    process.exitCode = 2;
    return undefined;
  }
}

interface GateFlags {
  config: string;
  golden?: string;
  baseline?: string;
  summaryOnly?: boolean;
  parser: string[];
  tag: string[];
  noCache?: boolean;
  refresh?: boolean;
  concurrency: string;
}

function gateOptions(options: GateFlags): {
  baseline?: string;
  parsers: string[];
  tags: string[];
  noCache?: boolean;
  refresh?: boolean;
  concurrency: number;
} {
  const concurrency = Number(options.concurrency);
  return {
    baseline: options.baseline,
    parsers: options.parser,
    tags: options.tag,
    noCache: options.noCache === true,
    refresh: options.refresh === true,
    concurrency: Number.isInteger(concurrency) ? concurrency : 0,
  };
}

interface LabelFlags {
  from?: string;
  out?: string;
  review?: boolean;
  config: string;
}

interface RunFlags {
  config: string;
  golden?: string;
  parser: string[];
  tag: string[];
  format: string;
  out?: string;
  noCache?: boolean;
  refresh?: boolean;
  concurrency: string;
  verbose?: boolean;
}

function collect(value: string, previous: string[]): string[] {
  return [...previous, value];
}

declare const __filename: string | undefined;

function moduleFilename(): string {
  const url = import.meta.url;
  if (typeof url === "string" && url.length > 0) return fileURLToPath(url);
  if (typeof __filename === "string" && __filename.length > 0) return __filename;
  return process.argv[1] ?? process.cwd();
}

/**
 * True when this file is the process entry point, including a symlinked bin.
 *
 * `npm install -g` points `argv` at a symlink. `import.meta.url` is the real
 * file. Both paths are resolved before they are compared.
 *
 * @example
 * ```ts
 * runsAsCli(process.argv[1], fileURLToPath(import.meta.url));
 * ```
 */
export function runsAsCli(argvPath: string | undefined, modulePath: string): boolean {
  if (argvPath === undefined || argvPath === "") return false;
  const resolvedArgv = path.resolve(argvPath);
  const resolvedModule = path.resolve(modulePath);
  if (resolvedArgv === resolvedModule) return true;
  try {
    return realpathSync(resolvedArgv) === realpathSync(resolvedModule);
  } catch {
    return false;
  }
}

if (runsAsCli(process.argv[1], moduleFilename())) {
  createProgram().parse();
}
