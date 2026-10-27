# js/

The `docassay` package for npm, written in TypeScript. Use npm, not pnpm or yarn.

```bash
cd js
npm ci
npm run lint && npm run typecheck && npm test && npm run build
node dist/cli.js --version
```

`/VERSION` is the source of truth. `scripts/sync-version.sh` copies it into `package.json`.

Layout and naming: [docs/docs/architecture/repo-layout.md](../docs/docs/architecture/repo-layout.md)
Packaging: [docs/docs/architecture/packaging.md](../docs/docs/architecture/packaging.md)
