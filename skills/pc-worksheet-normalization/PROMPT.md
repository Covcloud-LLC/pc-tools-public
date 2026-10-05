# Convert a local worksheets XML to comparable JSON

Use this prompt with a session that can run local commands. Supply:

- Bundle directory: `<absolute path to pc-worksheet-normalization>`
- Worksheet XML: `<absolute input path>`
- JSON destination: `<absolute output path>`
- Replace existing destination: `no` (change to `yes` only when intended)

Run the supplied bundle's `scripts/worksheet-normalize.py` with Python 3.8+
standard library; no installation or database connection is needed:

```sh
python3 <bundle-directory>/scripts/worksheet-normalize.py \
  --input <worksheet-XML> --output <JSON-destination>
```

Quote paths as needed. Add `--overwrite` only when replacement is requested.
Ask for missing paths if they cannot be resolved from the request. Consult the
bundle's README.md for the supported XML shapes and output contract.

The script selects the final recorded value for each qualified identifier,
including later reads and explicit missing values. Do not author comparable
values, rewrite the XML, or remove unsupported content. This task converts one
file; it does not extract worksheets or compare two rating runs.

Output uses contract version 2 and retains optional worksheet `Tag`. Keep source
references, dates and routine metadata separate from final values. Conversion
success does not prove that worksheet subjects can be matched between runs; do
not invent missing business keys or treat descriptions or ordering as identities.
Every output is v2, including untagged worksheets; no legacy-output flag exists.
Keep absent and empty Tag distinct. Do not relabel saved JSON, discard Tag, or
invent missing metadata to satisfy a consumer. V1-only consumers must update or
continue using retained v1 files. Empty identifier arrays and duplicate or missing
worksheet identity metadata do not establish downstream matchability.

Inspect both the exit code and JSON result. Only exit 0 with `status: ok` is
success: return the clickable absolute output path and worksheet/identifier
counts. For failure, report the category and remedy from the error message.
Preserve existing output unless replacement was requested; do not report a
partial or failed conversion as successful.

Relay any `warnings` in a successful result so the user can clean up a residual
temporary file if filesystem permissions prevented its removal.
