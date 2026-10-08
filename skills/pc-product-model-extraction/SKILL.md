---
name: pc-product-model-extraction
description: Extract one classic PC 10.2.3 product and line into validated native JSON, preserving declarations, entities, delegates, typelists and costs without semantic-role decisions. Use for product-model extraction or capture of a configured line.
argument-hint: '--source-root <checkout> --product <ProductCode> --line <LineCode> --out <directory> [--overwrite]'
---

# Native product extraction

Follow [PROMPT.md](PROMPT.md). Resolve `<skill-dir>` to this skill's directory, including
when it is installed outside pc-tools. Flags, exit codes, scope and the capture format
are in [README.md](README.md).
