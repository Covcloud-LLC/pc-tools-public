# Convert a local worksheets XML to comparable JSON

Convert one worksheets XML file to final-values JSON:

- Bundle: `<pc-worksheet-normalization directory>`
- Worksheet XML: `<input path>`
Read the bundle's README.md, then run:

```sh
python3 <bundle>/scripts/worksheet-normalize.py --input <worksheet-XML>
```

This writes `worksheets-normalized.json` beside the XML. If I name another
destination, add `--output <JSON-destination>`. Quote the paths. Add `--overwrite` only if I ask to replace the destination. Do
not edit the XML or write values yourself.

Only exit 0 with `status: ok` is success: tell me the saved path, the entry
count (`worksheet_count`, one entry per routine), the identifier count, and any
`warnings`. On failure, tell me the error category
and the README's remedy.
