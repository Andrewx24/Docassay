#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Command } from "commander";

import { clearCache } from "./cache.js";
import { initCommand } from "./config.js";
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
    .action((filePath: string, options: { strict?: boolean }) => {
      const code = validateCommand(filePath, { strict: options.strict === true });
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
    .action(async (options: RunFlags) => {
      try {
        const concurrency = Number(options.concurrency);
        const code = await runCommand(options.config, {
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

interface LabelFlags {
  from?: string;
  out?: string;
  review?: boolean;
  config: string;
}

interface RunFlags {
  config: string;
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

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invoked !== "" && invoked === path.resolve(moduleFilename())) {
  createProgram().parse();
}
