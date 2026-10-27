import { readFileSync } from "node:fs";

import { defineConfig } from "tsup";

const version = (
  JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as {
    version: string;
  }
).version;

export default defineConfig({
  entry: ["src/index.ts", "src/verify.ts", "src/cli.ts"],
  format: ["esm", "cjs"],
  dts: true,
  clean: true,
  sourcemap: true,
  define: {
    __DOCASSAY_VERSION__: JSON.stringify(version),
  },
  outExtension({ format }) {
    return { js: format === "cjs" ? ".cjs" : ".js" };
  },
});
