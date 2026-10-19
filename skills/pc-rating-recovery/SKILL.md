---
name: pc-rating-recovery
description: Inspect a PC SQL Server job stuck with the rate/quote request being processed, and explicitly reset an orphaned Quoting period to Draft in non-production. Use for stale rating or quoting locks after interrupted development requests, not active requests, production, rerating, async queue repair, or general status resets.
---

# Recover a stale non-production quoting job

Read [README.md](README.md) for connection setup, outcomes and limitations.
If the user wants SQL to run themselves without pc-tools, supply the complete
[SQL Server batch](sql/rating-recovery.sql) and its README instructions. For Oracle,
supply the [standalone translation](sql/rating-recovery-oracle.sql) and explain its
unverified schema/time assumptions and concurrent-insert limitation. Do not claim
Oracle support for the Python command. Keep preview defaults; providing either
script is not authorization to execute it.

1. Reuse the user's authorized connection and exact job number, preserving leading
   zeros. Never guess a target database or expose credentials. Run the bundled
   `scripts/rating-recovery.py --job-number '<job>'` with
   `--pc-database-config <database-config.xml>` or the documented `PC_WS_*` variables.
   The default is inspection and rolls back without a write.
2. Report the selected period, status, BeanVersion, async work items and outcome.
   `eligible` means the database predicates match, not proof the job is idle.
   Do not clear other flags, repair queues, generate a quote, or rerate.
3. Apply only when the user has authorized the actual reset, confirms the target is
   non-production and the job has no ongoing rating/quoting/editing request. Creating
   this tool or investigating a job is not authorization to repair that job. Reuse
   explicit authorization already given; do not ask again unnecessarily.
4. For an authorized reset, add `--apply --acknowledge-non-production
   --expected-period-id <selected ID> --expected-bean-version <inspected version>`.
   Do not require or perform PC shutdown. Warn that the operator must keep
   this job idle and may need to reload it; SQL bypasses application callbacks/cache
   invalidation. The command refuses changed selection/version or any async work item.
5. Report `cleared` only when JSON says `committed: true`. `already-draft` is a no-op,
   not evidence of a previous repair by this tool. If `outcome-unknown` occurs, inspect
   again before considering another apply; never blindly retry. Do not treat a SQL
   success as proof that all application caches or pricing results are valid.
