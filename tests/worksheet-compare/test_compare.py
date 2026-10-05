"""Independent expected outcomes for adopted worksheet comparison cases."""
import copy
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

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / 'skills/pc-worksheet-comparison'
SCRIPT = BUNDLE / 'scripts/worksheet-compare.py'
FIXTURES = Path(__file__).parent / 'fixtures'
SYNTHETIC = ROOT / 'examples/synthetic/worksheets'
spec = importlib.util.spec_from_file_location('comparator', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
CASES = {c['id']: c for c in json.loads((FIXTURES / 'edge-cases.json').read_text())['cases']}


def base():
    return copy.deepcopy(CASES['E01']['baseline'])


def result(a, b):
    mod.validate(a)
    mod.validate(b)
    return mod.compare(a, b, {})


# Hand-authored expectations: outcome, pairs, unresolved left/right, changed
# identifiers, changed context fields, nonzero identifier categories.
EXPECTED = {
    'E01': ('equal', 1, 0, 0, 0, 0, {}),
    'E02': ('incomplete', 1, 1, 0, 0, 0, {}),
    'E03': ('equal', 1, 0, 0, 0, 0, {}),
    'E04': ('different', 1, 0, 0, 1, 0, {'value': 1}),
    'E05': ('different', 1, 0, 0, 1, 0, {'type': 1}),
    'E06': ('different', 1, 0, 0, 1, 0, {'value': 1, 'type': 1}),
    'E07': ('different', 1, 0, 0, 1, 0, {'value': 1, 'type': 1}),
    'E08': ('different', 1, 0, 0, 1, 0, {'removed': 1}),
    'E09': ('different', 1, 0, 0, 1, 0, {'value': 1}),
    'E10': ('different', 1, 0, 0, 1, 0, {'receiver': 1}),
    'E11': ('incomplete', 0, 1, 1, 0, 0, {}),
    'E12': ('incomplete', 0, 1, 2, 0, 0, {}),
    'E13': ('incomplete', 0, 1, 1, 0, 0, {}),
    'E14': ('incomplete', 0, 1, 1, 0, 0, {}),
    'E15': ('different', 1, 0, 0, 0, 1, {}),
    'E16': ('different', 1, 0, 0, 1, 0, {'value': 1}),
    'E18': ('incomplete', 0, 1, 1, 0, 0, {}),
    'E19': ('incomplete', 0, 1, 2, 0, 0, {}),
    'E20': ('incomplete', 0, 1, 1, 0, 0, {}),
    'E21': ('different', 1, 0, 0, 0, 1, {}),
    'E24': ('different', 1, 0, 0, 1, 0, {'value': 1}),
    'E26': ('incomplete', 0, 1, 1, 0, 0, {}),
    'E27': ('incomplete', 0, 1, 1, 0, 0, {}),
}


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.a, self.b, self.out = [self.directory / n for n in ('a.json', 'b.json', 'result.json')]
        self.write(base(), base())

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, a, b):
        self.a.write_text(json.dumps(a))
        self.b.write_text(json.dumps(b))

    def cli(self, extra=(), script=SCRIPT, output=None):
        return subprocess.run([sys.executable, str(script), '--baseline', str(self.a), '--candidate', str(self.b),
                               '--output', str(output or self.out)] + list(extra), cwd=self.directory, capture_output=True, text=True)

    def assert_result(self, actual, expected):
        outcome, pairs, ua, ub, changed, fields, cats = expected
        self.assertEqual(actual['outcome'], outcome)
        self.assertEqual(actual['complete'], outcome != 'incomplete')
        s = actual['summary']
        for name, value in [('established_pairs', pairs), ('unresolved_baseline', ua), ('unresolved_candidate', ub),
                            ('changed_identifiers', changed), ('changed_context_fields', fields)]:
            self.assertEqual(s[name], value, name)
        self.assertEqual({k: v for k, v in s['identifier_categories'].items() if v}, cats)
        self.assertEqual(len(actual['pairs']), pairs)
        self.assertEqual(len(actual['unresolved']), ua + ub)
        self.assertTrue(all(u['reasons'] for u in actual['unresolved']))
        self.assertEqual((actual['format'], actual['version']), ('pc-worksheet-comparison', 2))
        self.assertEqual(set(s['identifier_categories']), {'added', 'removed', 'value', 'type', 'receiver'})
        for pair in actual['pairs']:
            changed = pair['outcome'] == 'different'
            self.assertEqual(set(pair), {'baseline', 'candidate', 'outcome'} | ({'context_changes', 'identifier_changes'} if changed else set()))
            for side in ('baseline', 'candidate'):
                self.assertEqual(set(pair[side]), {'index', 'metadata', 'routine'})
        for entry in actual['unresolved']:
            self.assertEqual(set(entry['worksheet']), {'index', 'metadata', 'routine'})

    def test_e01_through_e28_cli_and_exact_evidence(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                self.write(case['baseline'], case['candidate'])
                before = (self.a.read_bytes(), self.b.read_bytes())
                self.out.write_text('prior result')
                run = self.cli(['--overwrite'])
                if name not in EXPECTED:
                    self.assertEqual(run.returncode, 2, run.stderr)
                    self.assertEqual(self.out.read_text(), 'prior result')
                    continue
                self.assertEqual(run.returncode, 3 if EXPECTED[name][0] == 'incomplete' else 0, run.stderr)
                actual = json.loads(self.out.read_text())
                self.assert_result(actual, EXPECTED[name])
                self.assertEqual(before, (self.a.read_bytes(), self.b.read_bytes()))
                for pair in actual['pairs']:
                    sources = {side: case[side]['worksheets'][pair[side]['index']] for side in ('baseline', 'candidate')}
                    for side, source in sources.items():
                        self.assertEqual(pair[side]['metadata'], source['metadata'])
                        self.assertEqual(pair[side]['routine'], source['routine'])
                    for change in pair.get('identifier_changes', []):
                        for side, source in sources.items():
                            record = next((r for r in source['identifiers'] if r['identifier'] == change['identifier']), None)
                            self.assertEqual(change[side], None if record is None else {k: v for k, v in record.items() if k != 'identifier'})
                for entry in actual['unresolved']:
                    source = case[entry['side']]['worksheets'][entry['worksheet']['index']]
                    self.assertEqual(entry['worksheet']['metadata'], source['metadata'])
                    self.assertEqual(entry['worksheet']['routine'], source['routine'])

    def test_cp_pa_ho_oracles(self):
        for product, expected, mapping in [
            ('cp', ('different', 3, 0, 0, 1, 3, {'value': 1}), [(0, 2), (1, 0), (2, 1)]),
            ('pa', ('different', 2, 0, 0, 1, 0, {'value': 1}), [(0, 1), (1, 0)]),
            ('homeowners', ('incomplete', 0, 2, 2, 0, 0, {}), [])]:
            with self.subTest(product=product):
                a, b = [json.loads((SYNTHETIC / product / (side + '.json')).read_text()) for side in ('baseline', 'candidate')]
                actual = result(a, b)
                self.assert_result(actual, expected)
                self.assertEqual([(p['baseline']['index'], p['candidate']['index']) for p in actual['pairs']], mapping)
                if product == 'cp':
                    change = actual['pairs'][0]['identifier_changes'][0]
                    self.assertEqual((change['baseline']['value']['value'], change['candidate']['value']['value']), ('20', '21'))
                    self.assertTrue(all(p['context_changes'][0]['field'] == 'RateBookEdition' for p in actual['pairs']))
                if product == 'pa':
                    self.assertTrue(all(p['baseline']['metadata']['Tag'] == p['candidate']['metadata']['Tag'] for p in actual['pairs']))

    def test_producer_outputs_and_worksheet_reordering(self):
        # Use producer-owned expected v2 artifacts without a runtime dependency.
        sources = (list(SYNTHETIC.glob('*/baseline.json')) + list(SYNTHETIC.glob('*/candidate.json'))
                   + list((ROOT / 'tests/worksheet-normalize/fixtures').glob('*.expected.json')))
        self.assertEqual(len(sources), 9)
        # Real captures are validated where the checkout has them.
        for source in sources + list((ROOT / 'examples/pc').glob('*/*/worksheets/*/worksheets.json')):
            mod.validate(json.loads(source.read_text()))
        a = json.loads((SYNTHETIC / 'pa/baseline.json').read_text())
        b = copy.deepcopy(a)
        b['worksheets'].reverse()
        actual = result(a, b)
        self.assertEqual(actual['outcome'], 'equal')
        self.assertEqual([(p['baseline']['index'], p['candidate']['index']) for p in actual['pairs']], [(0, 1), (1, 0)])

    def test_reversed_missing_null_and_split(self):
        for name in ('E02', 'E08', 'E19'):
            c = CASES[name]
            actual = result(c['candidate'], c['baseline'])
            if name == 'E08':
                self.assertEqual(actual['summary']['identifier_categories']['added'], 1)
                self.assertEqual(actual['pairs'][0]['identifier_changes'][0]['candidate']['value']['kind'], 'null')
            else:
                self.assertEqual(actual['summary']['unresolved_baseline'], EXPECTED[name][3])
                self.assertEqual(actual['summary']['unresolved_candidate'], EXPECTED[name][2])

    def test_partial_identity_competes_only_where_compatible(self):
        a = base()
        other = copy.deepcopy(a['worksheets'][0])
        other['metadata']['FixedId'] = 'Other:b'
        a['worksheets'].append(other)
        b = copy.deepcopy(a)
        partial = copy.deepcopy(other)
        partial['metadata'].pop('EffectiveDate')
        b['worksheets'].append(partial)
        actual = result(a, b)
        self.assertEqual([(p['baseline']['index'], p['candidate']['index']) for p in actual['pairs']], [(0, 0)])
        self.assertEqual(actual['summary']['unresolved_baseline'], 1)
        self.assertEqual(actual['summary']['unresolved_candidate'], 2)
        self.assertEqual(actual['outcome'], 'incomplete')
        self.assertEqual(result(b, a)['summary']['established_pairs'], 1)

    def test_global_missing_identity_withholds_all_compatible_pairs(self):
        a = base()
        a['worksheets'].append(copy.deepcopy(a['worksheets'][0]))
        a['worksheets'][1]['metadata']['FixedId'] = 'Other:b'
        b = copy.deepcopy(a)
        b['worksheets'].append({'metadata': {}, 'routine': {}, 'identifiers': []})
        actual = result(a, b)
        self.assertEqual(actual['summary']['established_pairs'], 0)
        self.assertEqual((actual['summary']['unresolved_baseline'], actual['summary']['unresolved_candidate']), (2, 3))
        # Tag absence is known, so an absent-Tag partial cannot consume a tagged partner.
        a['worksheets'][0]['metadata']['Tag'] = ''
        b['worksheets'][0]['metadata']['Tag'] = ''
        self.assertEqual(result(a, b)['summary']['established_pairs'], 1)

    def test_duplicate_group_does_not_hide_independent_changes(self):
        a = base()
        b = base()
        for doc in (a, b):
            duplicate = copy.deepcopy(doc['worksheets'][0])
            duplicate['metadata']['FixedId'] = 'Other:b'
            doc['worksheets'].extend([duplicate, copy.deepcopy(duplicate)])
        b['worksheets'][0]['identifiers'][0]['value']['value'] = '21'
        actual = result(a, b)
        self.assert_result(actual, ('incomplete', 1, 2, 2, 1, 0, {'value': 1}))

    def test_missing_blank_identity_valid_but_unresolved(self):
        for group, field in [('metadata', 'FixedId'), ('metadata', 'EffectiveDate'), ('metadata', 'ExpirationDate'), ('routine', 'RoutineCode')]:
            for missing in (True, False):
                a = base()
                if missing:
                    a['worksheets'][0][group].pop(field)
                else:
                    a['worksheets'][0][group][field] = ''
                actual = result(a, a)
                self.assertEqual(actual['outcome'], 'incomplete')
                self.assertIn(field, actual['unresolved'][0]['unavailable_identity_fields'])
        a = base()
        a['worksheets'][0]['metadata']['FixedId'] = 'unqualified'
        self.assertEqual(result(a, a)['outcome'], 'incomplete')

    def test_empty_identifiers_and_context_presence(self):
        a = base()
        a['worksheets'][0]['identifiers'] = []
        self.assertEqual(result(a, a)['outcome'], 'equal')
        b = copy.deepcopy(a)
        del b['worksheets'][0]['metadata']['Description']
        del b['worksheets'][0]['routine']['RateBookEdition']
        actual = result(a, b)
        self.assert_result(actual, ('different', 1, 0, 0, 0, 2, {}))
        self.assertTrue(all(c['candidate'] == {'present': False} for c in actual['pairs'][0]['context_changes']))

    def test_overlap_and_receiver_presence(self):
        a = copy.deepcopy(CASES['E10']['baseline'])
        b = copy.deepcopy(a)
        rec = b['worksheets'][0]['identifiers'][0]
        rec['receiver']['value'] = ''
        rec['value'] = {'kind': 'opaque', 'type': 'entity.Cost', 'value': 'Cost:8', 'opaque': True}
        actual = result(a, b)
        self.assert_result(actual, ('different', 1, 0, 0, 1, 0, {'value': 1, 'type': 1, 'receiver': 1}))
        del rec['receiver']
        self.assertEqual(result(a, b)['summary']['identifier_categories']['receiver'], 1)

    def test_full_call_query_named_input_identity(self):
        a = base()
        a['worksheets'][0]['identifiers'] = []
        identities = [
            {'kind': 'function', 'name': 'f', 'class': 'A', 'context': []},
            {'kind': 'function', 'name': 'f', 'class': 'B', 'context': []},
            {'kind': 'query', 'table': 't', 'factor': 'f', 'source': '', 'context': []},
            {'kind': 'query', 'table': 't', 'factor': 'f', 'source': 'other', 'context': []},
        ]
        for arg in ('a.b', 'a'):
            ctx = [{'kind': 'method', 'name': 'm', 'object': {'name': 'o', 'type': 'T'}}, {'kind': 'argument', 'name': arg}]
            identities.extend([
                {'kind': 'function', 'class': 'C', 'name': 'f', 'context': ctx},
                {'kind': 'argument', 'name': 'x', 'context': ctx + [{'kind': 'function', 'class': 'C', 'name': 'f'}]},
                {'kind': 'parameter', 'name': 'x', 'context': ctx + [{'kind': 'query', 'table': 't', 'factor': 'f', 'source': ''}]},
            ])
        for ident in identities:
            a['worksheets'][0]['identifiers'].append({'identifier': ident, 'value': {'kind': 'string', 'type': None, 'value': 'x'}})
        b = copy.deepcopy(a)
        b['worksheets'][0]['identifiers'].reverse()
        b['worksheets'][0]['identifiers'][0]['value']['value'] = 'y'
        actual = result(a, b)
        self.assertEqual(actual['summary']['compared_identifiers'], 10)
        self.assertEqual(actual['summary']['changed_identifiers'], 1)
        self.assertEqual(actual['pairs'][0]['identifier_changes'][0]['identifier'], identities[-1])

    def test_unsupported_versions_envelopes_and_fields(self):
        for av, bv in [(1, 1), (1, 2), (2, 1), (999, 2), (2.0, 2), (True, 2)]:
            a, b = base(), base()
            a['version'], b['version'] = av, bv
            self.write(a, b)
            self.assertEqual(self.cli().returncode, 2)
            self.assertFalse(self.out.exists())
        for mutate in [lambda d: d.update(format='pc-worksheet-comparison-poc'),
                       lambda d: d.update(association={}),
                       lambda d: d['worksheets'][0]['metadata'].update(Extra='x'),
                       lambda d: d['worksheets'][0]['routine'].update(Extra='x'),
                       lambda d: d['worksheets'][0]['identifiers'][0].update(Extra='x')]:
            a = base()
            mutate(a)
            with self.assertRaises(mod.CompareError):
                mod.validate(a)

    def test_invalid_value_representations(self):
        invalid = [
            {'kind': 'number', 'type': 'java.math.BigDecimal', 'value': v}
            for v in [20, True, '20.0', '-0', '+20', '01', '.5', '1e2', 'NaN', '0.' + '0' * 99999 + '1']]
        invalid.extend([
            {'kind': 'boolean', 'type': 'boolean', 'value': 'false'},
            {'kind': 'string', 'type': 'int', 'value': '20'},
            {'kind': 'number', 'type': None, 'value': '20'},
            {'kind': 'null', 'type': None, 'value': 'null'},
            {'kind': 'string', 'type': None, 'value': 'x', 'opaque': True},
            {'kind': 'opaque', 'type': 'T', 'value': 'x'},
            {'kind': 'opaque', 'type': 'T', 'value': 'x', 'opaque': 1},
            {'kind': 'opaque', 'type': 'java.lang.String', 'value': 'x', 'opaque': True},
            {'kind': [], 'type': None, 'value': 'x'},
        ])
        for value in invalid:
            a = base()
            a['worksheets'][0]['identifiers'][0]['value'] = value
            with self.subTest(value=str(value)[:90]), self.assertRaises(mod.CompareError):
                mod.validate(a)
        for text in ['0', '-1', '0.01', '-0.01', '1' + '0' * 99999]:
            a = base()
            a['worksheets'][0]['identifiers'][0]['value']['value'] = text
            mod.validate(a)
        for value in [{'kind': 'null', 'type': 'int', 'value': None},
                      {'kind': 'opaque', 'type': '', 'value': '', 'opaque': True},
                      {'kind': 'string', 'type': None, 'value': ''}]:
            a['worksheets'][0]['identifiers'][0]['value'] = value
            mod.validate(a)

    def test_invalid_identifiers_and_receivers(self):
        for ident in [
            {'kind': 'variable', 'name': ''}, {'kind': 'method', 'name': 'm'},
            {'kind': 'variable', 'name': 'x', 'context': []},
            {'kind': 'argument', 'name': 'x', 'context': []},
            {'kind': 'parameter', 'name': 'x', 'context': [{'kind': 'function', 'class': 'C', 'name': 'f'}]},
            {'kind': 'function', 'name': 'f', 'class': 'C', 'context': [{'kind': 'argument', 'name': 'x'}]},
            {'kind': 'property', 'name': 'p', 'object': {'name': 'x', 'type': ''}},
            {'kind': 'query', 'table': 't', 'factor': 'f', 'context': []},
        ]:
            a = base()
            a['worksheets'][0]['identifiers'][0]['identifier'] = ident
            with self.assertRaises(mod.CompareError):
                mod.validate(a)
        for receiver in [{'type': 'wrong', 'value': 'x', 'opaque': True}, {'type': 'T', 'value': None, 'opaque': True}]:
            a = copy.deepcopy(CASES['E10']['baseline'])
            a['worksheets'][0]['identifiers'][0]['receiver'] = receiver
            with self.assertRaises(mod.CompareError):
                mod.validate(a)

    def test_invalid_json_keys_encoding_unreadable_preserve_prior(self):
        for raw in [b'{', b'{"format":"pc-worksheet-final-values","format":"x"}', b'NaN', b'\xff', b'[]', b'null',
                    json.dumps(base()).replace('"20"', '"\\ud800"').encode()]:
            self.a.write_bytes(raw)
            self.out.write_text('prior')
            self.assertEqual(self.cli(['--overwrite']).returncode, 2)
            self.assertEqual(self.out.read_text(), 'prior')
        self.a.unlink()
        self.assertEqual(self.cli(['--overwrite']).returncode, 2)
        self.a.mkdir()
        self.assertEqual(self.cli(['--overwrite']).returncode, 2)
        self.assertEqual(self.out.read_text(), 'prior')
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('denied')):
            with self.assertRaises(mod.CompareError) as error:
                mod.read_input(self.b)
        self.assertEqual(error.exception.category, 'input_read')

    def test_existing_overwrite_repeatability_and_aliases(self):
        self.assertEqual(self.cli().returncode, 0)
        saved = self.out.read_bytes()
        self.assertEqual(self.cli().returncode, 2)
        self.assertEqual(self.out.read_bytes(), saved)
        self.assertEqual(self.cli(['--overwrite']).returncode, 0)
        self.assertEqual(self.out.read_bytes(), saved)
        for source in (self.a, self.b):
            original = source.read_bytes()
            for mode in ('direct', 'symlink', 'hardlink'):
                alias = self.directory / (source.name + '-' + mode)
                if mode == 'direct':
                    alias = source
                elif mode == 'symlink':
                    alias.symlink_to(source)
                else:
                    os.link(source, alias)
                self.assertEqual(self.cli(['--overwrite'], output=alias).returncode, 2)
                self.assertEqual(source.read_bytes(), original)
        # Input aliases to each other are allowed: self-comparison is meaningful.
        self.b.unlink()
        self.b.symlink_to(self.a)
        self.assertEqual(self.cli(['--overwrite']).returncode, 0)

    def test_write_failures_atomic_publication_and_cleanup(self):
        self.out.write_text('prior')
        for operation in ('replace', 'fsync'):
            with patch.object(mod.os, operation, side_effect=OSError('simulated failure')):
                with self.assertRaises(mod.CompareError):
                    mod.run(self.a, self.b, self.out, True)
            self.assertEqual(self.out.read_text(), 'prior')
            self.assertEqual(list(self.directory.glob('.worksheet-compare-*')), [])
        self.assertEqual(self.cli(output=self.directory / 'missing' / 'out.json').returncode, 2)
        self.out.unlink()
        with patch.object(mod.os, 'link', side_effect=OSError('simulated no-link filesystem')):
            with self.assertRaises(mod.CompareError):
                mod.run(self.a, self.b, self.out)
        self.assertFalse(self.out.exists())
        original_link = os.link
        def racer(src, dst):
            Path(dst).write_text('concurrent writer')
            original_link(src, dst)
        with patch.object(mod.os, 'link', side_effect=racer):
            with self.assertRaises(mod.CompareError):
                mod.run(self.a, self.b, self.out)
        self.assertEqual(self.out.read_text(), 'concurrent writer')
        original_replace = os.replace
        def inspect_atomic(src, dst):
            data = json.loads(Path(src).read_text())
            self.assertEqual(data['outcome'], 'equal')
            self.assertEqual(Path(dst).read_text(), 'concurrent writer')
            original_replace(src, dst)
        with patch.object(mod.os, 'replace', side_effect=inspect_atomic):
            mod.run(self.a, self.b, self.out, True)
        self.assertEqual(json.loads(self.out.read_text())['outcome'], 'equal')

    def test_symlink_parent_and_dangling_destination(self):
        aliasdir = self.directory / 'aliasdir'
        aliasdir.symlink_to(self.directory, target_is_directory=True)
        self.assertEqual(self.cli(['--overwrite'], output=aliasdir / self.a.name).returncode, 2)
        target = self.directory / 'absent'
        self.out.symlink_to(target)
        self.assertEqual(self.cli().returncode, 2)
        self.assertFalse(target.exists())
        self.assertEqual(self.cli(['--overwrite']).returncode, 0)
        self.assertFalse(target.exists())
        self.assertFalse(self.out.is_symlink())

    def test_isolated_bundle_cli(self):
        installed = self.directory / 'installed'
        shutil.copytree(BUNDLE, installed)
        run = self.cli(script=installed / 'scripts/worksheet-compare.py')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(self.out.read_text())['outcome'], 'equal')


if __name__ == '__main__':
    unittest.main()
