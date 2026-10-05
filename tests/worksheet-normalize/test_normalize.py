import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from expected_fixtures import expected, comparison_expected

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / 'skills/pc-worksheet-normalization'
SCRIPT = BUNDLE / 'scripts/worksheet-normalize.py'
FIXTURES = Path(__file__).parent / 'fixtures'
SYNTHETIC = ROOT / 'examples/synthetic/worksheets'
spec = importlib.util.spec_from_file_location('normalizer', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
D = 'java.math.BigDecimal'


def xml(body, metadata='FixedId="synthetic:1" Description="same"', routine=''):
    return '<Worksheets><Worksheet {}><Routine {}>{}</Routine></Worksheet></Worksheets>'.format(metadata, routine, body)


def store(value, name='x', type_name=D, nested=''):
    attr = '' if value is None else ' Result="{}"'.format(value)
    return '<Store Variable="{}"{} ResultType="{}">{}</Store>'.format(name, attr, type_name, nested)


def variable(value, name='x', type_name=D):
    attr = '' if value is None else ' Value="{}"'.format(value)
    return '<Variable Name="{}"{} ValueType="{}"/>'.format(name, attr, type_name)


def normalized(body):
    return mod.normalize(ET.fromstring(xml(body)))


def records(body):
    return normalized(body)['worksheets'][0]['identifiers']


def find_record(data, kind, name, worksheet=0):
    return next(r for r in data['worksheets'][worksheet]['identifiers']
                if r['identifier']['kind'] == kind and r['identifier'].get('name') == name)


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / 'input.xml'
        self.output = self.directory / 'output.json'
        self.source.write_text(xml(store('1')))

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, source=None, output=None, extra=(), script=SCRIPT):
        return subprocess.run([sys.executable, str(script), '--input', str(source or self.source),
                               '--output', str(output or self.output)] + list(extra),
                              capture_output=True, text=True, cwd=self.directory)

    def test_hand_authored_expected_fixtures(self):
        for product in ['cp', 'pa', 'homeowners']:
            with self.subTest(product=product):
                actual = mod.normalize(mod.read_xml(FIXTURES / (product + '-synthetic.xml')))
                saved = json.loads((FIXTURES / (product + '-synthetic.expected.json')).read_text())
                self.assertEqual(saved, expected(product))
                self.assertEqual(actual, saved)

    def test_interim_values_absent_final_changes_visible(self):
        a = normalized(store('10') + store('20'))
        self.assertEqual(a, normalized(store('15') + store('20')))
        self.assertNotEqual(a, normalized(store('10') + store('21')))
        self.assertEqual(len(a['worksheets'][0]['identifiers']), 1)

    def test_comparison_illustrations(self):
        pairs = {}
        for product in ['cp', 'pa', 'homeowners']:
            pairs[product] = []
            for side, role in [('before', 'baseline'), ('after', 'candidate')]:
                stem = SYNTHETIC / product / role
                actual = mod.normalize(mod.read_xml(stem.with_suffix('.xml')))
                saved = json.loads(stem.with_suffix('.json').read_text())
                self.assertEqual(actual, comparison_expected(product, side))
                self.assertEqual(actual, saved)
                pairs[product].append(actual['worksheets'])
        # Correspondence is specified by fixture authors, not inferred by a comparator.
        before, after = pairs['cp']
        for left, right, amount in [(0, 2, '21'), (1, 0, '40'), (2, 1, '20')]:
            self.assertEqual(before[left]['metadata'], after[right]['metadata'])
            self.assertEqual(after[right]['identifiers'][0]['value']['value'], amount)
        self.assertEqual(before[1]['identifiers'], after[0]['identifiers'])
        self.assertEqual(before[2]['identifiers'], after[1]['identifiers'])
        self.assertNotEqual(before[0]['routine'], after[2]['routine'])
        before, after = pairs['pa']
        self.assertEqual(before[0]['metadata'], after[1]['metadata'])
        self.assertEqual(before[1]['metadata'], after[0]['metadata'])
        self.assertEqual(before[1]['identifiers'], after[0]['identifiers'])
        self.assertNotEqual(before[0]['identifiers'], after[1]['identifiers'])
        before, after = pairs['homeowners']
        # Proof of missing information: removing the changed IDs leaves four
        # indistinguishable entries. Neither possible pairing has more evidence.
        projections = [dict(w, metadata={k: v for k, v in w['metadata'].items() if k != 'FixedId'})
                       for w in before + after]
        self.assertTrue(all(w == projections[0] for w in projections))

    def test_tag_is_exact_optional_context_not_a_value_or_global_scope(self):
        for tag in ['', 'driver:001', ' A &amp; B ']:
            source = ET.fromstring(xml(store('1'), 'FixedId="same" Tag="{}"'.format(tag)))
            source.append(ET.fromstring(xml(store('2'), 'FixedId="same" Tag="{}"'.format(tag)))[0])
            data = mod.normalize(source)
            self.assertEqual(data['version'], 2)
            self.assertEqual(len(data['worksheets']), 2)
            for original, ws in zip(source, data['worksheets']):
                self.assertEqual(ws['metadata'], original.attrib)
                self.assertEqual(len(ws['identifiers']), 1)
            self.assertEqual([w['identifiers'][0]['value']['value'] for w in data['worksheets']], ['1', '2'])
        self.assertNotIn('Tag', normalized(store('1'))['worksheets'][0]['metadata'])
        # A tag is not interpreted, parsed as a number, or accepted at other levels.
        with self.assertRaises(mod.NormalizeError):
            mod.normalize(ET.fromstring(xml(store('1'), routine='Tag="driver:a"')))

    def test_nested_assignment_and_later_read(self):
        assignment = store('11', nested=variable('10') + '<Constant Operator="Plus" Value="1" ValueType="java.lang.Integer"/>')
        self.assertEqual(records(assignment)[0]['value']['value'], '11')
        data = normalized(assignment + store('99', name='y', nested=variable('12')))
        self.assertEqual(find_record(data, 'variable', 'x')['value']['value'], '12')

    def test_last_missing_replaces_populated_and_empty_string_is_not_missing(self):
        for body in [store('100') + store(None), store('100') + store('5', name='y', nested=variable(None))]:
            val = find_record(normalized(body), 'variable', 'x')['value']
            self.assertEqual(val, {'kind': 'null', 'value': None, 'type': D})
        self.assertEqual(records(store('', type_name='java.lang.String'))[0]['value']['value'], '')
        self.assertEqual(records(store('null', type_name='java.lang.String'))[0]['value']['kind'], 'string')

    def test_exact_numeric_canonicalization(self):
        self.assertEqual(normalized(store('1.0')), normalized(store('1.00')))
        self.assertNotEqual(normalized(store('0.09918')), normalized(store('0.0992')))
        cases = {'123456789012345678901234567890.000001000': '123456789012345678901234567890.000001',
                 '-0.000': '0', '+1.2300e+3': '1230', '.00100': '0.001',
                 '10e-5': '0.0001', '-12.3400': '-12.34', '1000e-2': '10', '1E+0': '1', '1e+' + '0' * 5000 + '2': '100'}
        for raw, want in cases.items():
            self.assertEqual(records(store(raw))[0]['value']['value'], want)
        # Count the canonical decimal's digits once, including the leading zero.
        for raw, want in [('0.' + '1' * 60000, '0.' + '1' * 60000),
                          ('1' * 60000 + '.1', '1' * 60000 + '.1'),
                          ('1e99999', '1' + '0' * 99999),
                          ('1e-99999', '0.' + '0' * 99998 + '1')]:
            self.assertEqual(mod.canonical_number(raw, '/number'), want)
        for raw in ['NaN', 'Infinity', '1,000', '', '1e100000', '1e-100000', '1e100001', '1e99999999']:
            with self.subTest(raw=raw), self.assertRaises(mod.NormalizeError):
                records(store(raw))

    def test_numeric_looking_strings_untyped_values_booleans_and_opaque(self):
        self.assertEqual(records(store('001', type_name='java.lang.String'))[0]['value']['value'], '001')
        for raw, want in [('true', True), ('false', False)]:
            self.assertIs(records(store(raw, type_name='java.lang.Boolean'))[0]['value']['value'], want)
        with self.assertRaises(mod.NormalizeError):
            records(store('yes', type_name='boolean'))
        val = records(store('{...}', type_name='entity.Cost[]'))[0]['value']
        self.assertEqual(val, {'kind': 'opaque', 'type': 'entity.Cost[]', 'value': '{...}', 'opaque': True})

    def test_properties_distinct_objects_and_last_read(self):
        body = '<PropertySet ObjectName="a" ObjectType="Cost" PropertyName="Rate" Result="0.09918" ResultType="java.math.BigDecimal"/>'
        body += '<PropertySet ObjectName="b" ObjectType="Cost" PropertyName="Rate" Result="4" ResultType="java.math.BigDecimal"/>'
        body += store('8', nested='<PropertyGet ObjectName="a" ObjectType="Cost" ObjectValue="Cost@123" PropertyName="Rate" Value="0.0992" ValueType="java.math.BigDecimal"/>')
        props = [r for r in records(body) if r['identifier']['kind'] == 'property']
        self.assertEqual([(r['identifier']['object']['name'], r['value']['value']) for r in props], [('a', '0.0992'), ('b', '4')])
        self.assertEqual(props[0]['receiver']['value'], 'Cost@123')
        body += '<PropertySet ObjectName="a" ObjectType="Cost" PropertyName="Rate"/>'
        prop = next(r for r in records(body) if r['identifier'].get('object', {}).get('name') == 'a')
        self.assertIsNone(prop['value']['value'])
        self.assertNotIn('receiver', prop)

    def test_missing_or_ambiguous_worksheet_context_is_preserved(self):
        # These are valid producer inputs even when a consumer cannot pair them.
        root = ET.fromstring('<Worksheets><Worksheet><Routine/></Worksheet>'
                             '<Worksheet FixedId="" EffectiveDate="later" ExpirationDate="earlier" Tag="">'
                             '<Routine RoutineCode=""/></Worksheet>'
                             '<Worksheet FixedId="" EffectiveDate="later" ExpirationDate="earlier" Tag="">'
                             '<Routine RoutineCode=""/></Worksheet></Worksheets>')
        data = mod.normalize(root)
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['worksheets'][0], {'metadata': {}, 'routine': {}, 'identifiers': []})
        expected_ambiguous = {'metadata': {'FixedId': '', 'EffectiveDate': 'later',
                                          'ExpirationDate': 'earlier', 'Tag': ''},
                              'routine': {'RoutineCode': ''}, 'identifiers': []}
        self.assertEqual(data['worksheets'][1:], [expected_ambiguous, expected_ambiguous])

    def test_worksheet_scope_and_metadata(self):
        one = xml(store('1'), 'Description="same" FixedId="a" EffectiveDate="today" ExpirationDate="later"', 'RateBookCode="book" RateBookEdition="2" RoutineCode="routine" RoutineVersion="3"')
        two = xml(store('2'), 'Description="same" FixedId="a"')
        root = ET.fromstring(one)
        root.append(ET.fromstring(two)[0])
        data = mod.normalize(root)
        self.assertEqual(len(data['worksheets']), 2)
        self.assertEqual([w['identifiers'][0]['value']['value'] for w in data['worksheets']], ['1', '2'])
        self.assertEqual(data['worksheets'][0]['metadata'], {'Description': 'same', 'FixedId': 'a', 'EffectiveDate': 'today', 'ExpirationDate': 'later'})
        self.assertEqual(data['worksheets'][0]['routine'], {'RateBookCode': 'book', 'RateBookEdition': '2', 'RoutineCode': 'routine', 'RoutineVersion': '3'})

    def test_call_results_arguments_parameters_and_contexts(self):
        def fn(cls, val, name='Value'):
            return '<Function ClassName="{}" Name="f" Value="{}" ValueType="java.math.BigDecimal"><Argument Name="{}" Value="{}" ValueType="java.math.BigDecimal"/></Function>'.format(cls, val, name, val)
        def query(table, val, factor='Rate'):
            attr = '' if val is None else ' Value="{}"'.format(val)
            return '<RateQuery TableCode="{}" FactorName="{}" Type="SingleFactor"{}><QueryParam Name="Value"{}/></RateQuery>'.format(table, factor, attr, attr)
        body = store('1', nested=fn('A', '10') + fn('B', '30') + fn('A', '20') + query('T', '001') + query('U', '002') + query('T', None))
        data = records(body)
        functions = [r for r in data if r['identifier']['kind'] == 'function']
        self.assertEqual([r['value']['value'] for r in functions], ['20', '30'])
        arguments = [r for r in data if r['identifier']['kind'] == 'argument']
        self.assertEqual([r['value']['value'] for r in arguments], ['20', '30'])
        queries = [r for r in data if r['identifier']['kind'] == 'query']
        self.assertEqual({r['identifier']['table']: r['value']['value'] for r in queries}, {'T': None, 'U': '002'})
        parameters = [r for r in data if r['identifier']['kind'] == 'parameter']
        self.assertEqual({r['identifier']['context'][0]['table']: r['value']['value'] for r in parameters}, {'T': None, 'U': '002'})
        self.assertEqual(next(r for r in parameters if r['value']['value'])['value']['kind'], 'string')

    def test_nested_call_scopes_and_void_method_arguments(self):
        body = store('1', nested='<Function ClassName="A" Name="outer" Value="1"><Argument Name="left"><Function ClassName="B" Name="inner" Value="10"/></Argument><Argument Name="right"><Function ClassName="B" Name="inner" Value="20"/></Argument></Function>')
        body += '<InstanceMethod FunctionName="m" ObjectName="a" ObjectType="C"><Argument Name="Value" Value="a"/></InstanceMethod>'
        body += '<InstanceMethod FunctionName="m" ObjectName="b" ObjectType="C"><Argument Name="Value" Value="b"/></InstanceMethod>'
        data = records(body)
        inner = [r for r in data if r['identifier'].get('name') == 'inner']
        self.assertEqual(len(inner), 2)
        self.assertEqual({r['value']['value'] for r in inner}, {'10', '20'})
        self.assertEqual(len([r for r in data if r['identifier'].get('name') == 'Value']), 2)
        self.assertFalse(any(r['identifier']['kind'] == 'method' for r in data))

    def test_recorded_conditions_not_recomputed(self):
        body = store('1') + '<ConditionalGroup><If Result="false"><Condition>' + variable('2') + '</Condition></If><Else Result="true">' + store('3') + '</Else><EndIf/></ConditionalGroup>'
        self.assertEqual(records(body)[0]['value']['value'], '3')
        body += '<ConditionalGroup><If Result="true"><Condition>' + variable('4') + '</Condition>' + store('5') + '</If><EndIf/></ConditionalGroup>'
        self.assertEqual(records(body)[0]['value']['value'], '5')

    def test_repeatability_input_fidelity_and_explicit_overwrite(self):
        before = self.source.read_bytes()
        run = self.cli()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['output'], str(self.output))
        saved = self.output.read_bytes()
        refused = self.cli()
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(json.loads(refused.stderr)['category'], 'output_exists')
        self.assertEqual(self.output.read_bytes(), saved)
        self.assertEqual(self.cli(extra=['--overwrite']).returncode, 0)
        self.assertEqual(self.output.read_bytes(), saved)
        self.assertEqual(self.source.read_bytes(), before)

    def test_installed_bundle_direct_invocation(self):
        installed = self.directory / 'installed bundle'
        shutil.copytree(BUNDLE, installed)
        run = self.cli(source=FIXTURES / 'pa-synthetic.xml', script=installed / 'scripts/worksheet-normalize.py')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(self.output.read_text()), expected('pa'))

    def test_unknown_semantics_fail_without_replacing_output(self):
        cases = [xml('<Loop/>'), xml('<Store Variable="x" Mystery="2"/>'),
                 xml('<Store/>'), xml('<Store Variable="x"><Argument Name="Value"/></Store>'),
                 xml('<Store Variable="x"><Function ClassName="A" Name="f"><Store Variable="x"/></Function></Store>'),
                 xml('<Store Variable="x"><Function ClassName="A" Name="f" ObjectName="a"/></Store>'),
                 xml('<Store Variable="x"><RateQuery TableCode="T" FactorName="F" Type="MultiFactor"/></Store>'),
                 xml('<Store Variable="x">unmodeled text</Store>'),
                 xml('<ConditionalGroup><Else Result="true"/><EndIf/></ConditionalGroup>'),
                 xml('<ConditionalGroup><If Result="false"><Condition/>'+store('3')+'</If><EndIf/></ConditionalGroup>'),
                 xml('<ConditionalGroup><If Result="true"><Condition/>'+store('3')+'</If><Else Result="true">'+store('4')+'</Else><EndIf/></ConditionalGroup>'),
                 xml('<ConditionalGroup><If Result="false"><Condition/></If><Else Result="false"/><EndIf/></ConditionalGroup>'),
                 '<Worksheets/>', '<Worksheet/>', '<Worksheets><Worksheet><Routine/><Routine/></Worksheet></Worksheets>',
                 '<!DOCTYPE Worksheets [<!ENTITY data "1">]>' + xml(store('&data;'))]
        self.output.write_text('keep me')
        for content in cases:
            with self.subTest(content=content):
                self.source.write_text(content)
                run = self.cli(extra=['--overwrite'])
                self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
                self.assertEqual(json.loads(run.stderr)['category'], 'unsupported_content')
                self.assertEqual(self.output.read_text(), 'keep me')
                self.assertFalse(list(self.directory.glob('.worksheet-normalize-*')))

    def test_malformed_missing_and_unreadable_inputs(self):
        self.output.write_text('preserve')
        self.source.write_text('<Worksheets>')
        self.assertEqual(json.loads(self.cli(extra=['--overwrite']).stderr)['category'], 'malformed_xml')
        self.source.unlink()
        self.assertEqual(json.loads(self.cli(extra=['--overwrite']).stderr)['category'], 'input_read')
        self.source.mkdir()
        self.assertEqual(json.loads(self.cli(extra=['--overwrite']).stderr)['category'], 'input_read')
        self.source.rmdir()
        self.source.write_text(xml(store('1')))
        self.source.chmod(0)
        try:
            if os.geteuid() == 0:
                # Root bypasses POSIX permissions; exercise the same read failure.
                with patch.object(Path, 'open', side_effect=PermissionError('denied')):
                    with self.assertRaises(mod.NormalizeError) as caught:
                        mod.read_xml(self.source)
                self.assertEqual(caught.exception.category, 'input_read')
            else:
                self.assertEqual(json.loads(self.cli(extra=['--overwrite']).stderr)['category'], 'input_read')
        finally:
            self.source.chmod(0o600)
        self.assertEqual(self.output.read_text(), 'preserve')

    def test_path_resolution_errors_are_actionable_and_preserve_output(self):
        self.output.write_text('preserve')
        unknown_home = '~__pc_tools_missing_user__/worksheet.xml'
        for kwargs, category in [({'source': unknown_home}, 'input_read'),
                                 ({'output': unknown_home}, 'output_write')]:
            with self.subTest(kwargs=kwargs):
                run = self.cli(extra=['--overwrite'], **kwargs)
                self.assertEqual(run.returncode, 1)
                self.assertEqual(json.loads(run.stderr)['category'], category)
                self.assertIn('Cannot resolve', json.loads(run.stderr)['message'])
                self.assertNotIn('Traceback', run.stderr)
                self.assertEqual(self.output.read_text(), 'preserve')
        # Python 3.9 resolve() raises RuntimeError for loops; newer Python may
        # defer the OSError until open(). Both must honor the CLI error contract.
        loop = self.directory / 'loop'
        loop.symlink_to('loop')
        for kwargs, category in [({'source': loop}, 'input_read'),
                                 ({'output': loop / 'output.json'}, 'output_write')]:
            run = self.cli(extra=['--overwrite'], **kwargs)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(json.loads(run.stderr)['category'], category)
            self.assertNotIn('Traceback', run.stderr)
            self.assertEqual(self.output.read_text(), 'preserve')

    def test_input_aliases_protected_even_with_overwrite(self):
        before = self.source.read_bytes()
        for alias in [self.source, self.directory / 'symlink', self.directory / 'hardlink']:
            if alias.name == 'symlink': alias.symlink_to(self.source)
            if alias.name == 'hardlink': os.link(self.source, alias)
            run = self.cli(output=alias, extra=['--overwrite'])
            self.assertEqual(run.returncode, 1)
            self.assertEqual(json.loads(run.stderr)['category'], 'input_output_same')
            self.assertEqual(self.source.read_bytes(), before)

    def test_output_failures_preserve_destination_and_cleanup(self):
        run = self.cli(output=self.directory / 'missing/output.json')
        self.assertEqual(json.loads(run.stderr)['category'], 'output_write')
        self.output.mkdir()
        run = self.cli(extra=['--overwrite'])
        self.assertEqual(json.loads(run.stderr)['category'], 'output_write')
        self.output.rmdir()
        self.output.write_text('preserve')
        for target in ['replace', 'fsync']:
            with patch.object(mod.os, target, side_effect=OSError('simulated disk/write failure')):
                with self.assertRaises(mod.NormalizeError) as caught:
                    mod.convert(self.source, self.output, overwrite=True)
                self.assertEqual(caught.exception.category, 'output_write')
            self.assertEqual(self.output.read_text(), 'preserve')
            self.assertFalse(list(self.directory.glob('.worksheet-normalize-*')))

    def test_cleanup_failure_does_not_misreport_publication_or_mask_write_error(self):
        with patch.object(Path, 'unlink', side_effect=PermissionError('cleanup denied')):
            status = mod.convert(self.source, self.output)
        self.assertEqual(status['status'], 'ok')
        self.assertIn('cleanup denied', status['warnings'][0])
        self.assertEqual(json.loads(self.output.read_text()), normalized(store('1')))
        self.output.write_text('preserve')
        with patch.object(Path, 'unlink', side_effect=PermissionError('cleanup denied')):
            with patch.object(mod.os, 'replace', side_effect=OSError('write denied')):
                with self.assertRaises(mod.NormalizeError) as caught:
                    mod.convert(self.source, self.output, overwrite=True)
        self.assertEqual(caught.exception.category, 'output_write')
        self.assertIn('write denied', str(caught.exception))
        self.assertEqual(self.output.read_text(), 'preserve')

    def test_no_clobber_publication_race_and_symlink_destination(self):
        real_link = os.link
        def racing_link(source, destination):
            Path(destination).write_text('concurrent writer')
            real_link(source, destination)
        with patch.object(mod.os, 'link', side_effect=racing_link):
            with self.assertRaises(mod.NormalizeError) as caught:
                mod.convert(self.source, self.output)
            self.assertEqual(caught.exception.category, 'output_exists')
        self.assertEqual(self.output.read_text(), 'concurrent writer')
        self.output.unlink()
        target = self.directory / 'other.json'
        target.write_text('other')
        self.output.symlink_to(target)
        self.assertEqual(self.cli().returncode, 1)
        self.assertEqual(self.cli(extra=['--overwrite']).returncode, 0)
        self.assertEqual(target.read_text(), 'other')
        self.assertFalse(self.output.is_symlink())


if __name__ == '__main__':
    unittest.main()
