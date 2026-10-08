---
name: pc-rating-access-report
description: Collect one PC line's rating access report in any environment where the extraction skills are installed. Runs the four extraction skills into one product root, then writes RATING-ACCESS-REPORT-<Line>.md and extracted/rating-access.json (every object the engines and calc routines touch with its backing, every write with its actor and target, every entity-backed read including lookup arguments, the per-engine routine parameter bindings, the unresolved calls) plus headed manual sections to fill from the checkout. Use when the user types /pc-rating-access-report, or says "rating access report", "what does the rating engine read and write", "which routine stores reach an entity", "collect the mutation-safety facts".
argument-hint: '--product-root <dir> [--out <dir>] [--overwrite]'
---

# Rating access report

Follow [PROMPT.md](PROMPT.md). Resolve `<skill-dir>` to this skill's directory, including
when it is installed outside pc-tools; the four extraction skills it runs first are the sibling
directories installed beside it. Inputs, flags, refusals, exit codes and the output format are
in [README.md](README.md).
