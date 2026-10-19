# Recover a stale quoting job

This small SQL Server command clears a persisted `Quoting` status left behind when
a non-production PC rating/quote request is interrupted. It selects the
job's actual selected period and changes it to `Draft`. Inspection is the default.

**Non-production only. Do not apply while anyone or any worker is rating, quoting,
or editing this job. PC may remain running.** Absence of an async work item
does not prove that a synchronous request is idle. The acknowledgment is the
operator's assertion, not automatic environment or thread detection.

## Setup and inspection

Use Python 3.8+ with the bundle's driver installed:

```bash
python3 -m venv /tmp/pc-recovery-venv
/tmp/pc-recovery-venv/bin/python -m pip install -r /path/to/pc-rating-recovery/requirements.txt
```

Supply the existing PC SQL Server configuration explicitly:

```bash
/tmp/pc-recovery-venv/bin/python /path/to/pc-rating-recovery/scripts/rating-recovery.py \
  --job-number '0000000042' \
  --pc-database-config /path/to/pc/modules/configuration/config/database-config.xml
```

This sends SELECTs/session settings, returns JSON, and rolls back; it never calls
commit. `read_only=True` is connection intent, not a SQL permission boundary.
For stricter inspection access, use a SELECT-only database account.

The XML reader accepts one literal SQL Server JDBC pool with `databaseName`, `user`
and `password`. Alternatively omit the flag and provide `PC_WS_SERVER`,
`PC_WS_DATABASE`, `PC_WS_USER`, `PC_WS_PASSWORD`, and optionally `PC_WS_PORT` (1433).
The names match worksheet extraction. Credentials are never printed. Connection
targets are explicit; the tool does not discover or certify non-production servers.

The supported initial target is the PC 10.2.3 `dbo` schema on SQL Server, exercised
with SQL Server 2022. It uses `pc_job`, `pc_jobpolicyperiod`, `pc_policyperiod`,
`pctl_policyperiodstatus` and `pc_basequotingworkitem`. Missing/incompatible required
columns or typecodes fail without a commit. This is not automatic certification
of arbitrary customized schemas or other PC releases. No PC checkout is needed
at runtime unless it supplies the optional database configuration input.

## Explicit apply

Inspect first. After confirming the job is idle, supply the returned selected
period ID and BeanVersion. This example uses synthetic IDs, not a live repair:

```bash
/tmp/pc-recovery-venv/bin/python /path/to/pc-rating-recovery/scripts/rating-recovery.py \
  --job-number '0000000042' --pc-database-config /path/to/database-config.xml \
  --apply --acknowledge-non-production \
  --expected-period-id 73 --expected-bean-version 4
```

The command requires exactly one exact active job and one selected-version edge to
an active, non-temporary period belonging to that job. Other quote versions may
exist; it never picks the newest ID or timestamp. It refuses more than 100 periods,
ambiguous selection, any async work item for the selected period (even failed), and
statuses other than `Quoting`. `Draft` is a no-op. `RateRequested` and `QuoteRequested`
need separate queue investigation.

Apply uses one SERIALIZABLE transaction, update locks on the job's period rows,
a three-second lock timeout and ten-second query timeout. It rechecks selection,
status, version and work-item absence, resolves status IDs by typecode, and requires
exactly one matching UPDATE result. Enabled SQL triggers on `pc_policyperiod` cause
refusal. The account needs SELECT on the named tables, UPDATE on the period, and
visibility of its trigger metadata.

Only three columns are assigned:

- `Status`: `Quoting` to `Draft`.
- `BeanVersion`: increment by one, so ordinary optimistic-lock writes based on the
  old version conflict instead of silently treating it as current.
- `UpdateTime`: SQL Server UTC time.

The tool preserves `UpdateUserID` because a SQL login is not a PC user;
it does not impersonate one. Save the JSON as the operator's repair record. Costs,
transactions, worksheets, totals, maturity and edit/branch lock flags are not
updated. A successful reset is not a successful rating or a restoration of results.

Direct SQL does **not** duplicate `unlockFromQuoting()` plus bundle commit: it skips
application permissions, pre-update rules, callbacks and cache notifications.
Reload the job after applying and confirm the alert is gone before resuming work.
A normal browser reload is not proven to invalidate every server cache. If the UI
still shows old state or reports a conflict, discard stale edits and investigate
the reload path; do not repeatedly reset statuses or perform a global cache flush.

## Results and errors

JSON includes job identity, period candidates, selected edge, `before`, typecode IDs,
up to two work-item examples, mode, outcome and commit/change flags. Apply adds
`after`, captured inside the transaction immediately before commit. It is not a
claim that the database cannot change later. Exact timestamps/decimals serialize
as strings. Success JSON is on stdout; errors are on stderr.

| Outcome | Meaning |
| --- | --- |
| `eligible` | Inspection found `Quoting` without a work item; operator must establish idleness. |
| `blocked-work-items` / `not-applicable` | Inspection completed but reset is ineligible. Apply refuses. |
| `cleared` | Exactly one period updated and commit acknowledged. |
| `already-draft` | No write or commit; does not attribute the existing Draft state to this tool. |
| `stale-selection` / `stale-version` | Evidence changed; inspect again rather than bypassing the guard. |
| `conditional-update` / `verification` | Expected write/result did not match; rollback attempted. |
| `outcome-unknown` | Commit acknowledgment failed; inspect before any retry. `after` is only the attempted state. |

