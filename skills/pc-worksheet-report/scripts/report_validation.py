"""Validate recorded comparison-v1 evidence; never open or repair upstream inputs."""
from report_contract import (CATEGORIES, METADATA, ROUTINE, final_value,
                             identifier, key, require, shape, string)

SIDES = ('baseline', 'candidate')
POLICY = 'exact-reference-tag-interval-routine-v1'
LIMITS = [
    'Caller assumes different retained runs of the same job and quote branch; inputs do not attest this.',
    'Only exact qualified reference, Tag presence/value, raw interval and nonempty RoutineCode establish correspondence.',
    'Unresolved worksheets are not inferred additions/removals, replacements, splits or merges.',
    'Opaque text and receivers describe recorded representations, not underlying object equality.',
    'Category counts overlap; changed_identifiers counts each changed identity once within established pairs.',
]


def same(actual, expected, path):
    require(key(actual) == key(expected), path, 'inconsistent with recorded evidence')


def count(value, path):
    require(type(value) is int and value >= 0, path, 'expected nonnegative integer')


def array(value, path):
    require(isinstance(value, list), path, 'expected array')


def presence(mapping, name):
    return {'present': True, 'value': mapping[name]} if name in mapping else {'present': False}


def worksheet(value, path):
    shape(value, {'index', 'metadata', 'routine', 'identifiers'}, set(), path)
    count(value['index'], path + '/index')
    for group, allowed in [('metadata', METADATA), ('routine', ROUTINE)]:
        shape(value[group], set(), allowed, path + '/' + group)
        for name, raw in value[group].items():
            string(raw, path + '/' + group + '/' + name)
    array(value['identifiers'], path + '/identifiers')
    records = {}
    for i, record in enumerate(value['identifiers']):
        rp = path + '/identifiers/' + str(i)
        shape(record, {'identifier', 'value'}, {'receiver'}, rp)
        identifier(record['identifier'], rp + '/identifier')
        ident = key(record['identifier'])
        require(ident not in records, rp, 'duplicate structured identifier')
        records[ident] = record
        final_value(record['value'], rp + '/value')
        if 'receiver' in record:
            require(record['identifier']['kind'] == 'property', rp, 'receiver requires property')
            receiver = record['receiver']
            shape(receiver, {'type', 'value', 'opaque'}, set(), rp + '/receiver')
            string(receiver['value'], rp + '/receiver/value')
            require(receiver['opaque'] is True and receiver['type'] == record['identifier']['object']['type'],
                    rp, 'receiver must be opaque with the property object type')
    return records


def identity(w):
    m, r = w['metadata'], w['routine']
    fixed = m.get('FixedId')
    qualified = fixed is not None and len(fixed.split(':')) == 2 and all(p.strip() for p in fixed.split(':'))
    return {'FixedId': fixed if qualified else None, 'Tag': presence(m, 'Tag'),
            'EffectiveDate': m.get('EffectiveDate') or None,
            'ExpirationDate': m.get('ExpirationDate') or None,
            'RoutineCode': r.get('RoutineCode') or None}


def categories(before, after):
    if before is None or after is None:
        return ['added' if before is None else 'removed']
    a, b = before['value'], after['value']
    result = []
    if a['kind'] != b['kind'] or key(a['value']) != key(b['value']):
        result.append('value')
    if a['kind'] != b['kind'] or a['type'] != b['type']:
        result.append('type')
    if (a['kind'] == 'opaque' or b['kind'] == 'opaque') and (a['kind'] != b['kind'] or a['value'] != b['value']):
        result.append('opaque_text')
    if presence(before, 'receiver') != presence(after, 'receiver'):
        result.append('receiver')
    return result


