# Copyright (c) 2026 Covcloud LLC. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Covcloud-Proprietary
"""Single, offline validation authority for native product captures (Python 3.8+)."""
import json
import re
from pathlib import Path

FORMAT = 'pc-tools.product-capture/1.0.0'
KINDS = ['coverage', 'condition', 'exclusion']
TIERS = ['mandatory', 'electable', 'scripted']
EXCLUSIONS = ['availability', 'scripts', 'questionContents', 'templateContents', 'ratingExecution']
FIELDS = {'column', 'typekey', 'monetaryamount', 'foreignkey', 'onetoone', 'edgeForeignKey', 'array'}
ENTITY_ROOTS = {'entity', 'subtype', 'delegate', 'viewEntity', 'nonPersistentEntity'}
# Platform/language releases whose typelist loader is known to match names ignoring ASCII case.
CASE_RULE_RELEASE = {'platform-version': '10.203.1', 'gosu-version': '1.17.4'}


def case_rule_applies(release):
    return all(release.get(key) == value for key, value in CASE_RULE_RELEASE.items())


class InvalidCapture(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidCapture('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream, object_pairs_hook=unique_object,
                         parse_constant=lambda value: (_ for _ in ()).throw(InvalidCapture('invalid JSON constant: ' + value)))


def check_shape(value, schema, root, pointer=''):
    """Evaluate only the JSON Schema keywords used by our shipped local schema."""
    if '$ref' in schema:
        target = root
        for part in schema['$ref'].split('/')[1:]:
            target = target[part]
        return check_shape(value, target, root, pointer)
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool, 'null': type(None)}
    expected = schema.get('type')
    if expected:
        names = expected if isinstance(expected, list) else [expected]
        if not any(type(value) is types[name] for name in names):
            raise InvalidCapture('%s: expected %s' % (pointer or '/', expected))
    if 'const' in schema and value != schema['const']:
        raise InvalidCapture('%s: expected %r' % (pointer, schema['const']))
    if 'enum' in schema and value not in schema['enum']:
        raise InvalidCapture('%s: invalid value %r' % (pointer, value))
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                raise InvalidCapture('%s: missing %s' % (pointer, key))
        props = schema.get('properties', {})
        extra = schema.get('additionalProperties', True)
        for key, item in value.items():
            child = props.get(key, extra)
            if child is False:
                raise InvalidCapture('%s: unknown property %s' % (pointer, key))
            if isinstance(child, dict):
                check_shape(item, child, root, pointer + '/' + key)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            raise InvalidCapture(pointer + ': too few items')
        if schema.get('uniqueItems') and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            raise InvalidCapture(pointer + ': duplicate items')
        for i, item in enumerate(value):
            check_shape(item, schema.get('items', {}), root, pointer + '/' + str(i))
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or ('pattern' in schema and not re.search(schema['pattern'], value)):
            raise InvalidCapture(pointer + ': invalid string')
    if type(value) is int and value < schema.get('minimum', value):
        raise InvalidCapture(pointer + ': below minimum')


def walk(node):
    yield node
    for ch in node['children']:
        yield from walk(ch)


def descendants(node, kind):
    return [x for x in walk(node) if x['kind'] == kind]


def members(entity):
    for declaration in entity['declarations']:
        yield from declaration['children']


def parents(entity):
    result = []
    for d in entity['declarations']:
        if d['attributes'].get('supertype'):
            result.append(d['attributes']['supertype'])
        result.extend(n['attributes'].get('name', '') for n in d['children'] if n['kind'] == 'implementsEntity')
    return result


def entity_closure(name, index):
    seen, todo = set(), [name]
    while todo:
        item = todo.pop()
        if item in seen or item not in index:
            continue
        seen.add(item)
        todo.extend(parents(index[item]))
    return seen


def collection_counts(c):
    return {
        'offerings': len(c['product']['offerings']),
        'clauses': len(c['policyLinePattern']['clauses']),
        'terms': sum(len(descendants(n, 'CovTerms')[0]['children']) if descendants(n, 'CovTerms') else 0 for n in c['policyLinePattern']['clauses']),
        'entities': len(c['entities']),
        'fields': sum(n['kind'] in FIELDS for e in c['entities'] for n in members(e)),
        'typeLists': len(c['typeLists']),
        'typecodeDeclarations': sum(len(descendants(d, 'typecode')) for t in c['typeLists'] for d in t['declarations']),
        'costCodes': len(c['costCodes'])}


