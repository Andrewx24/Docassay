#!/usr/bin/env node
import { Command } from "commander";

import { version } from "./version.js";

const program = new Command();

program
  .name("docassay")
  .description("Score document parsers against labeled golden files.")
  .version(version, "--version", "Print the version and exit.");

program.parse();
