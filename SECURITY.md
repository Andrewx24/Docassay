# Security

docassay is pre-alpha. Report a vulnerability privately. Do not open a public GitHub issue for a security problem.

## How to report

Open a private advisory:

https://github.com/Andrewx24/Docassay/security/advisories/new

Include:

- What is affected (package, version from `VERSION`, and whether it is Python, TypeScript, or the docs site)
- Steps to reproduce, or a proof of concept that does not include live credentials
- The impact you expect (data leak, code execution, score forgery, and so on)

## What happens next

A maintainer will acknowledge the report, confirm or reject it, and agree on a fix before any public disclosure. Please give them a reasonable window before writing about the issue in public.

## Scope

In scope: the `docassay` Python package, the `docassay` npm package, the GitHub Action in this repo, and the docs site published from this repo.

Out of scope: vulnerabilities in a third-party parser (Docling, LlamaParse, Reducto, and others). Report those to the parser's own project. A docassay adapter that leaks an API key or sends documents somewhere the adapter docs do not describe is in scope.

There is no hosted docassay service. Do not send personal documents or API keys in the report.
