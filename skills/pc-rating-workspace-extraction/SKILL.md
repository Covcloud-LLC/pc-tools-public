---
name: pc-rating-workspace-extraction
description: Write one PC line's rating workspace (the objects its calc routines read and write, nested under the line object with each property's declared type) from the line's ratebook folders, its product capture and the checkout. Use when the user types /pc-rating-workspace-extraction, or says "extract the rating workspace", "what objects do the calc routines use", "type the routine inputs".
argument-hint: '--product-root <dir> --source-root <checkout> [--out <dir>] [--no-platform-jar] [--overwrite]'
---

# Rating workspace extraction

Follow [PROMPT.md](PROMPT.md). Resolve `<skill-dir>` to this skill's directory, including
when it is installed outside pc-tools. Inputs, flags, refusals and the output format are in
[README.md](README.md).