def validate(c, check_state=True):
    schema = read_json(Path(__file__).resolve().parent.parent / 'schema/product-capture.schema.json')
    check_shape(c, schema, schema)
    diagnostics = list(c['diagnostics'])
    def error(code, message, node=None, pointer=''):
        d = {'code': code, 'severity': 'error', 'message': message, 'pointer': pointer}
        if node and node.get('sourceRefs'):
            d['sourceRefs'] = node['sourceRefs']
        if d not in diagnostics:
            diagnostics.append(d)
    def refs(node):
        for r in node.get('sourceRefs', []):
            path = r['path']
            if path.startswith('/') or '\\' in path or '..' in path.split('/'):
                error('SOURCE_REFERENCE', 'Source path must be source-relative: ' + path, node)
    for d in c['diagnostics']:
        refs(d)
    def inspect(root, family):
        vocabulary = schema['x-nativeDeclarations'][family]
        for n in walk(root):
            refs(n)
            for resolution in n.get('resolution', {}).values():
                refs(resolution)
            spec = vocabulary.get(n['kind'])
            if spec is None:
                error('UNSUPPORTED_DECLARATION', 'Unsupported native declaration ' + n['kind'], n)
                continue
            unknown = set(n['attributes']) - set(spec['attributes'])
            if unknown:
                error('UNSUPPORTED_ATTRIBUTE', n['kind'] + ': unsupported attributes ' + ', '.join(sorted(unknown)), n)
            for ch in n['children']:
                if ch['kind'] not in spec['children']:
                    error('UNSUPPORTED_MEMBER', n['kind'] + ' cannot contain ' + ch['kind'], ch)
            required = []
            if 'codeIdentifier' in spec['attributes']:
                required = ['codeIdentifier', 'public-id']
            if n['kind'] in FIELDS or n['kind'].endswith('-override'):
                required = ['name']
            required += {'PolicyLinePattern': ['policyLineSubtype'], 'ProductPolicyLinePattern': ['policyLinePattern'],
                         'CoveragePattern': ['owningEntityType', 'coverageSubtype', 'policyLinePattern'],
                         'ConditionPattern': ['owningEntityType', 'conditionSubtype', 'policyLinePattern'],
                         'ExclusionPattern': ['owningEntityType', 'exclusionSubtype', 'policyLinePattern'],
                         'implementsEntity': ['name'], 'implementsInterface': ['iface'],
                         'subtype': ['entity', 'supertype'], 'typecode': ['code'], 'typefilter': ['name'],
                         'array': ['arrayentity'], 'foreignkey': ['fkentity'], 'onetoone': ['fkentity'],
                         'edgeForeignKey': ['fkentity'], 'typekey': ['typelist']}.get(n['kind'], [])
            for key in required:
                if not n['attributes'].get(key):
                    error('MISSING_ATTRIBUTE', n['kind'] + ' requires ' + key, n)
            if any(not x.endswith('Script') for x in n.get('excludedChildren', [])):
                error('EXCLUDED_CONTENT', 'Only script elements may be removed from declaration children', n)
    source = c['source']
    if source['release'].get('public-app-version') != '10.2.3' or not source['release'].get('project-version', '').startswith('10.2.3.'):
        error('UNSUPPORTED_RELEASE', 'Initial support requires observed classic PC 10.2.3 release metadata')
    if source['generationMode'] != 'classic':
        error('UNSUPPORTED_APD', 'APD-generated and visualized-only models are not supported')
    scope = c['scope']
    p, line = c['product']['declaration'], c['policyLinePattern']['declaration']
    if p['kind'] != 'Product' or p['attributes'].get('codeIdentifier') != scope['product']:
        error('PRODUCT_IDENTITY', 'Selected product does not match declaration', p)
    if line['kind'] != 'PolicyLinePattern' or line['attributes'].get('codeIdentifier') != scope['line']:
        error('LINE_IDENTITY', 'Selected line does not match declaration', line)
    if any(n['attributes'].get('apdManaged') in ('true', '1') for n in (p, line)):
        error('UNSUPPORTED_APD', 'apdManaged declaration is unsupported')
    joins = descendants(p, 'ProductPolicyLinePattern')
    join_names = [n['attributes'].get('policyLinePattern') for n in joins]
    if join_names.count(scope['line']) != 1 or len(join_names) != len(set(join_names)):
        error('PRODUCT_LINE_JOIN', 'Product must join selected line exactly once, without duplicate line joins', p)
    if scope['availableProductLines'] != join_names:
        error('PRODUCT_LINE_JOIN', 'Available lines differ from native product joins')
    if scope['exclusions'] != EXCLUSIONS:
        error('EXCLUSIONS', 'Required exclusions must be explicit')
    if not scope['clauseKinds'] or not set(scope['clauseKinds']) <= set(KINDS) or not scope['coverageTiers'] or not set(scope['coverageTiers']) <= set(TIERS):
        error('SCOPE', 'Unknown or empty requested scope')
    narrowed = set(scope['clauseKinds']) != set(KINDS) or set(scope['coverageTiers']) != set(TIERS) or scope['entityClosure'] != 'composition'
    roots = [p, line] + c['policyLinePattern']['clauses'] + c['product']['offerings']
    for n in roots:
        inspect(n, 'product')
    # Each ID is distinct in its native family; code/public-ID cross collisions must not resolve by first match.
    def identities(nodes, label):
        for key in ('codeIdentifier', 'public-id'):
            seen = set()
            for n in nodes:
                value = n['attributes'].get(key)
                if value is not None:
                    if not value or value in seen:
                        error('DUPLICATE_IDENTITY', label + ': duplicate/empty ' + key + ' ' + value, n)
                    seen.add(value)
    identities([n for r in roots for n in walk(r)], 'product declarations')
    clauses = c['policyLinePattern']['clauses']
    for clause in clauses:
        a = clause['attributes']
        if a.get('policyLinePattern') != scope['line']:
            error('CLAUSE_LINE_JOIN', 'Clause names another line', clause)
        kind = {'CoveragePattern': 'coverage', 'ConditionPattern': 'condition', 'ExclusionPattern': 'exclusion'}.get(clause['kind'])
        if kind not in scope['clauseKinds']:
            error('CLAUSE_SCOPE', 'Unexpected clause kind', clause)
        if clause['kind'] == 'CoveragePattern':
            if 'existence' in a:
                expected_tier = {'Required': 'mandatory', 'Suggested': 'electable', 'Electable': 'electable'}.get(a['existence'])
                if expected_tier is None:
                    error('UNSUPPORTED_EXISTENCE', 'Unknown native existence ' + a['existence'], clause)
            else:
                expected_tier = 'scripted'
            if clause.get('tier') != expected_tier or ('existence' not in a and 'ExistenceScript' not in clause.get('excludedChildren', [])):
                error('COVERAGE_TIER', 'Coverage tier does not match retained existence declaration', clause)
        if clause['kind'] == 'CoveragePattern' and clause.get('tier') not in scope['coverageTiers']:
            error('CLAUSE_SCOPE', 'Coverage tier outside scope', clause)
    entities = {e['name']: e for e in c['entities']}
    lists = {t['name']: t for t in c['typeLists']}
    if len(entities) != len(c['entities']) or len(lists) != len(c['typeLists']):
        error('DUPLICATE_METADATA', 'Duplicate entity or typelist identity')
    for e in c['entities']:
        primary = [d for d in e['declarations'] if d['kind'] in ENTITY_ROOTS]
        if len(primary) != 1:
            error('ENTITY_PRIMARY', 'Expected one primary declaration for ' + e['name'])
        for d in e['declarations']:
            inspect(d, 'entity')
            if (d['attributes'].get('entity') or d['attributes'].get('entityName') or d['attributes'].get('name')) != e['name']:
                error('ENTITY_IDENTITY', 'Declaration does not name ' + e['name'], d)
        for target in parents(e):
            if target not in entities:
                error('MISSING_PARENT', e['name'] + ' requires ' + target)
        for n in members(e):
            target = n['attributes'].get('name') if n['kind'] == 'implementsEntity' else None
            if target in entities and not any(d['kind'] == 'delegate' for d in entities[target]['declarations']):
                error('INVALID_DELEGATE', e['name'] + ' implements non-delegate ' + target, n)
        for d in e['declarations']:
            target = d['attributes'].get('supertype')
            if target in entities and any(p['kind'] == 'delegate' for p in entities[target]['declarations']):
                error('INVALID_SUPERTYPE', e['name'] + ' has delegate supertype ' + target, d)
        # Duplicate fields in one layer are ambiguous. Explicit overrides remain ordered raw records.
        for d in e['declarations']:
            names = [n['attributes'].get('name') for n in d['children'] if n['kind'] in FIELDS]
            if len(names) != len(set(names)):
                error('AMBIGUOUS_FIELD', e['name'] + ': repeated field in one declaration', d)
        direct_names = [n['attributes'].get('name') for n in members(e) if n['kind'] in FIELDS]
        if len(direct_names) != len(set(direct_names)):
            error('AMBIGUOUS_FIELD', e['name'] + ': field redeclared across layers without explicit override')
        declared = {}
        for owner in entity_closure(e['name'], entities):
            for n in members(entities[owner]):
                if n['kind'] in FIELDS:
                    declared.setdefault(n['attributes'].get('name'), []).append(n)
        for n in members(e):
            a = n['attributes']
            if n['kind'].endswith('-override'):
                candidates = declared.get(a.get('name'), [])
                if len(candidates) != 1:
                    error('AMBIGUOUS_OVERRIDE', e['name'] + '.' + a.get('name', '') + ': override requires one inherited/base field', n)
            if n['kind'] in FIELDS:
                target = a.get('arrayentity') or a.get('fkentity')
                if target:
                    expected = 'captured' if target in entities else ('narrowed' if n['kind'] in ('array', 'onetoone') and narrowed else 'external')
                    if n.get('referenceStatus') != expected:
                        error('ENTITY_REFERENCE', e['name'] + '.' + a.get('name', '') + ': invalid reference status', n)
                    if n['kind'] in ('array', 'onetoone') and scope['entityClosure'] == 'composition' and target not in entities:
                        error('MISSING_COMPOSITION', 'Required composition target ' + target, n)
                    if expected == 'external' and not n.get('referenceReason'):
                        error('ENTITY_REFERENCE', 'External FK requires a reason', n)
    # Detect supertype and delegate cycles, without looping or choosing one parent.
    active, done = set(), set()
    def visit(name):
        if name in active:
            error('INHERITANCE_CYCLE', 'Cycle at ' + name)
            return
        if name in done or name not in entities:
            return
        active.add(name)
        for parent in parents(entities[name]):
            visit(parent)
        active.remove(name)
        done.add(name)
    for name in entities:
        visit(name)
    subtype_attrs = {'policyLineSubtype', 'owningEntityType', 'coverageSubtype', 'conditionSubtype', 'exclusionSubtype', 'modifierSubtype', 'scheduledItemType', 'rateFactorSubtype'}
    for root in [p, line] + clauses:
        for n in walk(root):
            for key in subtype_attrs:
                if n['attributes'].get(key) and n['attributes'][key] not in entities:
                    error('MISSING_ENTITY', key + ': ' + n['attributes'][key], n)
    for t in c['typeLists']:
        if sum(d['kind'] == 'typelist' for d in t['declarations']) != 1 and not t.get('entityTypes'):
            error('TYPELIST_PRIMARY', 'Expected one primary typelist ' + t['name'])
        for d in t['declarations']:
            inspect(d, 'typelist')
            if d['attributes'].get('name') != t['name']:
                error('TYPELIST_IDENTITY', 'Typelist declaration identity mismatch', d)
            for kind, key in [('typecode', 'code'), ('typefilter', 'name')]:
                values = [n['attributes'].get(key) for n in d['children'] if n['kind'] == kind]
                if len(values) != len(set(values)):
                    error('AMBIGUOUS_TYPELIST_LAYER', t['name'] + ': duplicate ' + kind, d)
        codes = {n['attributes'].get('code') for d in t['declarations'] for n in descendants(d, 'typecode')}
        headers = {n['attributes'].get('entity'): n for n in t.get('entityTypes', [])}
        for name, n in headers.items():
            refs(n)
            if n['kind'] not in ('entity', 'subtype') or n['children']:
                error('ENTITY_TYPEKEY', 'Entity typekey requires native entity/subtype headers', n)
            seen, current = set(), name
            while current != t['name'] and current in headers and current not in seen:
                seen.add(current)
                current = headers[current]['attributes'].get('supertype')
            if current != t['name']:
                error('ENTITY_TYPEKEY', 'Entity typekey header does not descend from ' + t['name'], n)
        codes.update(headers)
        for d in t['declarations']:
            for n in walk(d):
                if n['kind'] in ('include', 'exclude') and n['attributes'].get('code') not in codes:
                    error('MISSING_TYPECODE', t['name'] + ': ' + str(n['attributes'].get('code')), n)
    def typelist_target(n, raw):
        if raw in lists:
            return lists[raw]
        resolution = n.get('resolution', {}).get('typelist', {})
        target = resolution.get('target')
        if (target in lists and raw.isascii() and target.isascii() and raw.lower() == target.lower()
                and resolution.get('status') == 'verifiedCaseInsensitive'
                and case_rule_applies(c['source']['release'])):
            return lists[target]
        error('MISSING_TYPELIST', 'Unresolved typelist ' + raw, n)
        return None
    for root in [p, line] + clauses + [d for e in c['entities'] for d in e['declarations']] + [d for t in c['typeLists'] for d in t['declarations']]:
        for n in walk(root):
            raw = n['attributes'].get('typelist') or n['attributes'].get('typeList')
            if raw:
                t = typelist_target(n, raw)
                filt = n['attributes'].get('typefilter')
                if t and filt and not any(x['attributes'].get('name') == filt for d in t['declarations'] for x in descendants(d, 'typefilter')):
                    error('MISSING_TYPEFILTER', raw + '.' + filt, n)
                if t and n['kind'] == 'category' and n['attributes'].get('code') not in {x['attributes'].get('entity') for x in t.get('entityTypes', [])} and not any(x['attributes'].get('code') == n['attributes'].get('code') for d in t['declarations'] for x in descendants(d, 'typecode')):
                    error('MISSING_CATEGORY', raw + ': category code ' + str(n['attributes'].get('code')), n)
    def resolve(n, attr, candidates, allow_narrow=False):
        raw = n['attributes'].get(attr)
        found = [x for x in candidates if raw is not None and raw in (x['attributes'].get('codeIdentifier'), x['attributes'].get('public-id'))]
        if len(found) != 1:
            if not (allow_narrow and narrowed and n.get('resolution', {}).get(attr, {}).get('status') == 'narrowed'):
                error('AMBIGUOUS_REFERENCE' if found else 'MISSING_REFERENCE', attr + ': ' + str(raw), n)
            return None
        target = found[0]
        if raw != target['attributes'].get('codeIdentifier'):
            r = n.get('resolution', {}).get(attr, {})
            if r.get('target') != target['attributes'].get('codeIdentifier') or r.get('status') != 'publicId':
                error('REFERENCE_EVIDENCE', attr + ': public-ID resolution not retained', n)
        return target
    categories = descendants(line, 'CoverageCategory')
    symbols = descendants(line, 'CoverageSymbolGroupPattern')
    for clause in clauses:
        for attr, targets in [('coverageCategory', categories), ('coverageSymbolGroupPattern', symbols)]:
            if attr in clause['attributes']:
                resolve(clause, attr, targets)
        for n in descendants(clause, 'ScheduledItemClauseLink'):
            resolve(n, 'linkedClausePattern', clauses, True)
    for bound in c['policyLinePattern']['modifierBounds']:
        inspect(bound, 'modifierBounds')
        resolve(bound, 'modifierPatternCode', descendants(line, 'ModifierPattern') + descendants(p, 'ProductModifierPattern'))
    for offering in c['product']['offerings']:
        if offering['kind'] != 'Offering' or offering['attributes'].get('product') != scope['product']:
            error('OFFERING_PRODUCT', 'Offering must name selected product', offering)
        sels = descendants(offering, 'PolicyLineSelection')
        for sel in sels:
            selected = sel['attributes'].get('policyLineCode')
            if selected not in join_names:
                error('OFFERING_LINE', 'Offering selects a line outside the product: ' + str(selected), sel)
            if selected != scope['line']:
                continue  # retained other-line context, not missing selected-line definitions
            for kind, attr in [('CoverageSelection', 'coverageCode'), ('ConditionSelection', 'conditionCode'), ('ExclusionSelection', 'exclusionCode')]:
                for cs in descendants(sel, kind):
                    clause = resolve(cs, attr, [x for x in clauses if x['kind'] == kind.replace('Selection', 'Pattern')], True)
                    if clause:
                        terms = [t for n in descendants(clause, 'CovTerms') for t in n['children']]
                        for container in descendants(cs, 'CovTermSelections'):
                            for ts in container['children']:
                                term = resolve(ts, 'covTermCode', terms)
                                if term:
                                    for os in descendants(ts, 'CovTermOptSelection'):
                                        resolve(os, 'covTermOptCode', descendants(term, 'CovTermOpt'))
                                    for ps in descendants(ts, 'CovTermPackSelection'):
                                        resolve(ps, 'covTermPackCode', descendants(term, 'CovTermPack'))
            for ms in descendants(sel, 'LineModifierSelection'):
                resolve(ms, 'modifierCode', descendants(line, 'ModifierPattern'))
        for ms in descendants(offering, 'ProductModifierSelection'):
            resolve(ms, 'modifierCode', descendants(p, 'ProductModifierPattern'))
        available_terms = {n['attributes'].get('termType') for n in descendants(p, 'AvailablePolicyTerm')}
        for ts in descendants(offering, 'PolicyTermSelection'):
            if ts['attributes'].get('termType') not in available_terms:
                error('POLICY_TERM_REFERENCE', 'Offering selects undeclared policy term', ts)
    for record in c['product']['displayProperties'] + c['policyLinePattern']['displayProperties'] + c['policyLinePattern']['properties']:
        refs(record)
    for row in c['costCodes']:
        inspect(row, 'cost')
    # costModel contains pointers into native fields, never semantic attachment classifications.
    model = c['costModel']
    expected_arrays = []
    subtype = line['attributes'].get('policyLineSubtype')
    for owner in sorted(entity_closure(subtype, entities)):
        for n in members(entities[owner]):
            target = n['attributes'].get('arrayentity')
            if n['kind'] == 'array' and 'Cost' in entity_closure(target, entities):
                expected_arrays.append((owner, n['attributes'].get('name'), target))
    actual = [(x['declaringEntity'], x['field'], x['targetEntity']) for x in (model or {}).get('lineArrays', [])]
    if sorted(actual) != sorted(expected_arrays) or (not expected_arrays and model is not None):
        error('COST_MODEL', 'Cost root pointers do not match native line arrays')
    if model:
        roots_set = {x[2] for x in expected_arrays}
        expected_classes = sorted(name for name in entities if roots_set & entity_closure(name, entities))
        if model['costEntities'] != expected_classes:
            error('COST_MODEL', 'Cost hierarchy members differ from native inheritance')
        expected_tx = []
        for owner in expected_classes:
            for n in members(entities[owner]):
                target = n['attributes'].get('arrayentity')
                if n['kind'] == 'array' and 'Transaction' in entity_closure(target, entities):
                    expected_tx.append((owner, n['attributes'].get('name'), target))
        actual_tx = [(x['declaringEntity'], x['field'], x['targetEntity']) for x in model['transactionArrays']]
        if not expected_tx or sorted(actual_tx) != sorted(expected_tx):
            error('COST_TRANSACTION', 'Transaction array wiring is missing or differs from native declarations')
        for owner, array, target in expected_tx:
            fks = [n for ancestor in entity_closure(target, entities) for n in members(entities[ancestor])
                   if n['kind'] in ('foreignkey', 'onetoone', 'edgeForeignKey')
                   and n['attributes'].get('fkentity') in entity_closure(owner, entities)]
            if not fks:
                error('COST_TRANSACTION', 'Transaction ' + target + ' has no native FK to its cost hierarchy')
            if not any(n['kind'] == 'array' and n['attributes'].get('arrayentity') == target for n in model['policyPeriodArrays']):
                error('COST_TRANSACTION', 'PolicyPeriod array missing for ' + target)
        for node in model['policyPeriodArrays']:
            inspect(node, 'entity')
            if node['kind'] != 'array' or node['attributes'].get('arrayentity') not in {x[2] for x in expected_tx}:
                error('COST_TRANSACTION', 'Unexpected PolicyPeriod transaction array', node)
    counts = collection_counts(c)
    if c['summary'] != {k: {'captured': v, 'inspection': 'inspected'} for k, v in counts.items()}:
        error('SUMMARY_COUNTS', 'Collection summary does not match retained records')
    state = 'blocked' if any(d['severity'] == 'error' for d in diagnostics) else ('partialScope' if narrowed else 'ready')
    if check_state and c['state'] != state:
        error('STATE_MISMATCH', 'Stored state %s differs from recomputed %s' % (c['state'], state))
        state = 'blocked'
    return {'state': state, 'diagnostics': diagnostics}
