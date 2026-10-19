#!/usr/bin/env python3
"""Inspect or explicitly reset one stale non-production PC 10.2.3 Quoting period."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


WARNING = ('NON-PRODUCTION ONLY. Do not apply while anyone or any worker is rating, '
           'quoting or editing this job. PC may remain running. Reload the job '
           'after applying; SQL does not perform application callbacks or cache invalidation.')


class RecoveryError(Exception):
    def __init__(self, kind, message, evidence=None, code=4):
        super().__init__(message)
        self.kind, self.evidence, self.code = kind, evidence or {}, code


def configuration(args, env):
    """Same literal JDBC / PC_WS_* conventions as worksheet extraction; secrets stay in memory."""
    try:
        if args.pc_database_config:
            root = ET.parse(args.pc_database_config).getroot()
            dbs = [e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'database'
                   and e.get('dbtype') == 'sqlserver']
            if len(dbs) != 1:
                raise ValueError()
            pools = [e for e in dbs[0] if e.tag.rsplit('}', 1)[-1] == 'dbcp-connection-pool']
            if len(pools) != 1:
                raise ValueError()
            url = pools[0].get('jdbc-url', '')
            match = re.fullmatch(r'jdbc:sqlserver://([^;:/\\{}\s]+)(?::([0-9]+))?;(.+)', url)
            if not match or any(x in url for x in ('{', '}', '${')):
                raise ValueError()
            props = {}
            for item in match[3].rstrip(';').split(';'):
                key, value = item.split('=', 1)
                key = key.lower()
                if key in props or key not in ('databasename', 'user', 'password'):
                    raise ValueError()
                props[key] = value
            result = dict(server=match[1], port=int(match[2] or 1433),
                          database=props['databasename'], user=props['user'], password=props['password'])
        else:
            result = dict(server=env['PC_WS_SERVER'], port=int(env.get('PC_WS_PORT', '1433')),
                          database=env['PC_WS_DATABASE'], user=env['PC_WS_USER'], password=env['PC_WS_PASSWORD'])
        if not all(result[k] for k in ('server', 'database', 'user', 'password')) or not 1 <= result['port'] <= 65535:
            raise ValueError()
        return result
    except (OSError, ET.ParseError, ValueError, KeyError):
        raise RecoveryError('configuration', 'Supply one literal SQL Server database-config.xml or '
                            'PC_WS_SERVER, PC_WS_DATABASE, PC_WS_USER, PC_WS_PASSWORD (optional PC_WS_PORT).', code=2) from None


PERIOD_FIELDS = '''p.ID AS period_id, p.PublicID AS public_id, p.JobID AS job_id,
 p.BranchNumber AS branch_number, p.TemporaryBranch AS temporary, p.Retired AS retired,
 p.Status AS status_id, s.TYPECODE AS status_code, p.BeanVersion AS bean_version,
 p.UpdateTime AS update_time, p.UpdateUserID AS update_user_id,
 p.EditLocked AS edit_locked, p.Locked AS locked, p.LockingColumn AS locking_column,
 p.QuoteMaturityLevel AS quote_maturity_level, p.QuoteHidden AS quote_hidden,
 p.TotalPremiumRPT AS total_premium, p.TotalCostRPT AS total_cost'''

# No user-supplied SQL identifiers. SERIALIZABLE holds identity/edge/work-item predicates
# until rollback/commit. UPDLOCK on the selected job's periods avoids lock conversion races.
JOBS = '''SELECT TOP (2) ID AS job_id, JobNumber AS job_number FROM dbo.pc_job
 WHERE JobNumber = %s AND Retired = 0 ORDER BY ID'''
EDGES = '''SELECT TOP (3) ForeignEntityID AS period_id FROM dbo.pc_jobpolicyperiod
 WHERE OwnerID = %s ORDER BY ID'''
PERIODS = 'SELECT TOP (101) ' + PERIOD_FIELDS + ''' FROM dbo.pc_policyperiod p {hint}
 LEFT JOIN dbo.pctl_policyperiodstatus s ON s.ID = p.Status WHERE p.JobID = %s ORDER BY p.ID'''
ITEMS = '''SELECT TOP (2) ID AS work_item_id, Status AS status_id, Subtype AS subtype_id
 FROM dbo.pc_basequotingworkitem WHERE PolicyPeriodID = %s ORDER BY ID'''
TYPES = '''SELECT ID AS status_id, TYPECODE AS status_code FROM dbo.pctl_policyperiodstatus
 WHERE TYPECODE IN ('Quoting', 'Draft')'''
TRIGGERS = '''SELECT COUNT(*) AS enabled_triggers FROM sys.triggers
 WHERE parent_id = OBJECT_ID('dbo.pc_policyperiod') AND is_disabled = 0'''
UPDATE = '''UPDATE dbo.pc_policyperiod SET Status = %s, BeanVersion = BeanVersion + 1,
 UpdateTime = SYSUTCDATETIME()
 OUTPUT inserted.ID AS period_id, deleted.Status AS old_status_id,
 inserted.Status AS status_id, deleted.BeanVersion AS old_bean_version,
 inserted.BeanVersion AS bean_version
 WHERE ID = %s AND JobID = %s AND Status = %s AND BeanVersion = %s
 AND Retired = 0 AND TemporaryBranch = 0
 AND EXISTS (SELECT 1 FROM dbo.pc_jobpolicyperiod e WHERE e.OwnerID = %s AND e.ForeignEntityID = %s)
 AND NOT EXISTS (SELECT 1 FROM dbo.pc_basequotingworkitem w WHERE w.PolicyPeriodID = %s)'''


def rows(cursor, sql, params=()):
    if params:
        cursor.execute(sql, params)
    else:
        cursor.execute(sql)
    return cursor.fetchall()


def snapshot(cursor, job_number, apply):
    jobs = rows(cursor, JOBS, (job_number,))
    if not jobs or any(j['job_number'] != job_number for j in jobs):
        raise RecoveryError('unknown-job', 'No exact active job-number match; preserve leading zeros.')
    if len(jobs) != 1:
        raise RecoveryError('ambiguous-job', 'Multiple active jobs match; no job selected.')
    job = jobs[0]
    edges = rows(cursor, EDGES, (job['job_id'],))
    periods = rows(cursor, PERIODS.format(hint='WITH (UPDLOCK)' if apply else ''), (job['job_id'],))
    if len(periods) > 100:
        raise RecoveryError('too-many-periods', 'More than 100 periods; manual investigation required.')
    selected = [p for p in periods if len(edges) == 1 and p['period_id'] == edges[0]['period_id']]
    evidence = dict(job=job, selected_edges=edges, periods=periods)
    if len(edges) != 1 or len(selected) != 1:
        raise RecoveryError('ambiguous-period', 'Require exactly one selected-version edge to a period owned by this job.', evidence)
    period = selected[0]
    if period['retired'] != 0 or period['temporary'] != False:
        raise RecoveryError('ineligible-period', 'Selected period must be active and non-temporary.', evidence)
    if not isinstance(period['bean_version'], int) or not 0 <= period['bean_version'] <= 2147483647:
        raise RecoveryError('schema', 'Selected period has an unsupported BeanVersion.', evidence)
    type_rows = rows(cursor, TYPES)
    types = {r['status_code']: r['status_id'] for r in type_rows}
    if len(type_rows) != 2 or set(types) != {'Quoting', 'Draft'} or len(set(types.values())) != 2:
        raise RecoveryError('schema', 'Require unique exact Quoting and Draft typecodes.', evidence)
    items = rows(cursor, ITEMS, (period['period_id'],))
    evidence.update(before=period, work_items=items, typecodes=types)
    # TOP (2) is enough to refuse any item, irrespective of subtype, state or attempts.
    if items:
        outcome = 'blocked-work-items'
    elif period['status_id'] == types['Quoting'] and period['status_code'] == 'Quoting':
        outcome = 'eligible'
    elif period['status_id'] == types['Draft'] and period['status_code'] == 'Draft':
        outcome = 'already-draft'
    else:
        outcome = 'not-applicable'
    evidence['outcome'] = outcome
    return evidence


def recover(connection, args):
    """One short transaction; no commits at all in inspection or no-op paths."""
    evidence = {}
    commit_attempted = False
    try:
        cursor = connection.cursor(as_dict=True)
        cursor.execute('SET XACT_ABORT ON')
        cursor.execute('SET LOCK_TIMEOUT 3000')
        cursor.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        evidence = snapshot(cursor, args.job_number, args.apply)
        evidence.update(mode='apply' if args.apply else 'inspect', changed=False, committed=False)
        if not args.apply:
            connection.rollback()
            return evidence
        before = evidence['before']
        if before['period_id'] != args.expected_period_id:
            raise RecoveryError('stale-selection', 'Selected period differs from --expected-period-id.', evidence)
        if evidence['outcome'] == 'already-draft':
            # Idempotent no-op, not a claim that this tool performed an earlier repair.
            connection.rollback()
            evidence['after'] = dict(before)
            return evidence
        if evidence['outcome'] != 'eligible':
            raise RecoveryError(evidence['outcome'], 'Only Quoting with no async work item can be reset.', evidence)
        if before['bean_version'] == 2147483647:
            raise RecoveryError('schema', 'BeanVersion cannot be incremented safely.', evidence)
        if before['bean_version'] != args.expected_bean_version:
            raise RecoveryError('stale-version', 'BeanVersion changed; inspect again.', evidence)
        if rows(cursor, TRIGGERS)[0]['enabled_triggers']:
            raise RecoveryError('schema', 'Enabled PolicyPeriod SQL trigger detected; automatic reset refused.', evidence)
        t = evidence['typecodes']
        result = rows(cursor, UPDATE, (t['Draft'], before['period_id'], before['job_id'], t['Quoting'],
                      before['bean_version'], before['job_id'], before['period_id'], before['period_id']))
        expected = dict(period_id=before['period_id'], old_status_id=t['Quoting'], status_id=t['Draft'],
                        old_bean_version=before['bean_version'], bean_version=before['bean_version'] + 1)
        if result != [expected]:
            raise RecoveryError('conditional-update', 'Expected exactly one matching updated row; rolling back.', evidence)
        after_snapshot = snapshot(cursor, args.job_number, True)
        after = after_snapshot['before']
        unchanged = set(before) - {'status_id', 'status_code', 'bean_version', 'update_time'}
        if (after_snapshot['outcome'] != 'already-draft' or after['bean_version'] != expected['bean_version']
                or any(before[k] != after[k] for k in unchanged)):
            raise RecoveryError('verification', 'Post-update evidence differs from the narrow expected change.', evidence)
        evidence['after'] = after
        commit_attempted = True
        connection.commit()
        evidence.update(outcome='cleared', changed=True, committed=True)
        return evidence
    except Exception as error:
        try:
            connection.rollback()
            rolled_back = True
        except Exception:
            rolled_back = False
        if commit_attempted:
            # A failed commit acknowledgment cannot establish whether the server committed.
            evidence.update(committed=None, changed=None)
            raise RecoveryError('outcome-unknown', 'Commit was attempted but not confirmed. Reinspect; do not blindly retry.', evidence, 5) from None
        evidence.update(committed=False, rollback_confirmed=rolled_back)
        if isinstance(error, RecoveryError):
            error.evidence.update(evidence)
            raise
        raise RecoveryError('database', 'Database read/update failed; no commit was requested. Check connection, '
                            'schema, permissions and lock timeout. Driver details are suppressed.', evidence, 5) from None


def integer(value):
    n = int(value)
    if not 0 <= n <= 2147483646:
        raise argparse.ArgumentTypeError('must be between 0 and 2147483646')
    return n


def parser():
    p = argparse.ArgumentParser(description=__doc__, epilog=WARNING)
    p.add_argument('--job-number', required=True, help='Exact string; preserve leading zeros.')
    p.add_argument('--pc-database-config', type=Path, help='Literal SQL Server JDBC config; otherwise PC_WS_* variables.')
    p.add_argument('--apply', action='store_true', help='Commit the narrowly guarded reset (default: inspect only).')
    p.add_argument('--acknowledge-non-production', action='store_true', help='Acknowledge target is non-production and this job is idle.')
    p.add_argument('--expected-period-id', type=int, help='Selected period ID from inspection, required for apply.')
    p.add_argument('--expected-bean-version', type=integer, help='BeanVersion from inspection, required for apply.')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    connection = None
    try:
        if not args.job_number or args.job_number.strip() != args.job_number:
            raise RecoveryError('arguments', 'Supply a nonempty exact job number without surrounding whitespace.', code=2)
        if args.apply and (not args.acknowledge_non_production or args.expected_period_id is None
                           or args.expected_period_id <= 0 or args.expected_bean_version is None):
            raise RecoveryError('arguments', 'Apply requires --acknowledge-non-production, --expected-period-id '
                                'and --expected-bean-version from inspection.', code=2)
        config = configuration(args, os.environ)
        try:
            import pymssql
        except ImportError:
            raise RecoveryError('dependency', 'Install this bundle\'s requirements.txt into your Python environment.', code=2) from None
        try:
            connection = pymssql.connect(**config, read_only=not args.apply, autocommit=False,
                                         login_timeout=5, timeout=10, tds_version='7.4', charset='UTF-8',
                                         appname='pc-tools rating recovery')
        except pymssql.Error:
            raise RecoveryError('database', 'Cannot connect to the configured SQL Server; connection details suppressed.', code=5) from None
        result = recover(connection, args)
        print(json.dumps(dict(status='ok', warning=WARNING,
                              source={k: config[k] for k in ('server', 'port', 'database')}, **result),
                         default=str, sort_keys=True))
        return 0
    except RecoveryError as error:
        print(json.dumps(dict(status='error', error=error.kind, message=str(error), warning=WARNING,
                              **error.evidence), default=str, sort_keys=True), file=sys.stderr)
        return error.code
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass  # Do not turn a confirmed commit into an apparent failure.


if __name__ == '__main__':
    sys.exit(main())
