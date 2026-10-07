#!/usr/bin/env python3
"""Compare two PC final-values v3 captures; optionally write a Markdown report (Python 3.8+ stdlib)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


class CompareError(Exception):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


METADATA = {'FixedId', 'Tag', 'EffectiveDate', 'ExpirationDate', 'Description'}
ROUTINE = {'RateBookCode', 'RateBookEdition', 'RoutineCode', 'RoutineVersion'}
CATEGORIES = ('added', 'removed', 'value')
POLICY = 'exact-reference-tag-interval-routine-v1'
LIMITS = ['Caller assumes different retained runs of the same job and quote branch; inputs do not attest this.',
          'Only exact qualified reference, Tag presence/value, raw interval and nonempty RoutineCode establish correspondence.',
          'Unresolved worksheets are not inferred additions/removals, replacements, splits or merges.',
          'Object and typekey values are recorded text, not underlying object equality.',
          'changed_identifiers counts each changed name once within established pairs.']


def key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def dump(value):
    # ASCII escapes keep controls, bidi marks and markup inert wherever this text is shown.
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + '\n'


# ---- input validation ------------------------------------------------------

def require(ok, path, message):
    if not ok:
        raise CompareError('invalid_input', '{}: {}'.format(path, message))


def shape(value, required, optional, path):
    require(isinstance(value, dict), path, 'expected object')
    require(set(required) <= set(value) <= set(required) | set(optional), path,
            'expected fields {} with optional {}'.format(sorted(required), sorted(optional)))


def string(value, path, nonempty=False):
    require(isinstance(value, str) and (not nonempty or bool(value)), path, 'expected {}string'.format('nonempty ' if nonempty else ''))
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), path, 'unpaired Unicode surrogate')


def final_value(value, path):
    # Plain JSON: numbers are canonical decimal text, so a string; also boolean or null.
    require(value is None or type(value) is bool or isinstance(value, str), path, 'expected string, boolean or null')
    if isinstance(value, str):
        string(value, path)


def validate(document):
    shape(document, {'format', 'version', 'worksheets'}, set(), '$')
    if document['format'] != 'pc-worksheet-final-values' or type(document['version']) is not int or document['version'] != 3:
        raise CompareError('unsupported_version', 'Both inputs must be pc-worksheet-final-values version 3; '
                                                  're-normalize older captures from their retained worksheet XML')
    require(isinstance(document['worksheets'], list) and bool(document['worksheets']), '$.worksheets', 'expected nonempty array')
    for index, worksheet in enumerate(document['worksheets']):
        path = '$.worksheets[{}]'.format(index)
        shape(worksheet, {'metadata', 'routine', 'identifiers'}, set(), path)
        for field, allowed in [('metadata', METADATA), ('routine', ROUTINE)]:
            shape(worksheet[field], set(), allowed, path + '.' + field)
            for name, value in worksheet[field].items():
                string(value, path + '.' + field + '.' + name)
        require(isinstance(worksheet['identifiers'], dict), path + '.identifiers',
                'expected an object of name to value; re-normalize this capture from its retained worksheet XML')
        # JSON keys are strings; the reader already refused a name written twice.
        for name, value in worksheet['identifiers'].items():
            string(name, path + '.identifiers', True)
            final_value(value, path + '.identifiers' + json.dumps([name], ensure_ascii=True))
    return document


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise CompareError('invalid_input', 'Duplicate JSON key: ' + name)
        result[name] = value
    return result


def invalid_constant(value):
    raise CompareError('invalid_input', 'Non-JSON constant: ' + value)


def read_input(path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompareError('input_read', 'Cannot read {}: {}'.format(path, exc)) from exc
    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=invalid_constant)
        validate(document)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CompareError('invalid_input', 'Invalid JSON/encoding/nesting: {}'.format(exc)) from exc
    return document, {'sha256': hashlib.sha256(raw).hexdigest(), 'format': document['format'], 'version': 3,
                      'worksheet_count': len(document['worksheets']),
                      'identifier_count': sum(len(w['identifiers']) for w in document['worksheets'])}


# ---- comparison ------------------------------------------------------------

def identity(worksheet):
    metadata = worksheet['metadata']
    fixed = metadata.get('FixedId')
    # Producer preserves arbitrary text. Unusable qualification is uncertainty,
    # never a reason to discard a valid document or guess a source namespace.
    qualified = isinstance(fixed, str) and len(fixed.split(':')) == 2 and all(p.strip() for p in fixed.split(':'))
    return {'FixedId': fixed if qualified else None,
            'Tag': (('present', metadata['Tag']) if 'Tag' in metadata else ('absent',)),
            'EffectiveDate': metadata.get('EffectiveDate') or None,
            'ExpirationDate': metadata.get('ExpirationDate') or None,
            'RoutineCode': worksheet['routine'].get('RoutineCode') or None}


def compatible(left, right):
    return all(left[name] is None or right[name] is None or left[name] == right[name] for name in left)


def worksheet_context(index, worksheet):
    return {'index': index, 'metadata': worksheet['metadata'], 'routine': worksheet['routine']}


def presence(mapping, name):
    return {'present': True, 'value': mapping[name]} if name in mapping else {'present': False}


def compare_pair(left, right):
    context_changes = []
    for group in ('metadata', 'routine'):
        for field in sorted(set(left[group]) | set(right[group])):
            before, after = presence(left[group], field), presence(right[group], field)
            if before != after:
                context_changes.append({'group': group, 'field': field, 'baseline': before, 'candidate': after})
    a, b = left['identifiers'], right['identifiers']
    changes = []
    for name in sorted(set(a) | set(b)):
        # The change entry names the identifier once; each side holds only its recorded value.
        before = {'value': a[name]} if name in a else None
        after = {'value': b[name]} if name in b else None
        categories = []
        if before is None or after is None:
            categories.append('added' if before is None else 'removed')
        elif key(before['value']) != key(after['value']):
            categories.append('value')
        if categories:
            changes.append({'name': name, 'categories': categories, 'baseline': before, 'candidate': after})
    return context_changes, changes, len(set(a) | set(b))


def compare(baseline, candidate, inputs):
    sides = [baseline['worksheets'], candidate['worksheets']]
    identities = [[identity(w) for w in side] for side in sides]
    # An edge means possible competition, not a match. Mutual degree one AND
    # complete exact identities establish a pair. Partial identities never pair.
    neighbors = [[[] for _ in side] for side in sides]
    for i, left in enumerate(identities[0]):
        for j, right in enumerate(identities[1]):
            if compatible(left, right):
                neighbors[0][i].append(j)
                neighbors[1][j].append(i)
    pairs, established = [], [set(), set()]
    counts = {name: 0 for name in CATEGORIES}
    changed_context = changed_pairs = changed_identifiers = compared_identifiers = 0
    for i, possible in enumerate(neighbors[0]):
        if len(possible) != 1:
            continue
        j = possible[0]
        if len(neighbors[1][j]) != 1 or None in identities[0][i].values() or None in identities[1][j].values():
            continue
        left, right = sides[0][i], sides[1][j]
        contexts, changes, compared = compare_pair(left, right)
        pair = {'baseline': worksheet_context(i, left), 'candidate': worksheet_context(j, right),
                'outcome': 'different' if contexts or changes else 'equal'}
        if contexts or changes:
            pair.update(context_changes=contexts, identifier_changes=changes)
        pairs.append(pair)
        established[0].add(i)
        established[1].add(j)
        changed_context += len(contexts)
        changed_pairs += bool(contexts or changes)
        changed_identifiers += len(changes)
        compared_identifiers += compared
        for change in changes:
            for category in change['categories']:
                counts[category] += 1
    unresolved = []
    for side, label in enumerate(('baseline', 'candidate')):
        for i, worksheet in enumerate(sides[side]):
            if i in established[side]:
                continue
            missing = sorted(k for k, v in identities[side][i].items() if v is None)
            possible = neighbors[side][i]
            reasons = []
            if missing:
                reasons.append('insufficient_identity')
            if not possible:
                reasons.append('no_exact_partner')
            elif len(possible) > 1 or any(len(neighbors[1 - side][j]) > 1 for j in possible):
                reasons.append('ambiguous_correspondence')
            elif not missing:
                reasons.append('partner_has_insufficient_identity')
            unresolved.append({'side': label, 'worksheet': worksheet_context(i, worksheet), 'reasons': reasons,
                               'unavailable_identity_fields': missing, 'possible_partner_indexes': possible})
    return {'format': 'pc-worksheet-comparison', 'version': 3, 'policy': POLICY,
            'outcome': 'incomplete' if unresolved else 'different' if changed_pairs else 'equal',
            'complete': not unresolved, 'inputs': inputs,
            'summary': {'established_pairs': len(pairs), 'changed_pairs': changed_pairs,
                        'compared_identifiers': compared_identifiers, 'changed_identifiers': changed_identifiers,
                        'changed_context_fields': changed_context, 'identifier_categories': counts,
                        'unresolved_baseline': len(sides[0]) - len(established[0]),
                        'unresolved_candidate': len(sides[1]) - len(established[1])},
            'pairs': pairs, 'unresolved': unresolved, 'limits': LIMITS}


# ---- report ----------------------------------------------------------------

CELL = 60
REASONS = {'insufficient_identity': 'required identity is missing',
           'no_exact_partner': 'no exact partner',
           'ambiguous_correspondence': 'more than one possible partner',
           'partner_has_insufficient_identity': 'its possible partner lacks required identity'}


def code(text, cell=False):
    """Inline code that source text cannot break out of; JSON escapes controls and non-ASCII."""
    text = json.dumps(text, ensure_ascii=True)[1:-1].replace('\\"', '"') or '""'
    if cell:
        text = text.replace('|', '\\|')
    fence = '`' * (1 + max((len(m) for m in re.findall(r'`+', text)), default=0))
    pad = ' ' if text.startswith('`') or text.endswith('`') else ''
    return fence + pad + text + pad + fence


def short(text):
    return (text, False) if len(text) <= CELL else (text[:CELL - 3] + '...', True)


def label(worksheet, cell=False):
    description = worksheet['metadata'].get('Description')
    return code(description, cell) if description else '(no description)'


def where(worksheet):
    m, r = worksheet['metadata'], worksheet['routine']
    parts = ['reference ' + (code(m['FixedId']) if m.get('FixedId') else 'missing'),
             'Tag ' + (code(m['Tag']) if m.get('Tag') else '""' if 'Tag' in m else 'absent'),
             'interval {} to {}'.format(code(m['EffectiveDate']) if m.get('EffectiveDate') else 'missing',
                                        code(m['ExpirationDate']) if m.get('ExpirationDate') else 'missing'),
             'routine ' + (code(r['RoutineCode']) if r.get('RoutineCode') else 'missing')]
    return ', '.join(parts)


def cell(text):
    text, cut = short(text)
    return code(text, True) + (' (shortened)' if cut else ''), cut


def value_cell(record):
    return ('(absent)', False) if record is None else cell(json.dumps(record['value'], ensure_ascii=False))


def presence_cell(wrapper):
    return cell(json.dumps(wrapper['value'], ensure_ascii=False)) if wrapper['present'] else ('(absent)', False)


def render(result):
    s = result['summary']
    heading = ('**Complete comparison: {}.**'.format(result['outcome']) if result['complete']
               else '**Incomplete comparison.** Some worksheets could not be paired; equal pairs below do not mean the inputs are equal.')
    lines = ['# Worksheet comparison report', '', heading, '',
             '| Count | Value |', '|---|---:|',
             '| Established worksheet pairs | {} |'.format(s['established_pairs']),
             '| Pairs with changes | {} |'.format(s['changed_pairs']),
             '| Changed identifiers / compared | {} / {} |'.format(s['changed_identifiers'], s['compared_identifiers']),
             '| Changed context fields | {} |'.format(s['changed_context_fields']),
             '| Unresolved worksheets, baseline / candidate | {} / {} |'.format(s['unresolved_baseline'], s['unresolved_candidate']),
             '',
             'Identifier categories: {}.'.format(
                 ', '.join('{} {}'.format(c, n) for c, n in s['identifier_categories'].items())),
             '']
    shortened = False
    changed = [(i, p) for i, p in enumerate(result['pairs']) if p['outcome'] == 'different']
    equal = [(i, p) for i, p in enumerate(result['pairs']) if p['outcome'] == 'equal']
    if changed:
        lines += ['## Changed worksheets', '']
    for i, pair in changed:
        b, c = pair['baseline'], pair['candidate']
        lines += ['### Pair {}: baseline {} -> candidate {}'.format(i, b['index'], c['index']), '',
                  where(b) + '.', '',
                  '| Worksheet | Identifier | Before | After | Category |', '|---|---|---|---|---|']
        name = label(b, True)
        for change in pair['context_changes']:
            before, cut1 = presence_cell(change['baseline'])
            after, cut2 = presence_cell(change['candidate'])
            shortened = shortened or cut1 or cut2
            lines.append('| {} | {} | {} | {} | context |'.format(
                name, code(change['group'] + '.' + change['field'], True), before, after))
        for change in pair['identifier_changes']:
            ident, cut0 = cell(change['name'])
            before, cut1 = value_cell(change['baseline'])
            after, cut2 = value_cell(change['candidate'])
            shortened = shortened or cut0 or cut1 or cut2
            lines.append('| {} | {} | {} | {} | {} |'.format(name, ident, before, after, ', '.join(change['categories'])))
        lines.append('')
    if equal:
        lines += ['## Equal worksheets', '']
        lines += ['- Pair {}: baseline {} -> candidate {}, {}.'.format(i, p['baseline']['index'], p['candidate']['index'],
                                                                     label(p['baseline'])) for i, p in equal]
        lines.append('')
    lines += ['## Unresolved worksheets', '']
    if not result['unresolved']:
        lines.append('None.')
    for entry in result['unresolved']:
        w = entry['worksheet']
        partners = ', '.join(str(n) for n in entry['possible_partner_indexes']) or 'none'
        lines.append('- {} {}, {}: {}. Possible partners: {}. Identity: {}.'.format(
            entry['side'].capitalize(), w['index'], label(w), '; '.join(REASONS[r] for r in entry['reasons']),
            partners, where(w)))
    lines += ['', 'Indexes are zero-based positions in each input. Pair numbers are positions in `pairs`. '
              'Unresolved worksheets are not additions or removals. No business impact is inferred.']
    if shortened:
        lines.append('Cells marked "(shortened)" show the start of a long value; the appendix has the exact value.')
    text = dump(result)
    fence = '`' * max(3, 1 + max((len(m) for m in re.findall(r'`+', text)), default=0))
    lines += ['', '## Appendix: comparison JSON', '',
              'The exact comparison result, including identifier names and input hashes.', '',
              fence + 'json', text.rstrip('\n'), fence]
    return '\n'.join(lines) + '\n'


# ---- protected publication -------------------------------------------------

def local_path(value, label):
    try:
        path = Path(value).expanduser().absolute()
        return path, path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise CompareError('path_error', 'Cannot resolve {}: {}'.format(label, exc)) from exc


def same_file(a, b):
    (pa, ra), (pb, rb) = local_path(a, 'path'), local_path(b, 'path')
    try:
        return ra == rb or (pa.exists() and pb.exists() and os.path.samefile(pa, pb))
    except OSError as exc:
        raise CompareError('path_error', str(exc)) from exc


def protect(sources, destinations):
    for i, destination in enumerate(destinations):
        if any(same_file(source, destination) for source in sources):
            raise CompareError('input_output_same', 'Destination {} aliases an input, even with --overwrite'.format(destination))
        if any(same_file(other, destination) for other in destinations[:i]):
            raise CompareError('input_output_same', 'The comparison output and the report must be different files')


def snapshot(destination):
    """Remember what a destination held so a failed multi-file publish can put it back."""
    if not os.path.lexists(destination):
        return None
    if os.path.islink(destination):
        return ('link', os.readlink(destination))
    backup = Path(tempfile.mktemp(prefix='.worksheet-compare-', suffix='.bak', dir=str(destination.parent)))
    os.link(str(destination), str(backup))
    return ('file', backup)


def restore(destination, saved):
    if saved is None:
        os.unlink(str(destination))
    elif saved[0] == 'link':
        os.unlink(str(destination))
        os.symlink(saved[1], str(destination))
    else:
        os.replace(str(saved[1]), str(destination))


def publish(files, sources, overwrite):
    """Publish every (text, destination) atomically and all-or-none; return cleanup warnings."""
    temporaries, published, leftovers, warnings = [], [], [], []
    try:
        for text, destination in files:
            if os.path.lexists(destination) and not overwrite:
                raise CompareError('output_exists', '{} exists; choose a new path or explicitly use --overwrite'.format(destination))
        for text, destination in files:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=str(destination.parent),
                                             prefix='.worksheet-compare-', suffix='.tmp', delete=False) as stream:
                temporaries.append(Path(stream.name))
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        protect(sources, [d for _, d in files])
        try:
            for temporary, (_, destination) in zip(temporaries, files):
                saved = snapshot(destination) if overwrite else None
                if saved and saved[0] == 'file':
                    leftovers.append(saved[1])
                if overwrite:
                    os.replace(str(temporary), str(destination))
                else:
                    os.link(str(temporary), str(destination))
                published.append((destination, saved))
        except BaseException:
            for destination, saved in reversed(published):
                try:
                    restore(destination, saved)
                except OSError as exc:
                    warnings.append('Cannot restore {} after a failed publish: {}'.format(destination, exc))
            raise
    except FileExistsError as exc:
        raise CompareError('output_exists', 'Destination exists; choose a new path or explicitly use --overwrite') from exc
    except OSError as exc:
        raise CompareError('output_write', 'Cannot publish: {}'.format(exc)) from exc
    finally:
        for path in temporaries + leftovers:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                warnings.append('Cannot remove temporary file {}: {}'.format(path, exc))
    return warnings


def run(baseline, candidate, destination, overwrite=False, report=None):
    sources = [local_path(p, 'input')[0] for p in (baseline, candidate)]
    destinations = [local_path(p, 'output')[0] for p in ([destination] + ([report] if report else []))]
    protect(sources, destinations)
    left, a = read_input(sources[0])
    right, b = read_input(sources[1])
    result = compare(left, right, {'baseline': a, 'candidate': b})
    texts = [dump(result)] + ([render(result)] if report else [])
    warnings = publish(list(zip(texts, destinations)), sources, overwrite)
    status = {'status': result['outcome'], 'output': str(destinations[0]), 'summary': result['summary']}
    if report:
        status['report'] = str(destinations[1])
    if warnings:
        status['warnings'] = warnings
    return status, 0 if result['complete'] else 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, help='Baseline final-values v3 JSON')
    parser.add_argument('--candidate', required=True, help='Candidate final-values v3 JSON')
    parser.add_argument('--output', required=True, help='Destination comparison JSON; parent must exist')
    parser.add_argument('--report', help='Also write a Markdown report here; parent must exist')
    parser.add_argument('--overwrite', action='store_true', help='Explicitly replace existing output and report')
    args = parser.parse_args()
    try:
        status, code = run(args.baseline, args.candidate, args.output, args.overwrite, args.report)
    except (CompareError, RecursionError, UnicodeError, ValueError) as exc:
        print(json.dumps({'status': 'error', 'category': getattr(exc, 'category', 'invalid_input'), 'message': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(status, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
