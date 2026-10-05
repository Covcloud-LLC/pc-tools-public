#!/usr/bin/env python3
"""Compare two PC final-values v2 captures (Python 3.8+ stdlib)."""
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


NUMERIC = {'byte', 'short', 'int', 'long', 'float', 'double',
           'java.lang.Byte', 'java.lang.Short', 'java.lang.Integer', 'java.lang.Long',
           'java.lang.Float', 'java.lang.Double', 'java.math.BigInteger', 'java.math.BigDecimal'}
STRINGS = {'java.lang.String', 'java.lang.Character', 'char'}
BOOLEANS = {'boolean', 'java.lang.Boolean'}
METADATA = {'FixedId', 'Tag', 'EffectiveDate', 'ExpirationDate', 'Description'}
ROUTINE = {'RateBookCode', 'RateBookEdition', 'RoutineCode', 'RoutineVersion'}
CATEGORIES = ('added', 'removed', 'value', 'type', 'opaque_text', 'receiver')
DECIMAL = re.compile(r'(?:0|-?(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9]))\Z')


def key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


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


def object_identity(value, path):
    shape(value, {'name', 'type'}, set(), path)
    for field in value:
        string(value[field], path + '.' + field, True)


def atom(value, path):
    require(isinstance(value, dict), path, 'expected identity object')
    kind = value.get('kind')
    fields = {'function': {'kind', 'name', 'class'}, 'method': {'kind', 'name', 'object'},
              'query': {'kind', 'table', 'factor', 'source'},
              'argument': {'kind', 'name'}, 'parameter': {'kind', 'name'}}
    require(isinstance(kind, str) and kind in fields, path, 'unsupported context kind')
    shape(value, fields[kind], set(), path)
    for field in fields[kind] - {'kind', 'object'}:
        string(value[field], path + '.' + field, field != 'source')
    if 'object' in value:
        object_identity(value['object'], path + '.object')


def context(value, path, owner=None):
    require(isinstance(value, list), path, 'expected context array')
    for i, item in enumerate(value):
        atom(item, '{}[{}]'.format(path, i))
    prefix = value
    if owner:
        require(bool(value) and value[-1]['kind'] in owner, path, 'missing appropriate owning call/query')
        prefix = value[:-1]
        if value[-1]['kind'] == 'method':
            require(not prefix, path, 'method must be at routine scope')
    require(len(prefix) % 2 == 0, path, 'expected enclosing call/named-input pairs')
    for i in range(0, len(prefix), 2):
        require(prefix[i]['kind'] in {'function', 'method'} and prefix[i + 1]['kind'] == 'argument', path,
                'invalid enclosing call/named-input sequence')
        require(prefix[i]['kind'] != 'method' or i == 0, path, 'method must be at routine scope')


def identifier(value, path):
    require(isinstance(value, dict), path, 'expected identifier object')
    kind = value.get('kind')
    fields = {'variable': {'kind', 'name'}, 'property': {'kind', 'name', 'object'},
              'function': {'kind', 'class', 'name', 'context'},
              'query': {'kind', 'table', 'factor', 'source', 'context'},
              'argument': {'kind', 'name', 'context'}, 'parameter': {'kind', 'name', 'context'}}
    require(isinstance(kind, str) and kind in fields, path, 'unsupported identifier kind')
    shape(value, fields[kind], set(), path)
    for field in fields[kind] - {'kind', 'object', 'context'}:
        string(value[field], path + '.' + field, field != 'source')
    if 'object' in value:
        object_identity(value['object'], path + '.object')
    if 'context' in value:
        owner = {'argument': {'function', 'method'}, 'parameter': {'query'}}.get(kind)
        context(value['context'], path + '.context', owner)


def final_value(value, path):
    shape(value, {'kind', 'type', 'value'}, {'opaque'}, path)
    kind, recorded_type, raw = value['kind'], value['type'], value['value']
    require(recorded_type is None or isinstance(recorded_type, str), path, 'type must be string or null')
    if recorded_type is not None:
        string(recorded_type, path + '.type')
    require(isinstance(kind, str) and kind in {'null', 'number', 'string', 'boolean', 'opaque'}, path, 'unsupported value kind')
    require(('opaque' in value) == (kind == 'opaque'), path, 'opaque marker allowed and required only for opaque values')
    if kind == 'null':
        require(raw is None, path, 'null kind requires JSON null')
        return
    expected = ('number' if recorded_type in NUMERIC else 'boolean' if recorded_type in BOOLEANS
                else 'string' if recorded_type is None or recorded_type in STRINGS else 'opaque')
    require(kind == expected, path, 'kind does not match recorded type')
    if kind == 'boolean':
        require(type(raw) is bool, path, 'boolean requires JSON boolean')
    else:
        string(raw, path + '.value')
        if kind == 'number':
            require(bool(DECIMAL.fullmatch(raw)) and len(raw.replace('-', '').replace('.', '')) <= 100000,
                    path, 'number requires canonical finite decimal text, at most 100000 digits')
        if kind == 'opaque':
            require(value['opaque'] is True, path, 'opaque marker must be true')


