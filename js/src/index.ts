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
export type { Document } from "./models/document.js";