def validate(doc):
    shape(doc, {'format', 'version', 'policy', 'outcome', 'complete', 'inputs', 'summary', 'pairs', 'unresolved', 'limits'}, set(), '/')
    require(doc['format'] == 'pc-worksheet-comparison' and type(doc['version']) is int and doc['version'] == 1,
            '/version', 'supported result is pc-worksheet-comparison version 1')
    require(doc['policy'] == POLICY, '/policy', 'unsupported comparison policy')
    shape(doc['inputs'], set(SIDES), set(), '/inputs')
    for side in SIDES:
        inp = doc['inputs'][side]
        p = '/inputs/' + side
        shape(inp, {'format', 'version', 'sha256', 'worksheet_count', 'identifier_count'}, set(), p)
        require(inp['format'] == 'pc-worksheet-final-values' and type(inp['version']) is int and inp['version'] == 2,
                p, 'normalized input provenance must declare version 2')
        string(inp['sha256'], p + '/sha256')
        require(len(inp['sha256']) == 64 and all(c in '0123456789abcdef' for c in inp['sha256']), p, 'invalid SHA-256')
        for field in ('worksheet_count', 'identifier_count'):
            count(inp[field], p + '/' + field)
        require(inp['worksheet_count'] > 0, p, 'inputs must contain worksheets')
    array(doc['pairs'], '/pairs')
    array(doc['unresolved'], '/unresolved')
    array(doc['limits'], '/limits')
    for i, limit in enumerate(doc['limits']):
        string(limit, '/limits/' + str(i), True)
    require(set(LIMITS) <= set(doc['limits']), '/limits', 'missing supported policy limitations')
    inventories = {s: {} for s in SIDES}
    records = {}

    def register(side, w, path):
        records[path] = worksheet(w, path)
        require(w['index'] not in inventories[side], path, 'worksheet index reused')
        inventories[side][w['index']] = w

    totals = dict(established_pairs=len(doc['pairs']), changed_pairs=0, compared_identifiers=0,
                  changed_identifiers=0, changed_context_fields=0,
                  identifier_categories={c: 0 for c in CATEGORIES}, unresolved_baseline=0, unresolved_candidate=0)
    for i, pair in enumerate(doc['pairs']):
        p = '/pairs/' + str(i)
        shape(pair, {'baseline', 'candidate', 'outcome', 'context_changes', 'identifier_changes'}, set(), p)
        for side in SIDES:
            register(side, pair[side], p + '/' + side)
        array(pair['context_changes'], p + '/context_changes')
        array(pair['identifier_changes'], p + '/identifier_changes')
        contexts = {}
        for group in ('metadata', 'routine'):
            a, b = pair['baseline'][group], pair['candidate'][group]
            for field in set(a) | set(b):
                before, after = presence(a, field), presence(b, field)
                if before != after:
                    contexts[(group, field)] = {'category': 'metadata_routine', 'group': group, 'field': field,
                                                'baseline': before, 'candidate': after}
        seen = set()
        for j, change in enumerate(pair['context_changes']):
            cp = p + '/context_changes/' + str(j)
            shape(change, {'category', 'group', 'field', 'baseline', 'candidate'}, set(), cp)
            string(change['group'], cp + '/group')
            string(change['field'], cp + '/field')
            ident = (change['group'], change['field'])
            require(ident in contexts and ident not in seen, cp, 'unknown, unchanged or duplicate context field')
            same(change, contexts[ident], cp)
            seen.add(ident)
        require(seen == set(contexts), p + '/context_changes', 'missing recorded context changes')
        a, b = (records[p + '/' + side] for side in SIDES)
        changed = {ident: categories(a.get(ident), b.get(ident)) for ident in set(a) | set(b)}
        changed = {ident: cats for ident, cats in changed.items() if cats}
        seen = set()
        for j, change in enumerate(pair['identifier_changes']):
            cp = p + '/identifier_changes/' + str(j)
            shape(change, {'identifier', 'categories', 'baseline', 'candidate'}, set(), cp)
            identifier(change['identifier'], cp + '/identifier')
            ident = key(change['identifier'])
            require(ident in changed and ident not in seen, cp, 'unknown, unchanged or duplicate identifier change')
            same(change['baseline'], a.get(ident), cp + '/baseline')
            same(change['candidate'], b.get(ident), cp + '/candidate')
            same(change['categories'], changed[ident], cp + '/categories')
            seen.add(ident)
            for category in changed[ident]:
                totals['identifier_categories'][category] += 1
        require(seen == set(changed), p + '/identifier_changes', 'missing recorded identifier changes')
        different = bool(contexts or changed)
        same(pair['outcome'], 'different' if different else 'equal', p + '/outcome')
        totals['changed_pairs'] += int(different)
        totals['compared_identifiers'] += len(set(a) | set(b))
        totals['changed_identifiers'] += len(changed)
        totals['changed_context_fields'] += len(contexts)
    for i, entry in enumerate(doc['unresolved']):
        p = '/unresolved/' + str(i)
        shape(entry, {'side', 'worksheet', 'reasons', 'unavailable_identity_fields', 'possible_partner_indexes'}, set(), p)
        require(isinstance(entry['side'], str) and entry['side'] in SIDES, p, 'invalid side')
        register(entry['side'], entry['worksheet'], p + '/worksheet')
        totals['unresolved_' + entry['side']] += 1
        for field in ('reasons', 'unavailable_identity_fields', 'possible_partner_indexes'):
            array(entry[field], p + '/' + field)
        for value in entry['possible_partner_indexes']:
            count(value, p + '/possible_partner_indexes')
    for side in SIDES:
        inp = doc['inputs'][side]
        inv = inventories[side]
        same(len(inv), inp['worksheet_count'], '/inputs/' + side + '/worksheet_count')
        same(sorted(inv), list(range(len(inv))), '/inputs/' + side + '/worksheet_count')
        same(sum(len(w['identifiers']) for w in inv.values()), inp['identifier_count'], '/inputs/' + side + '/identifier_count')

    # Check declared correspondence against embedded evidence only. No new pairs
    # are returned, no source is reopened, and discrepancies are never repaired.
    ids = {s: {i: identity(w) for i, w in inventories[s].items()} for s in SIDES}
    possible = {s: {i: [] for i in ids[s]} for s in SIDES}
    for i, a in sorted(ids['baseline'].items()):
        for j, b in sorted(ids['candidate'].items()):
            if all(a[k] is None or b[k] is None or a[k] == b[k] for k in a):
                possible['baseline'][i].append(j)
                possible['candidate'][j].append(i)
    for i, pair in enumerate(doc['pairs']):
        a, b = pair['baseline']['index'], pair['candidate']['index']
        require(all(v is not None for v in ids['baseline'][a].values()) and
                all(v is not None for v in ids['candidate'][b].values()) and
                possible['baseline'][a] == [b] and possible['candidate'][b] == [a],
                '/pairs/' + str(i), 'declared pair violates exact unique correspondence policy')
    for i, entry in enumerate(doc['unresolved']):
        p = '/unresolved/' + str(i)
        side, index = entry['side'], entry['worksheet']['index']
        other = 'candidate' if side == 'baseline' else 'baseline'
        missing = sorted(k for k, v in ids[side][index].items() if v is None)
        partners = possible[side][index]
        same(entry['unavailable_identity_fields'], missing, p + '/unavailable_identity_fields')
        same(entry['possible_partner_indexes'], partners, p + '/possible_partner_indexes')
        reasons = ['insufficient_identity'] if missing else []
        if not partners:
            reasons.append('no_exact_partner')
        elif len(partners) > 1 or any(len(possible[other][j]) > 1 for j in partners):
            reasons.append('ambiguous_correspondence')
        elif not missing:
            require(any(v is None for v in ids[other][partners[0]].values()), p, 'unresolved entry has complete unique correspondence')
            reasons.append('partner_has_insufficient_identity')
        same(entry['reasons'], reasons, p + '/reasons')
    same(doc['summary'], totals, '/summary')
    same(doc['complete'], not doc['unresolved'], '/complete')
    same(doc['outcome'], 'incomplete' if doc['unresolved'] else 'different' if totals['changed_pairs'] else 'equal', '/outcome')
    return doc
