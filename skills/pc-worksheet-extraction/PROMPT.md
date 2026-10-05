# Fetch retained PC worksheets

Use this prompt with an assistant that can run local Python scripts. Replace the
bracketed inputs; the bundle may be installed anywhere. No skill installation is
required.

> Fetch the retained rating worksheets for PC job `[JOB_NUMBER]` into
> `[ABSOLUTE_OUTPUT_XML_PATH]`. Use the extraction bundle at `[BUNDLE_DIRECTORY]`
> and the authorized connection `[DATABASE_CONFIG_XML_PATH or existing
> PC_WS_* environment variables]`. Reuse the Python environment `[PYTHON_PATH]`.
>
> Read the bundle's README.md for setup and supported connection forms. Run its
> scripts/worksheet-extract.py with --job-number and --output, plus
> --pc-database-config when supplied. Preserve leading zeros in the job number.
> This script retrieves the complete saved XML for the most recent retained run;
> it does not generate worksheets or choose historical runs. Keep database access
> read-only. Do not print credentials or full worksheet contents.
>
> Inspect the exit code and JSON result. On exit 0 and status ok, return the
> absolute clickable file path, source server/database, job number and worksheet
> count. Preserve the file exactly as written. On failure, report the error
> category and the guide's remedy. Do not guess between multiple retained blobs,
> change PC data, or fabricate missing worksheets. Choose a new output
> path if one already exists unless I requested replacement. Keep ordinary local
> XML out of repository fixtures; only synthetic or non-sensitive examples belong
> there.
