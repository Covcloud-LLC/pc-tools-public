#!/usr/bin/env python3
# Copyright (c) 2026 Covcloud LLC. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Covcloud-Proprietary
"""Read one classic PC10.2.3 product/line into a faithful native JSON capture.

Python 3.8+ standard library. No role decisions, source writes or runtime network.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from native_capture_validation import (
    FORMAT, KINDS, TIERS, EXCLUSIONS, FIELDS,
    case_rule_applies, collection_counts, descendants, entity_closure, members, read_json, validate, walk)


CONFIGURATION_ROOT = 'modules/configuration'


class InputError(ValueError):
    pass


def local(tag):
    return tag.split('}')[-1]


def git(path, *args):
    return subprocess.check_output(['git', '--no-optional-locks', '-C', str(path)] + list(args), stderr=subprocess.DEVNULL).decode().strip()


def source_identity(root, read_paths):
    """Commit SHA and whether `read_paths` have uncommitted or untracked changes.

    Only a source root that is the top level of its own Git checkout has an identity; an export
    copied inside some other checkout must not take that checkout's commit."""
    try:
        if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
            return None, None
        return git(root, 'rev-parse', 'HEAD'), bool(git(root, 'status', '--porcelain', '--', *read_paths))
    except (OSError, subprocess.CalledProcessError):
        return None, None


def inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class Extractor:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.source_root).resolve(strict=True)
        self.config = (self.root / CONFIGURATION_ROOT / 'config').resolve(strict=True)
        if not inside(self.config, self.root):
            raise InputError('Configuration root must be inside source root')
        self.pm = self.config / 'resources/productmodel'
        self.release_path = self.root / 'project-version.properties'
        self.release = {}
        self.files = set()
        self.diagnostics = []
        self.entity_index = defaultdict(list)
        self.type_index = defaultdict(list)
        self.entities = {}
        self.types = {}

    def note(self, path):
        path = path.resolve(strict=True)
        if not inside(path, self.root):
            raise InputError('Input symlink escapes source root: ' + str(path))
        rel = path.relative_to(self.root).as_posix()
        self.files.add(rel)
        return rel

    def read(self, path):
        self.note(path)
        return path.read_text(encoding='utf-8')

    def xml(self, path):
        text = self.read(path)
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise InputError('DTD/entity declarations are unsupported: ' + str(path))
        root = ET.fromstring(text)
        for node in root.iter():
            namespace = node.tag.split('}')[0][1:] if node.tag.startswith('{') else ''
            if namespace not in ('', 'http://guidewire.com/datamodel', 'http://guidewire.com/typelists'):
                self.issue('UNSUPPORTED_NAMESPACE', str(path.relative_to(self.root)) + ': ' + namespace)
                break
        return root

    def issue(self, code, message, node=None):
        d = {'code': code, 'severity': 'error', 'message': message, 'pointer': ''}
        if node:
            d['sourceRefs'] = node['sourceRefs']
        self.diagnostics.append(d)

    def native(self, element, path, symbol=None):
        tag = local(element.tag)
        symbol = symbol or '/' + tag
        node = {'kind': tag, 'attributes': dict(element.attrib), 'children': [],
                'sourceRefs': [{'path': path.relative_to(self.root).as_posix(), 'symbol': symbol}]}
        if element.text is not None and (element.text.strip() or not len(element)):
            node['text'] = element.text
        excluded = []
        counts = defaultdict(int)
        for child in element:
            kind = local(child.tag)
            counts[kind] += 1
            if kind.endswith('Script'):
                excluded.append(kind)
                continue
            node['children'].append(self.native(child, path, symbol + '/' + kind + '[' + str(counts[kind]) + ']'))
            if child.tail and child.tail.strip():
                self.issue('UNSUPPORTED_MIXED_CONTENT', 'Non-whitespace text between native declarations', node)
        if excluded:
            node['excludedChildren'] = sorted(set(excluded))
        return node

    def load_node(self, path):
        return self.native(self.xml(path), path)

    def index_metadata(self):
        for family, suffixes, index in [('entity', ('.eti', '.etx', '.eix'), self.entity_index), ('typelist', ('.tti', '.tix', '.ttx'), self.type_index)]:
            for base in ['metadata', 'extensions']:
                directory = self.config / base / family
                if not directory.is_dir():
                    if base == 'extensions':
                        continue
                    raise InputError('Required metadata directory missing: ' + str(directory))
                for path in sorted(directory.rglob('*')):
                    if path.suffix not in suffixes:
                        continue
                    root = self.xml(path)
                    name = root.get('entity') or root.get('entityName') or root.get('name')
                    if not name:
                        self.issue('MISSING_METADATA_IDENTITY', 'No native identity in ' + str(path))
                        continue
                    index[name].append((path, root))

    def add_entities(self, seeds):
        queue = sorted(set(seeds) - set(self.entities))
        missing = set()
        while queue:
            name = queue.pop(0)
            if name in self.entities or name in missing:
                continue
            declarations = self.entity_index.get(name, [])
            if not declarations:
                self.issue('MISSING_ENTITY', 'No declaration for required entity ' + name)
                missing.add(name)
                continue
            e = {'name': name, 'declarations': [self.native(root, path) for path, root in declarations]}
            self.entities[name] = e
            for d in e['declarations']:
                if d['attributes'].get('supertype'):
                    queue.append(d['attributes']['supertype'])
                for n in d['children']:
                    a = n['attributes']
                    if n['kind'] == 'implementsEntity' and a.get('name'):
                        queue.append(a['name'])
                    if self.args.entity_closure == 'composition' and n['kind'] in ('array', 'onetoone'):
                        target = a.get('arrayentity') or a.get('fkentity')
                        if target:
                            queue.append(target)

    def raw_ancestors(self, name, seen=None):
        seen = set() if seen is None else seen
        if name in seen:
            return seen
        seen.add(name)
        for _, root in self.entity_index.get(name, []):
            targets = [root.get('supertype')] + [n.get('name') for n in root if local(n.tag) == 'implementsEntity']
            for target in targets:
                if target:
                    self.raw_ancestors(target, seen)
        return seen

    def costs(self, subtype):
        arrays = []
        for owner in sorted(entity_closure(subtype, self.entities)):
            for n in members(self.entities[owner]):
                target = n['attributes'].get('arrayentity')
                if n['kind'] == 'array' and target and 'Cost' in self.raw_ancestors(target):
                    arrays.append({'declaringEntity': owner, 'field': n['attributes']['name'], 'targetEntity': target})
        if not arrays:
            return None
        roots = {x['targetEntity'] for x in arrays}
        cost_names = sorted(name for name in self.entity_index if roots & self.raw_ancestors(name))
        self.add_entities(cost_names)
        tx = []
        for owner in cost_names:
            for n in members(self.entities[owner]):
                target = n['attributes'].get('arrayentity')
                if n['kind'] == 'array' and target and 'Transaction' in self.raw_ancestors(target):
                    tx.append({'declaringEntity': owner, 'field': n['attributes']['name'], 'targetEntity': target})
        self.add_entities(x['targetEntity'] for x in tx)
        # Capture only matching PolicyPeriod wiring, without opening its full graph.
        pp_arrays = []
        for path, root in self.entity_index.get('PolicyPeriod', []):
            declaration = self.native(root, path)
            pp_arrays.extend(n for n in declaration['children'] if n['kind'] == 'array' and n['attributes'].get('arrayentity') in {x['targetEntity'] for x in tx})
        return {'lineArrays': arrays, 'costEntities': cost_names, 'transactionArrays': tx, 'policyPeriodArrays': pp_arrays}

    def add_typelists(self, nodes):
        queue = list(nodes)
        while queue:
            n = queue.pop(0)
            raw = n['attributes'].get('typelist') or n['attributes'].get('typeList')
            if not raw:
                continue
            target = raw
            if raw not in self.type_index and raw not in self.entity_index:
                candidates = [k for k in self.type_index if raw.isascii() and k.isascii() and raw.lower() == k.lower()]
                if len(candidates) != 1:
                    self.issue('AMBIGUOUS_TYPELIST' if candidates else 'MISSING_TYPELIST', 'Cannot resolve ' + raw, n)
                    continue
                target = candidates[0]
                if case_rule_applies(self.release):
                    refs = [{'path': path.relative_to(self.root).as_posix(), 'symbol': '/' + local(root.tag)} for path, root in self.type_index[target]]
                    n.setdefault('resolution', {})['typelist'] = {'target': target, 'status': 'verifiedCaseInsensitive', 'sourceRefs': refs}
                else:
                    self.issue('UNVERIFIED_TYPELIST_CASE', raw + ' differs from ' + target + '; case-insensitive lookup is verified only for platform 10.203.1 with language version 1.17.4', n)
            if target not in self.types:
                declarations = [self.native(root, path) for path, root in self.type_index[target]]
                record = {'name': target, 'declarations': declarations}
                if not any(d['kind'] == 'typelist' for d in declarations) and target in self.entity_index:
                    # PC entity typekeys are generated from the native subtype hierarchy.
                    # Keep headers only; these are code definitions, not expanded foreign objects.
                    headers = []
                    for name, decls in sorted(self.entity_index.items()):
                        if target in self.raw_ancestors(name):
                            for path, root in decls:
                                if local(root.tag) in ('entity', 'subtype'):
                                    header = self.native(root, path)
                                    header['children'] = []
                                    headers.append(header)
                    record['entityTypes'] = headers
                self.types[target] = record
                queue.extend(x for d in declarations for x in walk(d))

    def reference_evidence(self, p, line, clauses, offerings, all_clauses):
        def resolve(n, attr, candidates, narrowed_candidates=()):
            raw = n['attributes'].get(attr)
            found = [x for x in candidates if raw is not None and raw in (x['attributes'].get('codeIdentifier'), x['attributes'].get('public-id'))]
            if len(found) == 1:
                t = found[0]
                if raw != t['attributes'].get('codeIdentifier'):
                    n.setdefault('resolution', {})[attr] = {'target': t['attributes'].get('codeIdentifier', ''), 'status': 'publicId', 'sourceRefs': t['sourceRefs']}
                return t
            omitted = [x for x in narrowed_candidates if raw is not None and raw in (x['attributes'].get('codeIdentifier'), x['attributes'].get('public-id'))]
            if not found and len(omitted) == 1:
                n.setdefault('resolution', {})[attr] = {'target': omitted[0]['attributes'].get('codeIdentifier', ''), 'status': 'narrowed', 'sourceRefs': omitted[0]['sourceRefs']}
            return None
        omitted = [x for x in all_clauses if x not in clauses]
        for clause in clauses:
            for attr, kind in [('coverageCategory', 'CoverageCategory'), ('coverageSymbolGroupPattern', 'CoverageSymbolGroupPattern')]:
                if attr in clause['attributes']:
                    resolve(clause, attr, descendants(line, kind))
            for n in descendants(clause, 'ScheduledItemClauseLink'):
                resolve(n, 'linkedClausePattern', clauses, omitted)
        for offering in offerings:
            for sel in descendants(offering, 'PolicyLineSelection'):
                if sel['attributes'].get('policyLineCode') != self.args.line:
                    continue
                for kind, attr in [('CoverageSelection', 'coverageCode'), ('ConditionSelection', 'conditionCode'), ('ExclusionSelection', 'exclusionCode')]:
                    for cs in descendants(sel, kind):
                        clause = resolve(cs, attr, [x for x in clauses if x['kind'] == kind.replace('Selection', 'Pattern')], omitted)
                        if clause:
                            terms = [t for container in descendants(clause, 'CovTerms') for t in container['children']]
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

    def run(self):
        args = self.args
        product_dir = self.pm / 'products' / args.product
        line_dir = self.pm / 'policylinepatterns' / args.line
        p = self.load_node(product_dir / (args.product + '.xml'))
        line = self.load_node(line_dir / (args.line + '.xml'))
        if self.release_path.is_file():
            self.release = dict(s.split('=', 1) for s in self.read(self.release_path).splitlines() if '=' in s and not s.startswith('#'))
        mode = 'apdGenerated' if any(n['attributes'].get('apdManaged') in ('true', '1') for n in [p, line]) or (line_dir / 'riskpatterns').exists() else 'classic'
        if line['attributes'].get('policyLineSubtype', '').startswith('APD'):
            mode = 'visualizedOnly' if not (line_dir / 'coveragepatterns').is_dir() else 'apdGenerated'
        clauses, all_clauses, offerings = [], [], []
        directory = line_dir / 'coveragepatterns'
        if not directory.is_dir():
            self.issue('MISSING_CLAUSE_DIRECTORY', 'No coveragepatterns directory for selected line')
        else:
            for path in sorted(directory.glob('*.xml')):
                if path.name.endswith('-lookups.xml'):
                    continue
                n = self.load_node(path)
                kind = {'CoveragePattern': 'coverage', 'ConditionPattern': 'condition', 'ExclusionPattern': 'exclusion'}.get(n['kind'])
                if kind is None:
                    self.issue('UNSUPPORTED_CLAUSE', 'Unknown clause root ' + n['kind'], n)
                if kind == 'coverage':
                    existence = n['attributes'].get('existence')
                    tier = 'mandatory' if existence == 'Required' else 'electable' if existence in ('Suggested', 'Electable') else 'scripted'
                    n['tier'] = tier
                    if existence is not None and existence not in ('Required', 'Suggested', 'Electable'):
                        self.issue('UNSUPPORTED_EXISTENCE', 'Unknown native existence ' + existence, n)
                    if existence is None and 'ExistenceScript' not in n.get('excludedChildren', []):
                        self.issue('MISSING_EXISTENCE', 'Neither existence declaration nor excluded ExistenceScript is present', n)
                all_clauses.append(n)
                if kind is None or kind != 'coverage' or n['tier'] in args.coverage_tiers:
                    clauses.append(n)
        for path in sorted((product_dir / 'offerings').glob('*.xml')):
            if not path.name.endswith('-lookups.xml'):
                offerings.append(self.load_node(path))
        # Unknown files in the selected declaration directories cannot silently become ready.
        for directory, main, allowed_dirs in [(product_dir, args.product, {'offerings', 'images', 'jurisdictions'}), (line_dir, args.line, {'coveragepatterns', 'images', 'jurisdictions', 'riskpatterns'})]:
            for path in sorted(directory.iterdir()):
                if path.is_dir() and path.name not in allowed_dirs or path.is_file() and path.suffix == '.xml' and path.name not in (main + '.xml', main + '-lookups.xml'):
                    self.issue('UNSUPPORTED_FILE', 'Unrecognized selected-scope declaration path: ' + str(path.relative_to(self.root)))
        bounds = []
        for path in sorted((line_dir / 'jurisdictions').glob('**/*-modifierminmax.xml')):
            bounds.extend(self.load_node(path)['children'])
        for selected_dir in (product_dir, line_dir):
            for path in sorted(selected_dir.rglob('*.xml')):
                relative = path.relative_to(selected_dir)
                if 'images' in relative.parts or 'riskpatterns' in relative.parts or path.name.endswith('-lookups.xml'):
                    continue
                rel = path.relative_to(self.root).as_posix()
                if rel not in self.files:
                    self.note(path)
                    self.issue('UNSUPPORTED_FILE', 'Unrecognized in-scope XML file: ' + rel)
        properties = []
        props = line_dir / (args.line + '.properties')
        if props.is_file():
            for number, raw in enumerate(self.read(props).splitlines(), 1):
                if not raw.strip() or raw.lstrip().startswith(('#', '!')):
                    continue
                if '=' not in raw or raw.endswith('\\'):
                    self.issue('UNSUPPORTED_PROPERTY', 'Unsupported property syntax at line ' + str(number))
                    continue
                key, value = raw.split('=', 1)
                properties.append({'name': key, 'value': value, 'sourceRefs': [{'path': props.relative_to(self.root).as_posix(), 'symbol': 'line ' + str(number)}]})
        display = {'product': [], 'line': []}
        display_path = self.config / 'locale/productmodel.display.properties'
        if display_path.is_file():
            for number, raw in enumerate(self.read(display_path).splitlines(), 1):
                family = 'product' if raw.startswith('Product_' + args.product + '.') else 'line' if raw.startswith('PolicyLine_' + args.line + '.') else None
                if family:
                    if '=' not in raw or raw.endswith('\\'):
                        self.issue('UNSUPPORTED_PROPERTY', 'Unsupported display property syntax at line ' + str(number))
                        continue
                    key, value = raw.split('=', 1)
                    display[family].append({'name': key, 'value': value, 'sourceRefs': [{'path': display_path.relative_to(self.root).as_posix(), 'symbol': 'line ' + str(number)}]})
        self.index_metadata()
        seeds = set()
        seed_attrs = {'policyLineSubtype', 'owningEntityType', 'coverageSubtype', 'conditionSubtype', 'exclusionSubtype', 'modifierSubtype', 'scheduledItemType', 'rateFactorSubtype'}
        for root in [p, line] + clauses:
            for n in walk(root):
                seeds.update(n['attributes'][key] for key in seed_attrs if n['attributes'].get(key))
        self.add_entities(seeds)
        cost_model = self.costs(line['attributes'].get('policyLineSubtype'))
        for e in self.entities.values():
            for n in members(e):
                target = n['attributes'].get('fkentity') or n['attributes'].get('arrayentity')
                if target and n['kind'] in FIELDS:
                    if target in self.entities:
                        n['referenceStatus'] = 'captured'
                    elif n['kind'] in ('array', 'onetoone') and args.entity_closure == 'seed':
                        n['referenceStatus'] = 'narrowed'
                    else:
                        n['referenceStatus'] = 'external'
                        n['referenceReason'] = 'Foreign-key target outside selected composition closure; declaration not expanded'
                        if target not in self.entity_index:
                            self.issue('MISSING_FK_TARGET', 'No native declaration indexed for FK target ' + target, n)
        nodes = [n for root in [p, line] + clauses for n in walk(root)]
        nodes += [n for e in self.entities.values() for d in e['declarations'] for n in walk(d)]
        self.add_typelists(nodes)
        self.reference_evidence(p, line, clauses, offerings, all_clauses)
        for bound in bounds:
            raw = bound['attributes'].get('modifierPatternCode')
            matches = [n for n in descendants(line, 'ModifierPattern') + descendants(p, 'ProductModifierPattern') if raw in (n['attributes'].get('codeIdentifier'), n['attributes'].get('public-id'))]
            if len(matches) == 1 and raw != matches[0]['attributes'].get('codeIdentifier'):
                bound.setdefault('resolution', {})['modifierPatternCode'] = {'target': matches[0]['attributes'].get('codeIdentifier', ''), 'status': 'publicId', 'sourceRefs': matches[0]['sourceRefs']}
        cost_path = self.config / 'resources/systables/costcode_ext.xml'
        cost_codes = []
        if cost_path.is_file():
            cost_codes = self.load_node(cost_path)['children']
        else:
            self.issue('MISSING_COST_CODES', 'Required costcode_ext.xml not found')
        read_paths = [self.config.relative_to(self.root).as_posix(), self.release_path.relative_to(self.root).as_posix()]
        rev, dirty = source_identity(self.root, read_paths)
        capture = {'formatVersion': FORMAT, 'state': 'blocked',
            'source': {'configurationRoot': CONFIGURATION_ROOT, 'revision': rev, 'dirty': dirty, 'release': self.release, 'generationMode': mode},
            'scope': {'product': args.product, 'line': args.line, 'availableProductLines': [n['attributes'].get('policyLinePattern', '') for n in descendants(p, 'ProductPolicyLinePattern')], 'clauseKinds': KINDS, 'coverageTiers': args.coverage_tiers, 'entityClosure': args.entity_closure, 'exclusions': EXCLUSIONS},
            'product': {'declaration': p, 'offerings': offerings, 'displayProperties': display['product']}, 'policyLinePattern': {'declaration': line, 'clauses': clauses, 'properties': properties, 'displayProperties': display['line'], 'modifierBounds': bounds},
            'entities': [self.entities[n] for n in sorted(self.entities)], 'typeLists': [self.types[n] for n in sorted(self.types)],
            'costCodes': cost_codes, 'costModel': cost_model, 'summary': {}, 'diagnostics': self.diagnostics}
        capture['summary'] = {k: {'captured': v, 'inspection': 'inspected'} for k, v in collection_counts(capture).items()}
        result = validate(capture, check_state=False)
        capture['state'] = result['state']
        capture['diagnostics'] = result['diagnostics']
        return capture


