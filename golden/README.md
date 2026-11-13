# golden/

Twenty labeled documents and pre-parsed Markdown for `docassay run`. The file adapter reads `parsed/<id>.md`, so the run needs no API keys. Seventeen of the files are produced by `golden/generate/build.py`. `SOURCES.md` lists the redistribution right for each one. `RESULTS.md` is the scorecard from the committed parses.

`docassay run --golden builtin` uses the copy of this folder shipped inside the Python and Node packages.

Format: [docs/docs/architecture/golden-sets.md](../docs/docs/architecture/golden-sets.md)
