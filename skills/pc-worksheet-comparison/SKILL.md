---
name: pc-worksheet-comparison
description: Compare two local PC final-values v2 JSON captures from rating runs of the same job and quote branch. Use for worksheet comparison, not extraction, normalization, rerating, or human report generation.
---

# Compare worksheets

Use the bundled Python 3.8+ script to produce comparison JSON. Read
[README.md](README.md) for input validation, correspondence, counts and statuses.

Obtain the baseline JSON, candidate JSON and destination paths from the request.
Resolve which capture is baseline if unclear. The caller supplies separately
retained captures of the same job and quote branch; this format cannot attest
job/branch identity or prove that another rating occurred. Do not fetch or rerate
to replace missing history. Comparison uses only the two JSON files.

Run the script from this installed skill directory (resolve its actual absolute
location, independent of the working directory):

```sh
python3 /path/to/pc-worksheet-comparison/scripts/worksheet-compare.py \
  --baseline /path/to/baseline.json --candidate /path/to/candidate.json \
  --output /path/to/comparison.json
```

Use `--overwrite` only when replacement of the destination is requested. Never
edit, relabel, repair, strip fields from, or replace an input to bypass validation.
If inputs are unsupported, explain the error; obtaining v2 from original XML is
a separate normalization task.

Read the process status and saved result. Exit 0 means complete `equal` or
`different`; exit 3 means a published `incomplete` result with unresolved
correspondence. Exit 2 means an error and no new result. For incomplete results,
state the established-pair and unresolved counts; never describe equal established
pairs as full equality. Give the output path and key counts without generating a
human comparison report. Opaque/receiver changes describe recorded text only.
