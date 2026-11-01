#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Command } from "commander";

import { initCommand } from "./config.js";
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

  return program;
}

function moduleFilename(): string {
  if (typeof import.meta.url === "string" && import.meta.url.length > 0) {
    return fileURLToPath(import.meta.url);
  }
  if (typeof __filename === "string" && __filename.length > 0) return __filename;
  return process.argv[1] ?? process.cwd();
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invoked === path.resolve(moduleFilename())) {
  createProgram().parse();
}
