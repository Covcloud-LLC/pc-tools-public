# PC worksheet extraction

Fetch the saved rating worksheets for a job number into one local XML file. The
supported target is the PC 10.2.3 SQL Server schema (SQL Server
2022). The file is the exact decompressed stored XML, including whitespace,
identifiers, dates, decimal precision and every worksheet. No normalization,
redaction, rating generation or historical rating-run selection takes place.

## Setup

Use Python 3.8+ and `pymssql` 2.3 or later, below 3. The SQL Server driver is the
bundle's only non-standard-library dependency. For example, from this directory:

```bash
python3 -m venv /tmp/pc-worksheet-venv
/tmp/pc-worksheet-venv/bin/python -m pip install -r requirements.txt
```

Use the platform's equivalent paths on Windows. If your Python/platform has no
pymssql wheel, use a supported Python with a wheel or follow
[pymssql's installation instructions](https://pymssql.readthedocs.io/en/stable/intro.html).
The driver uses FreeTDS; wheels bundle its native connectivity components. Python
3.12 with pymssql 2.4.2 is a supported local setup. No PC libraries or
other repository code are runtime dependencies.

## Connection

Use an authorized SQL Server login with SELECT access to `dbo.pc_job`,
`dbo.pc_policyperiod`, `dbo.pc_worksheetcontainer` and `dbo.pc_worksheetdata`.
The script sends a parameterized SELECT and session settings only. It requests
read-only connection intent, uses a SERIALIZABLE transaction, and closes without
committing. Read-only intent is a routing hint, not a permission boundary; a
SELECT-only login supplies that boundary. Reads can briefly block concurrent
rating updates; `--timeout` bounds database waits (default 30 seconds).

For a local PC 10.2.3 checkout, pass `--pc-database-config` pointing at
`modules/configuration/config/database-config.xml`. The file must contain exactly
one SQL Server database and a `dbcp-connection-pool` with the literal JDBC shape
`jdbc:sqlserver://HOST:PORT;DatabaseName=DB;User=USER;Password=PASSWORD`.
Port is optional (1433). Property names are case-insensitive. Properties may appear
in any order. Credentials are read into memory, never printed. Other JDBC options,
braced/escaped values, placeholders, JNDI, integrated authentication and named
instances are not interpreted; use environment configuration instead.

Alternatively, set these variables through your local secret manager or shell:

| Variable | Meaning |
|---|---|
| `PC_WS_SERVER` | SQL Server hostname or address |
| `PC_WS_PORT` | TCP port, default 1433 |
| `PC_WS_DATABASE` | Database name |
| `PC_WS_USER` | SQL authentication user |
| `PC_WS_PASSWORD` | SQL authentication password |

All except the port are required in environment mode. A supplied configuration
file takes precedence as a complete connection source; no partial mixing occurs.
No password flag exists. Do not paste secrets into prompts or commit configuration
files. This bundle uses the driver's default transport configuration; the local
acceptance target does not establish remote TLS/certificate configuration support.

## Run directly

From this bundle directory:

```bash
/tmp/pc-worksheet-venv/bin/python scripts/worksheet-extract.py \
  --job-number '0000123456' \
  --pc-database-config /path/to/pc-checkout/modules/configuration/config/database-config.xml \
  --output /tmp/worksheets-0000123456.xml
```

Omit `--pc-database-config` to use the environment. Preserve leading zeros and case
in the job number. The destination parent must exist. Existing destinations are
refused; use another path or explicitly add `--overwrite` to atomically replace
one. The script prepares a complete file before publishing it, with owner-only
permissions on POSIX. It leaves any existing output intact on retrieval, validation
or publication failure. Output filesystems must support atomic rename and, for
exclusive publication, hard links. `--help` lists the flags.

On success, exit 0 and one JSON object on stdout report `status: ok`, absolute
`output`, `source_server`, `source_port`, `source_database`, `job_number`, the
period/container/data IDs, worksheet count, XML size/SHA-256, and stored GZIP
size/SHA-256. If the temporary file cannot be removed after publication, `warnings`
names it; the saved XML is complete, and the residual file holds the same worksheet
data, so delete it. No worksheet text appears on stdout. On failure, a JSON error goes to
stderr, no success is printed, and no new destination is published. Argument-parser
usage errors use standard argparse text and exit 2.

## What is retrieved

The schema joins `pc_job.ID` to `pc_policyperiod.JobID`, then
`pc_worksheetcontainer.Branch`, then `pc_worksheetdata.WorksheetContainer`.
Only active (`Retired = 0`) rows participate. `WorksheetData.Data` is GZIP-compressed
XML containing a `Worksheets` root and its `Worksheet` children. SQL Server length
and SHA-256 are checked against the retrieved binary before decompression; XML is
validated without being serialized again. The blob is fetched as binary, with a
large TEXTSIZE setting to avoid driver-side truncation.

PC retains the latest rating data in this container; database update
timestamps are not treated as historical run identifiers. The script examines all
active matching periods/containers. No blob means no saved worksheets; exactly
one non-null blob is exported in full. Multiple non-null blobs or duplicate job
identities produce an ambiguity error. It does not infer which quote version is
latest, merge XML documents or silently discard retained content. Multi-quote
selection is not supported. DiagnosticRatingWorksheet.TextData is separate error
diagnostic data, not the saved rating worksheet container exported here. Archived,
retired, purged and externally extracted data are not restored.

The complete compressed blob and XML must fit in local memory. The script rejects
malformed GZIP/XML, non-Worksheets roots and DTDs. It validates well-formedness and
container structure, not business correctness of a rating calculation.

## Failures

| Exit | Category | Meaning / remedy |
|---|---|---|
| 2 | configuration / dependency | Check connection variables or supported JDBC shape; install requirements in the Python used to run the script. |
| 3 | unknown-job | No exact active job number; check source database and leading zeros. |
| 4 | no-worksheets | Job exists, but no saved blob or no Worksheet children; obtain a job with saved worksheets. Extraction does not rerate it. |
| 5 | database | Connection/login/SELECT/schema/timeout failure; check server, permissions and supported schema. Raw driver errors are suppressed to protect connection details. |
| 6 | output-write | Parent missing, existing file, permissions, space or unsupported filesystem; fix destination or explicitly request overwrite. |
| 7 | invalid-content | Binary length/hash mismatch or invalid compressed/XML content; inspect the source without substituting partial output. |
| 8 | ambiguous-job / ambiguous-worksheets | More than one job identity or retained blob; no selection made. |

## Use with an assistant

Load [SKILL.md](SKILL.md) through your assistant's skill support and ask “Fetch the
worksheets for job 0000123456,” supplying the connection and destination if they
are not already available. The repository's `install.sh` installs the bundle into
Claude Code; a Codex installation can copy or symlink this entire directory into
its skills directory. All script paths resolve within the bundle.

Without skill support, copy [PROMPT.md](PROMPT.md), fill its inputs and send it to
an assistant with local shell access. Both workflows execute the same script and
return the saved path, source database, job number and count.
