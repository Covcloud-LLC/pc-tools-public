---
name: pc-worksheet-extraction
description: Fetch the complete retained rating worksheets XML for a PC job number from SQL Server into a local file. Use for worksheet retrieval, not rating generation, historical run selection, normalization, or comparison.
---

# Fetch worksheets by job number

[README.md](README.md) holds setup, connection options, output and failure
remedies.

1. Get the exact job number (keep leading zeros), the connection and the
   destination. Reuse connection details already given in the session. With only
   a job number, use the configured connection and save `worksheets-<job>.xml` in
   the working directory. Pass the job number unchanged to `--job-number`, but if
   it contains `/`, `\`, `..` or other path characters, replace them in the
   filename so the file stays in the working directory. Never guess another
   database.
2. Run the script from this skill's directory, wherever it is installed, with a
   Python that has `requirements.txt` installed:

   ```bash
   python /path/to/pc-worksheet-extraction/scripts/worksheet-extract.py \
     --job-number '<job>' \
     --pc-database-config /path/to/checkout/modules/configuration/config/database-config.xml \
     --output <worksheets.xml>
   ```

   Leave out `--pc-database-config` to use the `PC_WS_*` environment variables.
   Add `--overwrite` only when the user asks to replace the destination. Do not
   print credentials or the XML.
3. Success is exit 0 with `status: ok`: return the saved path, source
   server/database, job number, worksheet count and any `warnings`. Otherwise
   report the error category and the README's remedy. Do not pick between
   several retained blobs, rerate, or change database records.
