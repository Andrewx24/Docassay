/**
 * Injected at build time by tsup, and by Vitest, from package.json.
 * scripts/sync-version.sh copies /VERSION into that file.
 */
declare const __DOCASSAY_VERSION__: string;

export const version = __DOCASSAY_VERSION__;
