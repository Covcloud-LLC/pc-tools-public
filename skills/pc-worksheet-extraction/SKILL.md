---
name: pc-worksheet-extraction
description: Fetch the complete retained rating worksheets XML for a PC job number from SQL Server into a local file. Use for worksheet retrieval, not rating generation, historical run selection, normalization, or comparison.
---

# Fetch worksheets by job number

Use the bundled `scripts/worksheet-extract.py`; resolve this path relative to this
skill directory even when installed elsewhere. [README.md](README.md) describes
setup, connection options, output semantics and failures.

Obtain the exact job number (preserving leading zeros), authorized SQL Server
connection configuration and destination. Reuse connection details already supplied
in the session. If the user only gives a job number, use the existing configured
target and save `worksheets-<job-number>.xml` in the working directory (use a safe
filename if the job number contains path characters). Ask for missing connection
information only when it is unavailable; never guess another database.

Use an available Python environment with the bundled `requirements.txt` installed.
For a checkout's literal JDBC configuration:

```bash
python /path/to/pc-worksheet-extraction/scripts/worksheet-extract.py \
  --job-number '0000123456' \
  --pc-database-config /path/to/pc-checkout/modules/configuration/config/database-config.xml \
  --output /chosen/local/worksheets-0000123456.xml
```

The alternative is `PC_WS_*` environment variables as documented in the guide.
Do not display credentials, the raw JDBC URL or full worksheet contents. Run the
script with the user's values, inspect its exit code and JSON result, and report
success only for exit 0 with `status: ok`. Return the clickable absolute output
path, source server/database, job number and worksheet count. The script validates
XML and fidelity; do not rewrite, normalize or redact its output.

Report a nonzero result by its error category and the relevant remedy in the guide.
If the output already exists, choose a new destination unless replacement was
requested. Multiple retained blobs are an explicit unsupported ambiguity: do not
select a version with ad hoc SQL. Do not rerate, generate worksheets, modify database
records or fabricate an example to get past missing data. Repository fixtures must
be synthetic or non-sensitive; ordinary local output stays faithful and local.
