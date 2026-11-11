set -euo pipefail
docassay init
mkdir -p golden/files out/parsed
printf '%s\n' 'Replace this with a sentence from the document' > out/parsed/example.md
printf '%s\n' '%PDF-1.1' > golden/files/example.pdf
docassay run
