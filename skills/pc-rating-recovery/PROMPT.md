# Inspect or recover one non-production quoting job

- Exact job number, retaining leading zeros: `<job>`
- Bundle directory: `<pc-rating-recovery>`
- Connection: `<database-config.xml>`, or explicitly configured `PC_WS_*` variables
- Python with the bundle's requirements installed: `<python>`
- Requested action: `inspect` by default; `apply` only if I explicitly authorize a reset

Read the bundle's README.md and SKILL.md. Run its `scripts/rating-recovery.py`
with `--job-number '<job>'` and the supplied connection, initially without `--apply`.
Report selected period, status, BeanVersion, work items and outcome. Do not expose
credentials or use a different database.

For an explicitly authorized apply, confirm that the target is non-production and
that no rating/quoting/editing request is running for this job. Reuse confirmations
already given. Add `--apply --acknowledge-non-production --expected-period-id <ID>
--expected-bean-version <version>` using the inspection result. PC may
remain running. Do not stop services, rerate, or change other state.

Report before/after JSON and whether commit was confirmed. Explain that the operator
may need to reload the job and SQL does not reproduce application callbacks/cache
invalidation. Treat already-Draft as a no-op. Reinspect an uncertain commit; never
automatically retry it. Building or inspecting this tool does not authorize a live repair.

If I request standalone SQL instead, supply the complete `sql/rating-recovery.sql`
for SQL Server or `sql/rating-recovery-oracle.sql` for Oracle, with preview defaults.
Read the README's engine-specific instructions. Oracle is an unexecuted translation,
not Python runtime support; explain schema/time assumptions and the concurrent-insert
limitation. Do not execute a supplied script unless I authorize that separately.
