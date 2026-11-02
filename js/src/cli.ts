#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Command } from "commander";

import { clearCache } from "./cache.js";
import { initCommand } from "./config.js";
import { validateCommand } from "./golden.js";
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

  return program;
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
