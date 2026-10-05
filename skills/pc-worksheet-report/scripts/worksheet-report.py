#!/usr/bin/env python3
"""Validate one comparison-v1 result and save its full local Markdown evidence report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote

from report_contract import ReportError
from report_validation import SIDES, validate


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ReportError('invalid_input', 'Duplicate JSON key: ' + name)
        result[name] = value
    return result


def invalid_constant(value):
    raise ReportError('invalid_input', 'Non-JSON constant: ' + value)


def read_input(path):
    raw = path.read_bytes()
    document = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=invalid_constant)
    validate(document)
    return document, hashlib.sha256(raw).hexdigest()


def json_text(value):
    # JSON escaping preserves exact strings, including controls, bidi and Unicode;
    # source markup remains inert data and can be decoded without losing evidence.
    return json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False)


def block(value):
    text = json_text(value)
    fence = '`' * max(3, 1 + max((len(m.group()) for m in re.finditer(r'`+', text)), default=0))
    return fence + 'json\n' + text + '\n' + fence + '\n'


def context_evidence(w):
    return {'index': w['index'], 'metadata': w['metadata'], 'routine': w['routine'],
            'Tag_state': {'present': True, 'value': w['metadata']['Tag']} if 'Tag' in w['metadata'] else {'present': False}}


def report(doc, source, digest, destination):
    summary = doc['summary']
    complete = doc['complete']
    lines = ['# Worksheet comparison report', '',
             ('**Complete comparison — {}.**'.format(doc['outcome']) if complete else '**Incomplete comparison.**'), '',
             '{} established worksheet pairs; {} pairs with changes. {} distinct changed identifiers '
             'among {} compared identities; {} changed context fields. Unresolved worksheets: '
             '{} baseline, {} candidate.'.format(summary['established_pairs'], summary['changed_pairs'],
                 summary['changed_identifiers'], summary['compared_identifiers'], summary['changed_context_fields'],
                 summary['unresolved_baseline'], summary['unresolved_candidate']), '']
    if not complete:
        lines += ['**Coverage is incomplete. Equal established pairs or zero recorded changes do not establish full equality.**', '']
    elif doc['outcome'] == 'equal':
        lines += ['Equality applies only to recorded evidence under the comparison policy.', '']
    lines += ['Baseline and candidate retain their source roles; no chronology is inferred. '
              'Scope assumes different retained runs of the same job and quote branch; the result does not prove this.', '',
              '| Identifier category | Count |', '|---|---:|']
    lines += ['| {} | {} |'.format(c, n) for c, n in summary['identifier_categories'].items()]
    lines += ['', 'Categories overlap and must not be summed as distinct identifiers. Context fields use a separate unit. '
              'Change counts cover established pairs only.', '', '## Source and coverage', '',
              'Comparison result v1; normalized input provenance v2. JSON pointers below identify evidence in the source result.', '',
              'Worksheet indexes are zero-based positions in their original baseline or candidate captures. '
              'Pair numbers are positions in the comparison result\'s `/pairs` array, not worksheet indexes; '
              'use each pair\'s baseline and candidate indexes to locate its original worksheets.', '',
              block({'comparison_file': str(source), 'comparison_sha256': digest, 'format': doc['format'],
                     'version': doc['version'], 'policy': doc['policy'], 'inputs': doc['inputs'], 'summary': summary}),
              '[Open source comparison JSON](<{}>)'.format(quote(os.path.relpath(source.resolve(), destination.parent.resolve()), safe='/')), '',
              'Exact evidence uses JSON escaping: quoted text, empty strings, booleans and null remain distinct. '
              'In identifier changes a null side means an absent record; a present record whose value kind is null '
              'is an explicit null value. Missing receiver keys mean receiver absence. Context presence is explicit. '
              'Metadata keys absent from an object are absent, not empty. Decode JSON escapes to recover the original text.', '',
              '## Findings by established worksheet pair', '']
    appendix = []

    def evidence(pointer, value, label):
        anchor = 'evidence-' + pointer.strip('/').replace('/', '-')
        if len(json_text(value)) > 1800:
            lines.extend(['[{} — full exact evidence](#{})'.format(label, anchor), ''])
            appendix.extend(['<a id="{}"></a>'.format(anchor), '', '### ' + label, '',
                             'Source JSON pointer: `{}`.'.format(pointer), '', block(value),
                             '[Return to finding](#finding-{})'.format(anchor), ''])
            lines.extend(['<a id="finding-{}"></a>'.format(anchor), ''])
        else:
            lines.extend([block(value)])

    if not doc['pairs']:
        lines += ['No worksheet pairs were established. No identifier equality or changes are established for unresolved worksheets.', '']
    for i, pair in enumerate(doc['pairs']):
        p = '/pairs/' + str(i)
        lines += ['### Pair {}: baseline index {} → candidate index {}'.format(i, pair['baseline']['index'], pair['candidate']['index']), '',
                  'Recorded pair outcome: **{}**. Source JSON pointer: `{}`.'.format(pair['outcome'], p), '',
                  'Worksheet labels and exact context (description, qualified reference, Tag state, raw interval and routine):', '']
        evidence(p, {s: context_evidence(pair[s]) for s in SIDES}, 'Pair {} worksheet context'.format(i))
        if pair['outcome'] == 'equal':
            lines += ['No recorded changes in this established pair. Unchanged identifier records remain in the source JSON.', '']
        for group, kind in [('context_changes', 'Context field'), ('identifier_changes', 'Identifier')]:
            for j, change in enumerate(pair[group]):
                pointer = p + '/' + group + '/' + str(j)
                label = 'Pair {} — {} finding {}'.format(i, kind.lower(), j)
                lines += ['#### ' + label, '', 'Source JSON pointer: `{}`.'.format(pointer), '']
                if group == 'identifier_changes':
                    lines += ['Recorded categories: **{}**. Baseline record: **{}**; candidate record: **{}**.'.format(
                        ', '.join(change['categories']), 'absent' if change['baseline'] is None else 'present',
                        'absent' if change['candidate'] is None else 'present'), '']
                    if len(change['categories']) > 1:
                        lines += ['These categories describe one changed structured identifier, counted once.', '']
                    if set(change['categories']) & {'receiver', 'opaque_text'}:
                        lines += ['Recorded representation changed; no underlying object change or business impact is inferred.', '']
                else:
                    lines += ['One changed metadata/routine context field; this is not a changed identifier.', '']
                evidence(pointer, change, label)
    lines += ['## Unresolved worksheets', '',
              'Possible partner indexes are possibilities, not established matches. Unresolved entries do not establish '
              'worksheet additions/removals, replacements, splits or merges. Their identifiers remain uncompared in the source.', '']
    if not doc['unresolved']:
        lines += ['None.', '']
    reasons = {'insufficient_identity': 'Required identity evidence is unavailable.',
               'no_exact_partner': 'No possible exact partner is recorded under the policy.',
               'ambiguous_correspondence': 'Competing possible partners prevent unique correspondence.',
               'partner_has_insufficient_identity': 'The possible partner lacks required identity evidence.'}
    for i, entry in enumerate(doc['unresolved']):
        pointer = '/unresolved/' + str(i)
        label = 'Unresolved {} index {} (entry {})'.format(entry['side'], entry['worksheet']['index'], i)
        lines += ['### ' + label, '', 'Source JSON pointer: `{}`.'.format(pointer), '',
                  ' '.join(reasons[r] for r in entry['reasons']), '']
        value = {k: v for k, v in entry.items() if k != 'worksheet'}
        value['worksheet'] = context_evidence(entry['worksheet'])
        evidence(pointer, value, label)
    lines += ['## Limits', '',
              'No premium totals, causation, significance ranking or business equivalence are inferred. '
              'Descriptions and routine labels are source text. This report concerns retained final values, not interim calculations. '
              'Input hashes are recorded provenance; upstream files were not reopened or independently attested.', '',
              'Source limitations (`/limits`):', '', block(doc['limits'])]
    if appendix:
        lines += ['## Exact evidence appendix', '', 'Long evidence is preserved in full below.', ''] + appendix
    return '\n'.join(lines).rstrip() + '\n'


def protect(source, destination):
    if source.resolve() == destination.resolve() or (source.exists() and destination.exists() and os.path.samefile(source, destination)):
        raise ReportError('input_output_same', 'Output aliases the comparison input, even with --overwrite')


def save(text, source, destination, overwrite):
    temporary = None
    warning = None
    try:
        protect(source, destination)
        if os.path.lexists(destination) and not overwrite:
            raise ReportError('output_exists', 'Output exists; choose a new path or explicitly request --overwrite')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=str(destination.parent),
                                         prefix='.worksheet-report-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        protect(source, destination)
        if overwrite:
            os.replace(str(temporary), str(destination))
        else:
            os.link(str(temporary), str(destination))
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                warning = 'Temporary file cleanup failed: {}'.format(exc)
    return warning


def run(source, destination, overwrite=False):
    source = Path(source).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    protect(source, destination)
    doc, digest = read_input(source)
    text = report(doc, source, digest, destination)
    warning = save(text, source, destination, overwrite)
    status = {'status': 'saved', 'comparison_outcome': doc['outcome'], 'complete': doc['complete'],
            'output': str(destination), 'comparison_sha256': digest, 'report_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'findings': doc['summary']['changed_identifiers'] + doc['summary']['changed_context_fields'],
            'unresolved_entries': len(doc['unresolved'])}
    if warning:
        status['warnings'] = [warning]
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='One comparison v1 JSON result')
    parser.add_argument('--output', required=True, help='Local Markdown path; parent must exist')
    parser.add_argument('--overwrite', action='store_true', help='Explicitly replace an existing report')
    args = parser.parse_args()
    try:
        status = run(args.input, args.output, args.overwrite)
    except (ReportError, OSError, ValueError, UnicodeError, RecursionError, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'category': getattr(exc, 'category', 'input_or_output_error'), 'message': str(exc)},
                         ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps(status, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
