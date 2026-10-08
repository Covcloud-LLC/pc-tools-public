---
name: pc-rating-process-extraction
description: Extract how one PC line is rated today by one engine (dispatch, call graph, units of work with their method bodies and ratebook routines, each unit's dataflow facts, lookups, emissions) from a checkout into one PC-native rating-process document, joined to the line's product capture, with a human gate for engine selection and the facts the source does not spell out. Use when the user types /pc-rating-process-extraction, or says "extract the rating process", "how does PersonalAutoLine rate", "what does CPRatingEngine do", "run the rating-process extractor".
argument-hint: '[--source-root <pc checkout>] --line <LineCode> --product <ProductCode> --product-root <dir> [--engine <EngineClass>] [--decisions <yaml>] [--force]'
---

# Rating process extraction

Follow [PROMPT.md](PROMPT.md). Resolve `<skill-dir>` and the script path to this skill's
directory, including when it is installed outside pc-tools. Flags, outputs, the document and the
decisions file are in [README.md](README.md).