Exit 0 means inspection/no-op/apply completed; read `outcome` and `committed` to
distinguish them. Exit 2 means arguments/configuration/dependency; 4 means refusal;
5 means database failure or uncertain commit. Argument-parser usage errors use
standard stderr text. Other errors are JSON and suppress raw driver messages.
Pre-commit failures include `rollback_confirmed`; a false value requires inspection.
Repeating apply on a now-Draft period does not increment the version again, even
if the prior expected version is supplied. There is no automatic retry.

## Without pc-tools or Python

Open [sql/rating-recovery.sql](sql/rating-recovery.sql) in a SQL Server query client
connected explicitly to the intended non-production PC database. It is a complete
standalone batch with the same identity, selected-period, work-item, typecode,
expected-version and exactly-one-row checks. Its preview takes update locks too;
run it in a dedicated query window with no existing transaction.

1. Set `@JobNumber`, retaining leading zeros. Leave `@Apply = 0`. Execute the whole
   batch: it SELECTs the candidates, work items and before evidence, then rolls back.
2. Review that evidence and establish the job is idle. Set the expected period and
   BeanVersion variables from preview, `@AcknowledgeNonProductionAndIdleJob = 1`,
   and `@Apply = 1`. Execute the whole batch again.
3. The batch commits automatically only after all checks pass; otherwise it rolls
   back and throws. **Do not add another COMMIT or run an isolated UPDATE fragment.**
   Read the final outcome/commit result and reload the job. After a connection loss,
   run preview again before considering a retry.

The standalone batch returns SQL result sets and before/after JSON columns rather
than the CLI's JSON envelope. It uses the same three-column update and Draft no-op.
Its lock timeout is three seconds; set a query timeout in your client if desired.
It does not configure a client query timeout. The script is reviewed with synthetic
checks and is not executed against a database as part of this delivery.

## Oracle standalone translation

[sql/rating-recovery-oracle.sql](sql/rating-recovery-oracle.sql) is an **unexecuted,
uncompiled Oracle PL/SQL translation**. The Python command supports SQL Server only.
Use the maintained complete scripts here, not shortened chat snippets: the SQL Server
batch includes full before/after JSON and protected-field verification, while Oracle
uses DBMS_OUTPUT and its own transaction/locking semantics. Neither is a tested live
repair path. These are original recovery scripts, not copied application source.

Before using Oracle, verify the actual tables, selected-version edge, columns and
typecodes. It assumes the same PC 10.2.3 logical mapping, numeric `Retired` and
`TemporaryBranch` flags, a nonnegative 32-bit integer BeanVersion, and a compatible
UpdateTime column. Run as the table owner in a new, clean session, without synonym
redirects. The block refuses an existing transaction before entering its rollback
handler. Required package access includes DBMS_TRANSACTION and DBMS_OUTPUT.

Keep `v_apply = 0` for preview. Copy its period/version to the expected variables;
an authorized apply also requires `v_apply = 1` and `v_ack_dev_idle = 1`. Any work
item blocks apply; already Draft is a no-op. The block changes the same three columns
and verifies protected period fields before commit. DBMS_OUTPUT prints before and
verified-after evidence, followed by a commit acknowledgment; save that output.

Oracle SERIALIZABLE uses a consistent snapshot, not SQL Server's predicate-range
locking. Locking the existing job, selected edge and period does not prevent all
concurrent async-item or additional-edge insertions. Such inserts can escape the
absence checks. **The operator must keep all work for this job idle throughout.**
`FOR UPDATE WAIT 3` bounds ordinary row-lock waits, not exclusive table-lock waits or the whole block; set a client
query timeout if needed. Serialization/lock errors roll back, with no automatic retry.
See Oracle's [transaction consistency documentation](https://docs.oracle.com/en/database/oracle/oracle-database/19/cncpt/data-concurrency-and-consistency.html).

UpdateTime uses `CAST(SYS_EXTRACT_UTC(SYSTIMESTAMP) AS TIMESTAMP)`: UTC without a
timezone component. Confirm the installation's application convention and column
type before apply; a DATE column loses fractional seconds, and timezone-bearing
columns require an explicit reviewed adaptation. This choice is a translation of
the SQL Server script, not evidence of the installation's expected time convention.
See Oracle's [SYS_EXTRACT_UTC reference](https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/SYS_EXTRACT_UTC.html).

Execute the entire block. SQL*Plus, SQLcl and SQL Developer Run Script accept the
leading `SET SERVEROUTPUT ON` and trailing `/`. Other clients generally require
omitting those client directives and enabling DBMS_OUTPUT separately. Never append
COMMIT or run only the UPDATE. An uncertain commit acknowledgment requires a new
preview before considering retry. Application callbacks/cache visibility remain
outside both scripts; reload and inspect before resuming work.
