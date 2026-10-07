---
name: pc-worksheet-normalization
description: Convert local PC worksheets XML into comparable JSON containing final scoped identifier values. Use for worksheet normalization, not database extraction, rating generation, or two-file comparison.
---

# Normalize worksheet values

[README.md](README.md) holds the supported XML, the JSON contract and the failure
remedies.

1. Get the XML path from the request. Without a destination, the JSON goes to
   `worksheets-normalized.json` beside the XML; pass `--output <path>` only when
   the user names another destination.
2. Run the script from this skill's directory, wherever it is installed:

   ```sh
   python3 /path/to/pc-worksheet-normalization/scripts/worksheet-normalize.py \
     --input <worksheets.xml>
   ```

   Add `--overwrite` only when the user asks to replace the destination. Do not
   edit the XML or write values yourself to get past a failure.
3. Success is exit 0 with `status: ok`: return the saved path, the worksheet and
   identifier counts, and any `warnings`. Otherwise report the error category and
   the README's remedy.
