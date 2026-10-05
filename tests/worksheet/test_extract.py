"""Synthetic offline fixtures only; no database or driver installation needed."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'skills/pc-worksheet-extraction/scripts/worksheet-extract.py'
spec = importlib.util.spec_from_file_location('worksheet_extract', SCRIPT)
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)
XML = b'<?xml version="1.0"?>\r\n<Worksheets><!--keep--><Worksheet z="001.2300" a="&amp;"><Value>  synthetic  </Value></Worksheet><Worksheet/></Worksheets>\r\n'


def row(xml=XML, **changes):
    packed = gzip.compress(xml)
    value = dict(source_database='SyntheticDB', job_id=1, job_number='0001', period_id=2,
                 branch_number=1, container_id=3, data_id=4, payload=packed,
                 stored_bytes=len(packed), stored_sha256=hashlib.sha256(packed).digest())
    value.update(changes)
    return value


class ExtractTests(unittest.TestCase):
    def fails(self, code, callback, *args):
        with self.assertRaises(ws.ExtractionError) as caught:
            callback(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_exact_bytes_order_whitespace_comments_escapes(self):
        data, result = ws.decode([row()], '0001')
        self.assertEqual(data, XML)
        self.assertEqual(result['worksheet_count'], 2)
        self.assertEqual(result['xml_sha256'], hashlib.sha256(XML).hexdigest())

    def test_large_unicode_and_utf16_are_preserved(self):
        for encoding in ('utf-8', 'utf-16'):
            data = ('<?xml version="1.0" encoding="%s"?><Worksheets><Worksheet>%s</Worksheet></Worksheets>' % (encoding, '雪 café\r\n' * 20000)).encode(encoding)
            self.assertEqual(ws.decode([row(data)], '0001')[0], data)

    def test_unknown_and_inexact_collation_match(self):
        self.fails(3, ws.decode, [], '0001')
        self.fails(3, ws.decode, [row()], '0001 ')

    def test_null_and_empty_document_are_no_worksheets(self):
        self.fails(4, ws.decode, [row(payload=None)], '0001')
        self.fails(4, ws.decode, [row(b'<Worksheets/>')], '0001')

    def test_multiple_jobs_or_blobs_never_silently_select(self):
        self.fails(8, ws.decode, [row(), row(job_id=8)], '0001')
        self.fails(8, ws.decode, [row(), row(data_id=9, period_id=10)], '0001')
        self.assertEqual(ws.decode([row(payload=None), row()], '0001')[0], XML)

    def test_truncation_and_hash_mismatch(self):
        self.fails(7, ws.decode, [row(stored_bytes=9)], '0001')
        self.fails(7, ws.decode, [row(stored_sha256=b'bad')], '0001')

    def test_corrupt_gzip_and_crc(self):
        for packed in (b'not gzip', gzip.compress(XML)[:-1], gzip.compress(XML)[:-8] + b'12345678'):
            self.fails(7, ws.decode, [row(payload=packed, stored_bytes=len(packed), stored_sha256=hashlib.sha256(packed).digest())], '0001')

    def test_invalid_xml_root_children_and_doctype(self):
        for data in (b'<Worksheets>', b'<Other/>', b'<Worksheets><Other/></Worksheets>',
                     b'<!DOCTYPE Worksheets [<!ENTITY a "test">]><Worksheets><Worksheet>&a;</Worksheet></Worksheets>',
                     '<!DOCTYPE Worksheets><Worksheets><Worksheet/></Worksheets>'.encode('utf-16')):
            self.fails(7, ws.decode, [row(data)], '0001')

    def test_atomic_exclusive_and_explicit_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'result.xml'
            self.assertEqual(ws.write_output(target, XML), str(target))
            self.assertEqual(target.read_bytes(), XML)
            self.fails(6, ws.write_output, target, b'changed')
            self.assertEqual(target.read_bytes(), XML)
            ws.write_output(target, b'changed', True)
            self.assertEqual(target.read_bytes(), b'changed')
            self.assertEqual(list(Path(d).iterdir()), [target])
            if os.name == 'posix':
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_cleanup_failure_after_publication_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            target, warnings = Path(d) / 'result.xml', []
            with patch.object(ws.os, 'unlink', side_effect=PermissionError(13, 'Permission denied')):
                self.assertEqual(ws.write_output(target, XML, warnings=warnings), str(target))
            self.assertEqual(target.read_bytes(), XML)
            residual = [p for p in Path(d).iterdir() if p != target]
            self.assertEqual(len(residual), 1)
            self.assertEqual(warnings, ['Could not remove temporary file {}: Permission denied'.format(residual[0])])

    def test_output_missing_parent_directory_and_write_failure(self):
        with tempfile.TemporaryDirectory() as d:
            self.fails(6, ws.write_output, Path(d) / 'missing' / 'x.xml', XML)
            self.fails(6, ws.write_output, Path(d), XML)
            with patch.object(ws.os, 'fsync', side_effect=OSError('disk full')):
                self.fails(6, ws.write_output, Path(d) / 'x.xml', XML)
            self.assertEqual(list(Path(d).iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink behavior')
    def test_symlink_target_not_modified(self):
        with tempfile.TemporaryDirectory() as d:
            target, link = Path(d) / 'original', Path(d) / 'link'
            target.write_bytes(b'original'); link.symlink_to(target)
            self.fails(6, ws.write_output, link, XML)
            ws.write_output(link, XML, True)
            self.assertEqual(target.read_bytes(), b'original')
            self.assertFalse(link.is_symlink())
            self.assertEqual(link.read_bytes(), XML)

    def test_environment_and_jdbc_configuration_without_secret_logging(self):
        env = dict(PC_WS_SERVER='localhost', PC_WS_DATABASE='SyntheticDB', PC_WS_USER='test', PC_WS_PASSWORD='secret')
        self.assertEqual(ws.configuration(argparse.Namespace(pc_database_config=None), env)['port'], 1433)
        self.fails(2, ws.configuration, argparse.Namespace(pc_database_config=None), {})
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / 'db.xml'
            config.write_text('<database-config><database dbtype="h2"/><database dbtype="sqlserver"><dbcp-connection-pool jdbc-url="jdbc:sqlserver://localhost:1433;DatabaseName=SyntheticDB;User=test;Password=secret"/></database></database-config>')
            self.assertEqual(ws.configuration(argparse.Namespace(pc_database_config=config), {})['password'], 'secret')
            config.write_text(config.read_text().replace('Password=secret', 'Password=secret;encrypt=true'))
            error = self.fails(2, ws.configuration, argparse.Namespace(pc_database_config=config), {})
            self.assertNotIn('secret', str(error))

    def test_cli_success_and_failures_have_separate_channels(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'x.xml'
            with patch.object(ws, 'configuration', return_value=dict(server='localhost', port=1433)), patch.object(ws, 'fetch', return_value=lambda job: [row()]):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = ws.main(['--job-number', '0001', '--output', str(output)])
                self.assertEqual(code, 0); self.assertEqual(stderr.getvalue(), '')
                self.assertEqual(json.loads(stdout.getvalue())['output'], str(output))
            for code, kind in ((3, 'unknown-job'), (4, 'no-worksheets'), (5, 'database')):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(ws, 'configuration', return_value={}), patch.object(ws, 'fetch', side_effect=ws.ExtractionError(code, kind, 'test failure')), redirect_stdout(stdout), redirect_stderr(stderr):
                    result = ws.main(['--job-number', '0001', '--output', str(output)])
                self.assertEqual(result, code); self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(json.loads(stderr.getvalue())['error'], kind)
                self.assertEqual(output.read_bytes(), XML)

    def test_parameter_binding_read_intent_and_driver_errors(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        class DriverError(Exception):
            pass
        driver = SimpleNamespace(Error=DriverError, connect=MagicMock())
        connection = driver.connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        supplied = "0001' OR 1=1 --"
        with patch.dict('sys.modules', pymssql=driver):
            self.assertEqual(ws.fetch({}, 30)(supplied), [])
            self.assertEqual(cursor.execute.call_args.args, (ws.QUERY, (supplied,)))
            self.assertTrue(driver.connect.call_args.kwargs['read_only'])
            connection.commit.assert_not_called()
            driver.connect.side_effect = DriverError('password=secret')
            error = self.fails(5, ws.fetch({}, 30), '0001')
            self.assertNotIn('secret', str(error))


if __name__ == '__main__':
    unittest.main()
