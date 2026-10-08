---
name: pc-ratebook-extraction
description: Turn one exported PC Rate Management (RTM) ratebook XML into an analysis set a human or an LLM can read (JSON for the book, its rate tables and calc routines, pseudo-code per routine, CSV per table). Use when the user types /pc-ratebook-extraction, or says "extract the ratebook", "what rate tables and routines does this book have", "render the calc routines", "run the ratebook extractor".
argument-hint: '--ratebook <export.xml> [--product-root <dir> | --out <dir>] [--steps full|summary|none] [--rows all|sample|none] [--row-limit N] [--force]'
---

# Ratebook extraction

Follow [PROMPT.md](PROMPT.md). Resolve `<skill-dir>` to this skill's directory, including
when it is installed outside pc-tools. Flags, exit codes and the output format are in
[README.md](README.md).
