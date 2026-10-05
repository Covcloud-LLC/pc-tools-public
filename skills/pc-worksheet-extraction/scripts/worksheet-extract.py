#!/usr/bin/env python3
"""Read retained PC 10.2.3 SQL Server worksheet XML without rewriting it."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from xml.parsers import expat
import zlib


class ExtractionError(Exception):
    def __init__(self, code, kind, message):
        super().__init__(message)
        self.code, self.kind = code, kind


# One statement reads job identity and every active retained blob; no dirty reads,
# timestamps masquerading as rating-run IDs, TOP 1, or text conversion of binary data.
QUERY = """
SELECT DB_NAME() AS source_database, j.ID AS job_id, j.JobNumber AS job_number,
       p.ID AS period_id, p.BranchNumber AS branch_number,
       w.ID AS container_id, d.ID AS data_id, d.Data AS payload,
       DATALENGTH(d.Data) AS stored_bytes,
       HASHBYTES('SHA2_256', d.Data) AS stored_sha256
FROM dbo.pc_job AS j
LEFT JOIN dbo.pc_policyperiod AS p ON p.JobID = j.ID AND p.Retired = 0
LEFT JOIN dbo.pc_worksheetcontainer AS w ON w.Branch = p.ID AND w.Retired = 0
LEFT JOIN dbo.pc_worksheetdata AS d ON d.WorksheetContainer = w.ID AND d.Retired = 0
WHERE j.JobNumber = %s AND j.Retired = 0
ORDER BY p.ID, w.ID, d.ID
"""


def configuration(args, env):
    """Read secrets in memory; never put the JDBC URL in errors or process arguments."""
    if args.pc_database_config:
        try:
            root = ET.parse(args.pc_database_config).getroot()
            databases = [e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'database'
                         and e.get('dbtype') == 'sqlserver']
            if len(databases) != 1:
                raise ValueError()
            pools = [e for e in databases[0] if e.tag.rsplit('}', 1)[-1] == 'dbcp-connection-pool']
            if len(pools) != 1:
                raise ValueError()
            url = pools[0].get('jdbc-url', '')
            # Deliberately support only the documented literal local JDBC form.
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
            config = dict(server=match[1], port=int(match[2] or 1433),
                          database=props['databasename'], user=props['user'], password=props['password'])
        except (OSError, ET.ParseError, ValueError, KeyError):
            raise ExtractionError(2, 'configuration', 'Cannot read a single literal SQL Server JDBC configuration. See README; use PC_WS_* environment variables for other connection forms.') from None
    else:
        try:
            config = dict(server=env['PC_WS_SERVER'], port=int(env.get('PC_WS_PORT', '1433')),
                          database=env['PC_WS_DATABASE'], user=env['PC_WS_USER'], password=env['PC_WS_PASSWORD'])
        except (KeyError, ValueError):
            raise ExtractionError(2, 'configuration', 'Set PC_WS_SERVER, PC_WS_DATABASE, PC_WS_USER and PC_WS_PASSWORD (optional PC_WS_PORT), or supply --pc-database-config.') from None
    if not all(config[k] for k in ('server', 'database', 'user', 'password')) or not 1 <= config['port'] <= 65535:
        raise ExtractionError(2, 'configuration', 'Connection fields must be nonempty and the port must be between 1 and 65535.')
    return config


def fetch(config, timeout):
    try:
        import pymssql
    except ImportError:
        raise ExtractionError(2, 'dependency', 'Install the bundled requirements.txt into your Python environment.') from None
    def retrieve(job):
        try:
            with pymssql.connect(**config, login_timeout=timeout, timeout=timeout,
                                 appname='pc-tools worksheet extraction', read_only=True,
                                 tds_version='7.4', charset='UTF-8', autocommit=False) as connection:
                with connection.cursor(as_dict=True) as cursor:
                    # Session-only settings. SERIALIZABLE gives a consistent read while
                    # rating may replace a blob; closing rolls back and releases locks.
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
                    cursor.execute('SET TEXTSIZE 2147483647')
                    cursor.execute(QUERY, (job,))
                    return cursor.fetchall()
        except pymssql.Error:
            # Driver messages can include connection details. Do not echo them.
            raise ExtractionError(5, 'database', 'SQL Server connection or read failed. Check availability, credentials, SELECT permissions, PC 10.2.3 schema and timeout; no output was written.') from None
    return retrieve


def decode(rows, requested_job):
    if not rows:
        raise ExtractionError(3, 'unknown-job', 'No active job matches the supplied job number.')
    if len({r['job_id'] for r in rows}) != 1:
        raise ExtractionError(8, 'ambiguous-job', 'Multiple active jobs match; no job was selected.')
    # SQL Server collations may be case-insensitive or ignore trailing spaces.
    if any(r['job_number'] != requested_job for r in rows):
        raise ExtractionError(3, 'unknown-job', 'No exact active job-number match; preserve spelling and leading zeros.')
    payloads = [r for r in rows if r['payload'] is not None]
    if not payloads:
        raise ExtractionError(4, 'no-worksheets', 'The job exists but has no saved worksheet XML; data may be absent or purged.')
    if len(payloads) != 1:
        raise ExtractionError(8, 'ambiguous-worksheets', 'Multiple retained worksheet blobs match this job. No quote version or rating run was guessed; extraction is unsupported for this job.')
    row = payloads[0]
    packed = bytes(row['payload'])
    if len(packed) != row['stored_bytes'] or hashlib.sha256(packed).digest() != bytes(row['stored_sha256']):
        raise ExtractionError(7, 'invalid-content', 'Retrieved binary data does not match SQL Server length/hash; no output was written.')
    try:
        xml = gzip.decompress(packed)
        def reject_doctype(*unused):
            raise ValueError('DTD is not worksheet content')
        validator = expat.ParserCreate()
        validator.StartDoctypeDeclHandler = reject_doctype
        validator.Parse(xml, True)
        root = ET.fromstring(xml)
        if root.tag != 'Worksheets' or any(e.tag != 'Worksheet' for e in root):
            raise ValueError()
    except (OSError, EOFError, zlib.error, expat.ExpatError, ET.ParseError, ValueError):
        raise ExtractionError(7, 'invalid-content', 'Stored data is not a valid GZIP Worksheets XML document; no output was written.') from None
    if len(root) == 0:
        raise ExtractionError(4, 'no-worksheets', 'The job has an empty Worksheets document; no output was written.')
    return xml, dict(source_database=row['source_database'], job_number=row['job_number'],
                     policy_period_id=row['period_id'], branch_number=row['branch_number'],
                     worksheet_container_id=row['container_id'], worksheet_data_id=row['data_id'],
                     worksheet_count=len(root), xml_bytes=len(xml),
                     xml_sha256=hashlib.sha256(xml).hexdigest(),
                     stored_gzip_bytes=len(packed), stored_gzip_sha256=hashlib.sha256(packed).hexdigest())


def write_output(path, xml, overwrite=False):
    temporary = None
    try:
        # Do not follow an existing destination symlink. Replacement replaces the
        # directory entry, and exclusive publication refuses any existing entry.
        target = Path(os.path.abspath(os.path.expanduser(str(path))))
        with tempfile.NamedTemporaryFile(prefix='.worksheet-', suffix='.tmp', dir=str(target.parent), delete=False) as handle:
            temporary = handle.name
            handle.write(xml)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
        return str(target)
    except (OSError, ValueError):
        raise ExtractionError(6, 'output-write', 'Cannot publish the XML file. Check the parent directory, permissions, free space and existing destination; use --overwrite only to replace an intended file.') from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-number', required=True, help='Exact job number; retain leading zeros.')
    parser.add_argument('--output', required=True, type=Path, help='Destination XML file; parent must exist.')
    parser.add_argument('--pc-database-config', type=Path, help='Optional PC database-config.xml; otherwise use PC_WS_* environment variables.')
    parser.add_argument('--timeout', type=positive, default=30, help='Login and query timeout in seconds (default 30).')
    parser.add_argument('--overwrite', action='store_true', help='Atomically replace an existing output file.')
    args = parser.parse_args(argv)
    try:
        if not args.job_number or args.job_number != args.job_number.strip():
            raise ExtractionError(2, 'configuration', 'Supply a nonempty job number without surrounding whitespace.')
        config = configuration(args, os.environ)
        rows = fetch(config, args.timeout)(args.job_number)
        xml, result = decode(rows, args.job_number)
        result['output'] = write_output(args.output, xml, args.overwrite)
        result['source_server'] = config['server']
        result['source_port'] = config['port']
        print(json.dumps(dict(status='ok', **result), sort_keys=True))
        return 0
    except ExtractionError as error:
        print(json.dumps(dict(status='error', error=error.kind, message=str(error))), file=sys.stderr)
        return error.code


if __name__ == '__main__':
    sys.exit(main())