def validate(document):
    shape(document, {'format', 'version', 'worksheets'}, set(), '$')
    if document['format'] != 'pc-worksheet-final-values' or type(document['version']) is not int or document['version'] != 2:
        raise CompareError('unsupported_version', 'Both inputs must be pc-worksheet-final-values version 2; no v1 or POC envelopes')
    require(isinstance(document['worksheets'], list) and bool(document['worksheets']), '$.worksheets', 'expected nonempty array')
    for index, worksheet in enumerate(document['worksheets']):
        path = '$.worksheets[{}]'.format(index)
        shape(worksheet, {'metadata', 'routine', 'identifiers'}, set(), path)
        for field, allowed in [('metadata', METADATA), ('routine', ROUTINE)]:
            shape(worksheet[field], set(), allowed, path + '.' + field)
            for name, value in worksheet[field].items():
                string(value, path + '.' + field + '.' + name)
        require(isinstance(worksheet['identifiers'], list), path, 'identifiers must be array')
        seen = set()
        for i, record in enumerate(worksheet['identifiers']):
            rp = path + '.identifiers[{}]'.format(i)
            shape(record, {'identifier', 'value'}, {'receiver'}, rp)
            identifier(record['identifier'], rp + '.identifier')
            identity = key(record['identifier'])
            require(identity not in seen, rp, 'duplicate final structured identifier')
            seen.add(identity)
            final_value(record['value'], rp + '.value')
            if 'receiver' in record:
                require(record['identifier']['kind'] == 'property', rp, 'receiver requires property identifier')
                receiver = record['receiver']
                shape(receiver, {'type', 'value', 'opaque'}, set(), rp + '.receiver')
                string(receiver['value'], rp + '.receiver.value')
                require(receiver['type'] == record['identifier']['object']['type'] and receiver['opaque'] is True,
                        rp, 'receiver type must equal property object type and opaque must be true')
    return document


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise CompareError('invalid_input', 'Duplicate JSON key: ' + name)
        result[name] = value
    return result


def read_input(path):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompareError('input_read', 'Cannot read {}: {}'.format(path, exc)) from exc
    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                              parse_constant=lambda value: invalid_constant(value))
        validate(document)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CompareError('invalid_input', 'Invalid JSON/encoding/nesting: {}'.format(exc)) from exc
    return document, {'sha256': hashlib.sha256(raw).hexdigest(), 'format': document['format'], 'version': 2,
                      'worksheet_count': len(document['worksheets']),
                      'identifier_count': sum(len(w['identifiers']) for w in document['worksheets'])}


def invalid_constant(value):
    raise CompareError('invalid_input', 'Non-JSON constant: ' + value)


def identity(worksheet):
    metadata = worksheet['metadata']
    fixed = metadata.get('FixedId')
    # Producer preserves arbitrary text. Unusable qualification is uncertainty,
    # never a reason to discard a valid document or guess a source namespace.
    qualified = isinstance(fixed, str) and len(fixed.split(':')) == 2 and all(p.strip() for p in fixed.split(':'))
    fields = {'FixedId': fixed if qualified else None,
              'Tag': (('present', metadata['Tag']) if 'Tag' in metadata else ('absent',)),
              'EffectiveDate': metadata.get('EffectiveDate') or None,
              'ExpirationDate': metadata.get('ExpirationDate') or None,
              'RoutineCode': worksheet['routine'].get('RoutineCode') or None}
    return fields


def compatible(left, right):
    return all(left[name] is None or right[name] is None or left[name] == right[name] for name in left)


def evidence(index, worksheet):
    return {'index': index, 'metadata': worksheet['metadata'], 'routine': worksheet['routine'],
            'identifiers': sorted(worksheet['identifiers'], key=lambda r: key(r['identifier']))}


def presence(mapping, name):
    return {'present': True, 'value': mapping[name]} if name in mapping else {'present': False}


