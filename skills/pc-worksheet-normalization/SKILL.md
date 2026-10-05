---
name: pc-worksheet-normalization
description: Convert local PC worksheets XML into comparable JSON containing final scoped identifier values. Use for worksheet normalization, not database extraction, rating generation, or two-file comparison.
---

# Normalize worksheet values

Use `scripts/worksheet-normalize.py` relative to this skill directory, with
Python 3.8+ (standard library only). Obtain the supplied XML path and desired JSON
destination; ask for either only if unavailable. [README.md](README.md) documents
supported shapes, the JSON contract and failure remedies.

```sh
python3 /path/to/pc-worksheet-normalization/scripts/worksheet-normalize.py \
  --input /path/to/worksheets.xml --output /path/to/comparable.json
```

Run the script with the user's paths. Add `--overwrite` only when replacement is
requested. Do not generate or edit comparable values yourself. Do not alter the
XML or silently simplify unsupported input to make it pass.

Inspect the exit code and JSON result. Report success only for exit 0 and
`status: ok`; return the clickable absolute saved path and worksheet/identifier
counts. On failure, report its category and actionable message. Existing output
is preserved by default; choose a new destination or obtain explicit replacement
instructions if needed. A failed conversion must never be reported as success.

The saved contract is version 2, preserving optional worksheet `Tag` alongside
subject references, dates and routine metadata. Successful conversion does not
prove cross-run subject correspondence. Do not infer business keys from descriptions,
opaque displays or array order; relay missing-context limitations when comparison
readiness is part of the request.

Every output declares v2, even without Tag; there is no legacy-output mode. Preserve
absent versus empty Tag. Never relabel saved JSON or discard Tag for compatibility.
V1-only consumers need an update or retained v1 files. Missing identity metadata,
duplicate worksheet metadata and empty identifier arrays are preserved; they do
not imply successful downstream matching.

Relay any `warnings` in a successful result so the user can clean up a residual
temporary file if filesystem permissions prevented its removal.
