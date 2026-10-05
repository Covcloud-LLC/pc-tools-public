"""Report written by the comparison run: order, inert source text, exact appendix and all-or-none publication."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_compare import BUNDLE, CASES, ROOT, SCRIPT, SYNTHETIC, base, mod

SECTIONS = ['## Changed worksheets', '## Equal worksheets', '## Unresolved worksheets', '## Appendix: comparison JSON']


def appendix(text):
    match = re.search(r'^## Appendix: comparison JSON\n\n.*?\n\n(`{3,})json\n(.*?)\n\1\n\Z', text, re.M | re.S)
    return match.group(2) + '\n'


def body(text):
    return text.split('## Appendix: comparison JSON')[0]


def cells(row):
    return [c.strip() for c in re.split(r'(?<!\\)\|', row)[1:-1]]


def rows(text):
    changed = body(text).split('## Changed worksheets')[1:]
    lines = changed[0].split('\n## ')[0].splitlines() if changed else []
    return [r for r in lines if r.startswith('| ') and not r.startswith('| Worksheet')]


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.a, self.b = self.home / 'a.json', self.home / 'b.json'
        self.out, self.report = self.home / 'comparison.json', self.home / 'report.md'

    def write(self, a, b):
        self.a.write_text(json.dumps(a))
        self.b.write_text(json.dumps(b))

    def cli(self, extra=(), script=SCRIPT, report=None, output=None, cwd=None):
        return subprocess.run([sys.executable, str(script), '--baseline', str(self.a), '--candidate', str(self.b),
                               '--output', str(output or self.out), '--report', str(report or self.report)] + list(extra),
                              cwd=cwd or self.home, capture_output=True, text=True)

    def render(self, a, b):
        self.write(a, b)
        run = self.cli(['--overwrite'])
        self.assertIn(run.returncode, (0, 3), run.stderr)
        return json.loads(self.out.read_text()), self.report.read_text()

    def test_order_counts_and_exact_appendix_for_every_case(self):
        for name, case in CASES.items():
            self.write(case['baseline'], case['candidate'])
            run = self.cli(['--overwrite'])
            if run.returncode == 2:
                continue
            with self.subTest(case=name):
                result, text = json.loads(self.out.read_text()), self.report.read_text()
                self.assertEqual(appendix(text), self.out.read_text())
                positions = [text.find(h) for h in SECTIONS if h in text]
                self.assertEqual(positions, sorted(positions))
                self.assertLess(text.find('| Established worksheet pairs |'), positions[0])
                equal = [p for p in result['pairs'] if p['outcome'] == 'equal']
                changed = [p for p in result['pairs'] if p['outcome'] == 'different']
                self.assertEqual(len(re.findall(r'^- Pair \d+:', text, re.M)), len(equal))
                self.assertEqual(len(re.findall(r'^### Pair \d+:', text, re.M)), len(changed))
                findings = sum(len(p['context_changes']) + len(p['identifier_changes']) for p in changed)
                self.assertEqual(len(rows(text)), findings)
                self.assertTrue(all(len(cells(r)) == 5 for r in rows(text)))
                self.assertEqual(len(re.findall(r'^- (Baseline|Candidate) \d+,', text, re.M)), len(result['unresolved']))
                self.assertEqual('**Incomplete comparison.**' in text, not result['complete'])
                self.assertNotIn(str(self.home), text)
                self.assertNotIn('opaque_text', text)

    def test_cells_show_categories_types_presence_and_receivers(self):
        expected = {
            'E05': ('`20` as `java.math.BigDecimal`', '`20` as `java.lang.Integer`', 'type'),
            'E07': ('`20` as `java.math.BigDecimal`', '`null` as `java.math.BigDecimal`', 'value, type'),
            'E08': ('`null`', '(absent)', 'removed'),
            'E09': ('`"Vehicle:17"` opaque', '`"Vehicle:18"` opaque', 'value'),
            'E10': ('`20`, receiver `CostData:17`', '`20`, receiver `CostData:18`', 'receiver'),
            'E16': (None, None, 'value'),
            'E24': ('`false`', '`true`', 'value'),
        }
        for name, (before, after, category) in expected.items():
            with self.subTest(case=name):
                _, text = self.render(CASES[name]['baseline'], CASES[name]['candidate'])
                row = cells(rows(text)[0])
                self.assertEqual(row[4], category)
                if before is not None:
                    self.assertEqual(row[2:4], [before, after])
                if name == 'E16':
                    self.assertEqual(row[1], '`a.[b.Value]`')
        _, text = self.render(CASES['E21']['baseline'], CASES['E21']['candidate'])
        self.assertEqual(cells(rows(text)[0])[1:], ['`routine.RoutineVersion`', '`"1"`', '`"2"`', 'context'])
        _, text = self.render(CASES['E14']['baseline'], CASES['E14']['candidate'])
        self.assertIn('Tag ""', text)
        self.assertIn('Tag absent', text)

    def test_hostile_source_text_stays_inert(self):
        a = base()
        hostile = 'x | y `z` <script>alert(1)</script>\n# Fake heading\n[link](https://example.invalid) ‮\x00 雪'
        a['worksheets'][0]['metadata']['Description'] = hostile
        b = copy.deepcopy(a)
        b['worksheets'][0]['identifiers'][0]['value'] = {'kind': 'string', 'type': None, 'value': hostile}
        b['worksheets'][0]['identifiers'][0]['identifier'] = a['worksheets'][0]['identifiers'][0]['identifier']
        a['worksheets'][0]['identifiers'][0]['value'] = {'kind': 'string', 'type': None, 'value': '``'}
        _, text = self.render(a, b)
        for raw in ('‮', '\x00', '雪', '<script>alert(1)</script>\n'):
            self.assertNotIn(raw, text)
        self.assertEqual([l for l in text.splitlines() if l.startswith('#')],
                         ['# Worksheet comparison report', '## Changed worksheets', '### Pair 0: baseline 0 -> candidate 0',
                          '## Unresolved worksheets', '## Appendix: comparison JSON'])
        row = cells(rows(text)[0])
        self.assertEqual(len(row), 5)
        self.assertTrue(row[0].startswith('`` ') or row[0].startswith('``x'), row[0])
        for value in row[:4]:
            self.assertRegex(value, r'^(`+)[^`].*[^`]\1(?: \(shortened\))?$|^(`+) .* \2(?: \(shortened\))?$')
        self.assertEqual(json.loads(appendix(text)), json.loads(self.out.read_text()))
        self.assertEqual(json.loads(appendix(text))['pairs'][0]['identifier_changes'][0]['candidate']['value']['value'], hostile)

    def test_long_values_are_marked_shortened_and_exact_in_appendix(self):
        a = base()
        b = copy.deepcopy(a)
        long_value = '9' * 500
        b['worksheets'][0]['identifiers'][0]['value']['value'] = long_value
        _, text = self.render(a, b)
        after = cells(rows(text)[0])[3]
        self.assertTrue(after.endswith('...` (shortened)'))
        self.assertLess(len(after), 100)
        self.assertIn('the appendix has the exact value', text)
        self.assertIn('"' + long_value + '"', appendix(text))
        _, text = self.render(base(), base())
        self.assertNotIn('shortened', text)

    def test_repeatable_without_paths(self):
        self.write(CASES['E02']['baseline'], CASES['E02']['candidate'])
        self.assertEqual(self.cli().returncode, 3)
        first = self.report.read_bytes()
        other = self.home / 'elsewhere'
        other.mkdir()
        self.assertEqual(self.cli(report=other / 'r.md', output=other / 'c.json', cwd=other).returncode, 3)
        self.assertEqual((other / 'r.md').read_bytes(), first)

    def test_errors_publish_neither_file(self):
        self.write(base(), base())
        self.a.write_text('{')
        self.assertEqual(self.cli().returncode, 2)
        self.assertFalse(self.out.exists() or self.report.exists())
        self.out.write_text('prior output')
        self.report.write_text('prior report')
        self.assertEqual(self.cli(['--overwrite']).returncode, 2)
        self.assertEqual((self.out.read_text(), self.report.read_text()), ('prior output', 'prior report'))
        self.write(base(), base())
        self.out.unlink()
        # The report already exists, so neither file is written without --overwrite.
        self.assertEqual(self.cli().returncode, 2)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.report.read_text(), 'prior report')

    def test_report_aliases_refused(self):
        self.write(base(), base())
        raw = self.a.read_bytes()
        link = self.home / 'link.md'
        link.symlink_to(self.a)
        for report in (self.a, self.b, link, self.out, self.home / '.' / 'comparison.json'):
            with self.subTest(report=str(report)):
                self.assertEqual(self.cli(['--overwrite'], report=report).returncode, 2)
                self.assertEqual(self.a.read_bytes(), raw)
                self.assertFalse(self.out.exists())

    def test_failed_second_publish_rolls_back_the_first(self):
        self.write(base(), base())
        original_link, calls = os.link, []

        def second_fails(src, dst, **kw):
            calls.append(dst)
            if str(dst) == str(self.report):
                raise OSError('simulated failure')
            return original_link(src, dst, **kw)
        with patch.object(mod.os, 'link', side_effect=second_fails):
            with self.assertRaises(mod.CompareError):
                mod.run(self.a, self.b, self.out, False, self.report)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.out.exists() or self.report.exists())
        self.out.write_text('prior output')
        target = self.home / 'target.md'
        target.write_text('prior target')
        self.report.symlink_to(target)
        original_replace = os.replace

        def report_fails(src, dst):
            if str(dst) == str(self.report):
                raise OSError('simulated failure')
            return original_replace(src, dst)
        with patch.object(mod.os, 'replace', side_effect=report_fails):
            with self.assertRaises(mod.CompareError):
                mod.run(self.a, self.b, self.out, True, self.report)
        self.assertEqual(self.out.read_text(), 'prior output')
        self.assertEqual(os.readlink(self.report), str(target))
        self.assertEqual(target.read_text(), 'prior target')
        self.assertEqual(list(self.home.glob('.worksheet-compare-*')), [])
        status, code = mod.run(self.a, self.b, self.out, True, self.report)
        self.assertEqual((code, status['status']), (0, 'equal'))
        self.assertFalse(self.report.is_symlink())
        self.assertEqual(target.read_text(), 'prior target')

    def test_checked_in_synthetic_examples_regenerate_by_hash(self):
        cases = sorted(p.name for p in SYNTHETIC.iterdir() if p.is_dir())
        self.assertEqual(cases, ['cp', 'homeowners', 'pa'])
        for case in cases:
            with self.subTest(case=case):
                folder = SYNTHETIC / case
                self.assertEqual(sorted(p.name for p in folder.iterdir()),
                                 ['baseline.json', 'baseline.xml', 'candidate.json', 'candidate.xml', 'comparison.json', 'report.md'])
                run = subprocess.run([sys.executable, str(SCRIPT), '--baseline', str(folder / 'baseline.json'),
                                      '--candidate', str(folder / 'candidate.json'), '--output', str(self.out),
                                      '--report', str(self.report), '--overwrite'], capture_output=True, text=True)
                self.assertEqual(run.returncode, 3 if case == 'homeowners' else 0, run.stderr)
                for name, produced in (('comparison.json', self.out), ('report.md', self.report)):
                    self.assertEqual(hashlib.sha256(produced.read_bytes()).hexdigest(),
                                     hashlib.sha256((folder / name).read_bytes()).hexdigest(), name)
                inputs = json.loads((folder / 'comparison.json').read_text())['inputs']
                for side in ('baseline', 'candidate'):
                    self.assertEqual(inputs[side]['sha256'], hashlib.sha256((folder / (side + '.json')).read_bytes()).hexdigest())
                self.assertNotIn('app.', ''.join(p.read_text() for p in folder.iterdir()))

    def test_installed_bundle_skill_and_prompt_commands(self):
        installed = self.home / 'installed bundle'
        shutil.copytree(BUNDLE, installed, ignore=shutil.ignore_patterns('__pycache__'))
        unrelated = self.home / 'unrelated'
        unrelated.mkdir()
        shutil.copyfile(SYNTHETIC / 'cp/baseline.json', self.a)
        shutil.copyfile(SYNTHETIC / 'cp/candidate.json', self.b)
        for entrypoint in ('SKILL.md', 'PROMPT.md'):
            self.assertIn('scripts/worksheet-compare.py', (installed / entrypoint).read_text())
            self.assertIn('--report', (installed / entrypoint).read_text())
            report = unrelated / (entrypoint + '.report.md')
            run = self.cli(script=installed / 'scripts/worksheet-compare.py', report=report,
                           output=unrelated / (entrypoint + '.json'), cwd=unrelated)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['report'], str(report))
            self.assertEqual(report.read_bytes(), (SYNTHETIC / 'cp/report.md').read_bytes())
        for client in ('.agents', '.claude'):
            self.assertEqual((ROOT / client / 'skills/pc-worksheet-comparison').resolve(), BUNDLE.resolve())


if __name__ == '__main__':
    unittest.main()