def compare_pair(left, right):
    context_changes = []
    for group in ('metadata', 'routine'):
        for field in sorted(set(left[group]) | set(right[group])):
            before, after = presence(left[group], field), presence(right[group], field)
            if before != after:
                context_changes.append({'category': 'metadata_routine', 'group': group, 'field': field,
                                        'baseline': before, 'candidate': after})
    a = {key(r['identifier']): r for r in left['identifiers']}
    b = {key(r['identifier']): r for r in right['identifiers']}
    changes = []
    for ident in sorted(set(a) | set(b)):
        before, after = a.get(ident), b.get(ident)
        categories = []
        if before is None or after is None:
            categories.append('added' if before is None else 'removed')
        else:
            av, bv = before['value'], after['value']
            if av['kind'] != bv['kind'] or key(av['value']) != key(bv['value']):
                categories.append('value')
            if av['kind'] != bv['kind'] or av['type'] != bv['type']:
                categories.append('type')
            if (av['kind'] == 'opaque' or bv['kind'] == 'opaque') and (av['kind'] != bv['kind'] or av['value'] != bv['value']):
                categories.append('opaque_text')
            if presence(before, 'receiver') != presence(after, 'receiver'):
                categories.append('receiver')
        if categories:
            changes.append({'identifier': (before or after)['identifier'], 'categories': categories,
                            'baseline': before, 'candidate': after})
    return context_changes, changes


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
        if len(neighbors[1][j]) != 1 or any(v is None for v in identities[0][i].values()) or any(v is None for v in identities[1][j].values()):
            continue
        left, right = sides[0][i], sides[1][j]
        contexts, changes = compare_pair(left, right)
        pairs.append({'baseline': evidence(i, left), 'candidate': evidence(j, right),
                      'context_changes': contexts, 'identifier_changes': changes,
                      'outcome': 'different' if contexts or changes else 'equal'})
        established[0].add(i)
        established[1].add(j)
        changed_context += len(contexts)
        changed_pairs += bool(contexts or changes)
        changed_identifiers += len(changes)
        compared_identifiers += len({key(r['identifier']) for r in left['identifiers'] + right['identifiers']})
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
            unresolved.append({'side': label, 'worksheet': evidence(i, worksheet), 'reasons': reasons,
                               'unavailable_identity_fields': missing, 'possible_partner_indexes': possible})
    return {'format': 'pc-worksheet-comparison', 'version': 1,
            'policy': 'exact-reference-tag-interval-routine-v1',
            'outcome': 'incomplete' if unresolved else 'different' if changed_pairs else 'equal',
            'complete': not unresolved, 'inputs': inputs,
            'summary': {'established_pairs': len(pairs), 'changed_pairs': changed_pairs,
                        'compared_identifiers': compared_identifiers, 'changed_identifiers': changed_identifiers,
                        'changed_context_fields': changed_context, 'identifier_categories': counts,
                        'unresolved_baseline': len(sides[0]) - len(established[0]),
                        'unresolved_candidate': len(sides[1]) - len(established[1])},
            'pairs': pairs, 'unresolved': unresolved,
            'limits': ['Caller assumes different retained runs of the same job and quote branch; inputs do not attest this.',
                       'Only exact qualified reference, Tag presence/value, raw interval and nonempty RoutineCode establish correspondence.',
                       'Unresolved worksheets are not inferred additions/removals, replacements, splits or merges.',
                       'Opaque text and receivers describe recorded representations, not underlying object equality.',
                       'Category counts overlap; changed_identifiers counts each changed identity once within established pairs.']}


def local_path(value, label):
    try:
        path = Path(value).expanduser().absolute()
        return path, path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise CompareError('path_error', 'Cannot resolve {}: {}'.format(label, exc)) from exc


def protect(inputs, destination):
    dest, resolved = local_path(destination, 'output')
    for source in inputs:
        path, source_resolved = local_path(source, 'input')
        try:
            same = resolved == source_resolved or (dest.exists() and path.exists() and os.path.samefile(path, dest))
        except OSError as exc:
            raise CompareError('path_error', str(exc)) from exc
        if same:
            raise CompareError('input_output_same', 'Destination aliases an input, even with --overwrite')
    return dest


def save_json(result, destination, overwrite, sources):
    temporary = None
    warning = None
    try:
        protect(sources, destination)
        if os.path.lexists(destination) and not overwrite:
            raise CompareError('output_exists', 'Destination exists; choose a new path or explicitly use --overwrite')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=str(destination.parent),
                                         prefix='.worksheet-compare-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        protect(sources, destination)
        if overwrite:
            os.replace(str(temporary), str(destination))
        else:
            os.link(str(temporary), str(destination))
    except FileExistsError as exc:
        raise CompareError('output_exists', 'Destination exists; choose a new path or explicitly use --overwrite') from exc
    except OSError as exc:
        raise CompareError('output_write', 'Cannot publish {}: {}'.format(destination, exc)) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                warning = 'Cannot remove temporary file {}: {}'.format(temporary, exc)
    return warning


def run(baseline, candidate, destination, overwrite=False):
    sources = [local_path(p, 'input')[0] for p in (baseline, candidate)]
    destination = protect(sources, destination)
    left, a = read_input(sources[0])
    right, b = read_input(sources[1])
    result = compare(left, right, {'baseline': a, 'candidate': b})
    warning = save_json(result, destination, overwrite, sources)
    status = {'status': result['outcome'], 'output': str(destination), 'summary': result['summary']}
    if warning:
        status['warnings'] = [warning]
    return status, 0 if result['complete'] else 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, help='Baseline final-values v2 JSON')
    parser.add_argument('--candidate', required=True, help='Candidate final-values v2 JSON')
    parser.add_argument('--output', required=True, help='Destination JSON; parent must exist')
    parser.add_argument('--overwrite', action='store_true', help='Explicitly replace existing output')
    args = parser.parse_args()
    try:
        status, code = run(args.baseline, args.candidate, args.output, args.overwrite)
    except (CompareError, RecursionError, UnicodeError, ValueError) as exc:
        print(json.dumps({'status': 'error', 'category': getattr(exc, 'category', 'invalid_input'), 'message': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(status, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
