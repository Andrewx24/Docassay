/**
 * pytest for document ingestion.
 *
 * @example
 * ```ts
 * import { version } from "docassay";
 * console.log(version);
 * ```
 */
export { ConfigError, loadConfig } from "./config.js";
export { version } from "./version.js";
export type { Config } from "./models/config.js";
export {
  AdapterConfigError,
  AdapterError,
  AdapterUnavailable,
} from "./adapters/base.js";
export type { Adapter, RawResult } from "./adapters/base.js";
export { registerAdapter, resolve } from "./adapters/registry.js";
export { version } from "./version.js";
export { loadGolden } from "./golden.js";
export type { Document } from "./models/document.js";
