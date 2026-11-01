#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Command } from "commander";

import { validateCommand } from "./golden.js";
import { version } from "./version.js";

declare const __filename: string;

function modulePath(): string {
  if (import.meta.url) return fileURLToPath(import.meta.url);
  return __filename;
}

/**
 * Build the `docassay` command.
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
    .command("validate")
    .description("Check golden files against the schema.")
    .argument("[path]", "golden set directory or one golden file", "golden")
    .option("--strict", "fail if a file still contains # TODO verify")
    .action((filePath: string, options: { strict?: boolean }) => {
      const code = validateCommand(filePath, { strict: options.strict === true });
      if (code !== 0) process.exitCode = code;
    });
  return program;
}

const entry = process.argv[1];
if (entry && path.resolve(modulePath()) === path.resolve(entry)) {
  createProgram().parse();
}