def publish(capture, out, overwrite, source):
    out = Path(out)
    if inside(out, source):
        raise InputError('Output must be outside the read-only source checkout')
    target = out / ('product-capture.json' if capture['state'] == 'ready' else 'product-capture.diagnostic.json')
    if target.is_symlink() or out.is_symlink():
        raise InputError('Output directory/file must not be a symbolic link')
    if target.exists() and not overwrite:
        raise InputError('Output exists; pass --overwrite explicitly: ' + str(target))
    if target.exists() and capture['state'] != 'ready':
        previous = read_json(target)
        if isinstance(previous, dict) and previous.get('state') == 'ready':
            raise InputError('Diagnostic publication cannot replace a prior ready capture, regardless of filename')
    out.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(out), prefix='.product-capture-', suffix='.tmp', delete=False) as f:
            staged = Path(f.name)
            json.dump(capture, f, indent=1, ensure_ascii=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        result = validate(read_json(staged))
        if result['state'] != capture['state']:
            raise InputError('Staged capture failed independent reread validation')
        if overwrite:
            os.replace(str(staged), str(target))
        else:
            # Exclusive atomic publication; a racing writer cannot be silently overwritten.
            os.link(str(staged), str(target))
            staged.unlink()
        staged = None
        # Only this tool's stale diagnostic file is owned. Never delete arbitrary neighboring files.
        stale = out / 'product-capture.diagnostic.json'
        if overwrite and capture['state'] == 'ready' and stale.is_file() and not stale.is_symlink():
            try:
                stale.unlink()
            except OSError as exc:
                print('warning: ready capture published; stale diagnostic cleanup failed: ' + str(exc), file=sys.stderr)
        return target
    finally:
        if staged is not None and staged.exists():
            staged.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--product')
    parser.add_argument('--line')
    parser.add_argument('--out')
    parser.add_argument('--coverage-tiers', default=','.join(TIERS))
    parser.add_argument('--entity-closure', choices=['composition', 'seed'], default='composition')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--survey', action='store_true')
    args = parser.parse_args(argv)
    try:
        tiers = args.coverage_tiers.split(',')
        if len(tiers) != len(set(tiers)) or not set(tiers) <= set(TIERS):
            raise InputError('Invalid --coverage-tiers')
        args.coverage_tiers = tiers
        extractor = Extractor(args)
        if args.survey:
            products = []
            for path in sorted((extractor.pm / 'products').glob('*/*.xml')):
                if path.name.endswith('-lookups.xml'):
                    continue
                root = extractor.xml(path)
                if local(root.tag) == 'Product':
                    products.append({'product': root.get('codeIdentifier'), 'lines': [n.get('policyLinePattern') for n in root.iter() if local(n.tag) == 'ProductPolicyLinePattern']})
            print(json.dumps({'products': products, 'supportedRelease': '10.2.3', 'exclusions': EXCLUSIONS}, indent=2))
            return 0
        if not args.product or not args.line or not args.out:
            raise InputError('--product, --line and --out are required (use --survey to discover identity)')
        for value in [args.product, args.line]:
            if value in ('.', '..') or '/' in value or '\\' in value:
                raise InputError('Product and line must be native identifiers, not paths')
        if inside(Path(args.out), extractor.root):
            raise InputError('Output must be outside the read-only source checkout')
        capture = extractor.run()
        target = publish(capture, args.out, args.overwrite, extractor.root)
        print(json.dumps({'state': capture['state'], 'output': str(target.resolve()), 'summary': capture['summary'], 'diagnostics': capture['diagnostics']}, ensure_ascii=False))
        return 0 if capture['state'] == 'ready' else 3
    except (OSError, ValueError, ET.ParseError, RecursionError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
