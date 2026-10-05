# Fetch retained PC worksheets

Fetch the retained rating worksheets for one job into a local XML file:

- Job number: `<job number>` (keep leading zeros)
- Destination: `<output XML path>`
- Bundle: `<pc-worksheet-extraction directory>`
- Connection: `<database-config.xml path>`, or the `PC_WS_*` environment variables
- Python with the bundle's `requirements.txt` installed: `<python path>`

Read the bundle's README.md, then run:

```bash
<python> <bundle>/scripts/worksheet-extract.py --job-number '<job number>' \
  --pc-database-config <database-config.xml> --output <output XML path>
```

Leave out `--pc-database-config` when using the environment variables. Add
`--overwrite` only if I ask to replace the destination. Do not print credentials
or the XML, and do not change database records.

Only exit 0 with `status: ok` is success: tell me the saved path, source
server/database, job number, worksheet count and any `warnings`. On failure, tell
me the error category and the README's remedy.
