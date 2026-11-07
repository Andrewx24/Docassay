/**
 * Locate the golden set shipped inside the package.
 *
 * @example
 * ```ts
 * import { builtinConfigPath } from "./builtin.js";
 *
 * builtinConfigPath();
 * ```
 */

import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** `--golden` was not `builtin`, or the packaged set is not installed. */
export class BuiltinError extends Error {
  readonly exitCode = 2;

  /**
   * @example
   * ```ts
   * throw new BuiltinError('unknown --golden value "nope". Use --golden builtin.');
   * ```
   */
  constructor(message: string) {
    super(message);
    this.name = "BuiltinError";
  }
}

/**
 * Return `docassay.yaml` for the packaged public golden set.
 *
 * Search `builtin-golden` next to the package, then parent directories for a
 * checkout's `golden/docassay.yaml`. `origin` is this module's directory
 * unless a test passes another path.
 *
 * @example
 * ```ts
 * const config = builtinConfigPath();
 * ```
 */
export function builtinConfigPath(origin?: string): string {
  const here = origin ?? path.dirname(fileURLToPath(import.meta.url));
  const packaged = path.join(here, "..", "builtin-golden", "docassay.yaml");
  if (existsSync(packaged)) return packaged;
  let dir = here;
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = path.join(dir, "golden", "docassay.yaml");
    if (existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new BuiltinError(
    "packaged golden set not found. Reinstall docassay or run from a checkout " +
      "that contains golden/docassay.yaml.",
  );
}

/**
 * Return the `sets` directory next to the packaged config.
 *
 * @example
 * ```ts
 * const folder = builtinSetsPath();
 * ```
 */
export function builtinSetsPath(origin?: string): string {
  return path.join(path.dirname(builtinConfigPath(origin)), "sets");
}

/**
 * Return `config`, or the packaged config when `golden` is `builtin`.
 *
 * @example
 * ```ts
 * configForGoldenFlag("docassay.yaml", "builtin");
 * ```
 */
export function configForGoldenFlag(config: string, golden: string | undefined): string {
  if (golden === undefined) return config;
  if (golden !== "builtin") {
    throw new BuiltinError(`unknown --golden value "${golden}". Use --golden builtin.`);
  }
  return builtinConfigPath();
}
