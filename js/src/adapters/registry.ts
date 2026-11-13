/**
 * Built-in adapter ids and third-party adapters registered with {@link registerAdapter}.
 *
 * @example
 * ```ts
 * import { resolve } from "./adapters/registry.js";
 *
 * const adapter = resolve("llamaparse:fast");
 * ```
 */

import {
  AdapterConfigError,
  AdapterError,
  type Adapter,
  type RawResult,
} from "./base.js";
import { DoclingAdapter } from "./docling.js";
import { FileAdapter } from "./file.js";
import { LlamaParseAdapter } from "./llamaparse.js";
import type { Document, Source } from "../models/document.js";

/** Built-ins in the Adapters doc order. Variants follow the protocol comment. */
export const BUILTINS: Record<string, readonly string[]> = {
  docling: [],
  file: [],
  llamaparse: ["fast", "cost_effective", "agentic", "agentic_plus"],
  reducto: [],
};

/** Callable registered by a third party. `id` is the name before `:variant`. */
export type AdapterFactory = ((
  variant: string | null,
  options: Record<string, unknown>,
) => Adapter) & {
  readonly id: string;
  readonly variants: readonly string[];
};

const extras = new Map<string, AdapterFactory>();

class PendingAdapter implements Adapter {
  readonly id: string;
  readonly variants: readonly string[];
  readonly variant: string | null;
  readonly options: Record<string, unknown>;

  constructor(
    id: string,
    variants: readonly string[],
    variant: string | null,
    options: Record<string, unknown>,
  ) {
    this.id = id;
    this.variants = variants;
    this.variant = variant;
    this.options = options;
  }

  available(): Promise<{ ok: boolean; reason?: string }> {
    return Promise.resolve({
      ok: false,
      reason: `${this.id} adapter is not available in this build`,
    });
  }

  cacheIdentity(): Record<string, unknown> {
    return { id: this.id, variant: this.variant, options: { ...this.options } };
  }

  fetch(path: string): Promise<RawResult> {
    return Promise.reject(
      new AdapterError(`${this.id} adapter is not available in this build; cannot fetch ${path}`),
    );
  }

  toDocument(raw: RawResult, source: Source): Document {
    void raw;
    void source;
    throw new AdapterError(`${this.id} adapter is not available in this build`);
  }
}

/**
 * Register a third-party adapter. Built-in ids cannot be replaced.
 *
 * @example
 * ```ts
 * registerAdapter(Object.assign(createFile, { id: "mine", variants: [] }));
 * ```
 */
export function registerAdapter(factory: AdapterFactory): void {
  if (factory.id in BUILTINS) {
    throw new AdapterConfigError(`cannot replace built-in adapter "${factory.id}"`);
  }
  extras.set(factory.id, factory);
}

/** Drop third-party adapters. Built-ins stay registered. */
export function clearRegisteredAdapters(): void {
  extras.clear();
}

/**
 * Build the adapter for `id` or `id:variant`.
 *
 * Unknown ids and variants throw {@link AdapterConfigError} (exit 2). The
 * message lists every id this process can load.
 *
 * @example
 * ```ts
 * const adapter = resolve("docling", { mode: "http" });
 * ```
 */
export function resolve(parserId: string, options: Record<string, unknown> = {}): Adapter {
  const [adapterId, variant] = splitId(parserId);
  const factory = factoryFor(adapterId);
  const ids = validIds();
  if (factory === undefined) {
    throw new AdapterConfigError(`unknown parser id "${parserId}"; valid ids: ${ids}`);
  }
  if (variant === null && factory.variants.length > 0) {
    throw new AdapterConfigError(
      `missing variant for "${adapterId}"; valid ids: ${ids}; valid variants: ${factory.variants.join(", ")}`,
    );
  }
  if (variant !== null && !factory.variants.includes(variant)) {
    const detail = factory.variants.length > 0 ? `; valid variants: ${factory.variants.join(", ")}` : "";
    throw new AdapterConfigError(
      `unknown variant "${variant}" for "${adapterId}"; valid ids: ${ids}${detail}`,
    );
  }
  return factory(variant, options);
}

/**
 * Return the adapter ids a config error should list, in a stable order.
 *
 * @example
 * ```ts
 * console.log(validIds());
 * ```
 */
export function validIds(): string {
  const extra = [...extras.keys()].filter((id) => !(id in BUILTINS)).sort();
  return [...Object.keys(BUILTINS), ...extra].join(", ");
}

function factoryFor(adapterId: string): AdapterFactory | undefined {
  if (adapterId === "file") {
    const variants = BUILTINS.file ?? [];
    return Object.assign(
      (variant: string | null, options: Record<string, unknown>) =>
        new FileAdapter(variant, options),
      { id: "file", variants },
    );
  }
  if (adapterId === "docling") {
    const variants = BUILTINS.docling ?? [];
    return Object.assign(
      (variant: string | null, options: Record<string, unknown>) =>
        new DoclingAdapter(variant, options),
      { id: "docling", variants },
    );
  }
  if (adapterId === "llamaparse") {
    const variants = BUILTINS.llamaparse ?? [];
    return Object.assign(
      (variant: string | null, options: Record<string, unknown>) =>
        new LlamaParseAdapter(variant, options),
      { id: "llamaparse", variants },
    );
  }
  if (adapterId in BUILTINS) {
    const variants = BUILTINS[adapterId] ?? [];
    const pending: AdapterFactory = Object.assign(
      (variant: string | null, options: Record<string, unknown>) =>
        new PendingAdapter(adapterId, variants, variant, options),
      { id: adapterId, variants },
    );
    return pending;
  }
  return extras.get(adapterId);
}

function splitId(parserId: string): [string, string | null] {
  const colon = parserId.indexOf(":");
  if (colon === -1) return [parserId, null];
  return [parserId.slice(0, colon), parserId.slice(colon + 1)];
}
