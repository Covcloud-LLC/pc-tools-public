#!/usr/bin/env python3
"""Normalize observed PC worksheet XML using Python 3.8+ stdlib."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET


class NormalizeError(Exception):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


# An explicit supported grammar: accepting a tag alone would silently accept
# semantic changes in its attributes or nesting. Source-only shapes are rejected.
OPERANDS = {'Constant', 'Variable', 'PropertyGet', 'Function', 'RateQuery'}
BRANCHES = {'If', 'ElseIf', 'Else', 'EndIf'}
STATEMENTS = {'Store', 'PropertySet', 'InstanceMethod', 'ConditionalGroup', 'Loop', 'Iteration'} | BRANCHES
# Attributes an Iteration marker repeats from its Loop.
LOOP = ('Iterable', 'IterableType', 'LoopIndexVariable', 'LoopVariable', 'LoopVariableType')
VALUE = {'Value', 'ValueType'}
EXPRESSION = VALUE | {'Operator', 'LeftParenthesis', 'RightParenthesis'}
ATTRIBUTES = {
    'Worksheets': set(),
    'Worksheet': {'Description', 'EffectiveDate', 'ExpirationDate', 'FixedId', 'Tag'},
    'Routine': {'RateBookCode', 'RateBookEdition', 'RoutineCode', 'RoutineVersion'},
    'Store': {'Variable', 'Declaration', 'Result', 'ResultType'},
    'PropertySet': {'ObjectName', 'ObjectType', 'PropertyName', 'Result', 'ResultType'},
    'Variable': EXPRESSION | {'Name', 'Type'},
    'PropertyGet': EXPRESSION | {'ObjectName', 'ObjectType', 'ObjectValue', 'PropertyName', 'Type'},
    'Constant': EXPRESSION,
    'Function': EXPRESSION | {'ClassName', 'Name', 'Type'},
    'Argument': VALUE | {'Name'},
    'RateQuery': EXPRESSION | {'TableCode', 'TableName', 'FactorName', 'FactorSource', 'Type'},
    'QueryParam': VALUE | {'Name'},
    'InstanceMethod': {'FunctionName', 'Object', 'ObjectName', 'ObjectType', 'Type'},
    'ConditionalGroup': set(), 'If': {'Result'}, 'ElseIf': {'Result'}, 'Else': {'Result'},
    'Condition': set(), 'EndIf': set(),
    'Loop': set(LOOP) | {'IterableSize'}, 'Iteration': set(LOOP) | {'IterationCount'},
}
CHILDREN = {
    'Worksheets': {'Worksheet'}, 'Worksheet': {'Routine'}, 'Routine': STATEMENTS,
    'Store': OPERANDS, 'PropertySet': OPERANDS, 'Argument': OPERANDS,
    'Function': {'Argument'}, 'RateQuery': {'QueryParam'},
    'InstanceMethod': {'Argument'}, 'ConditionalGroup': STATEMENTS,
    'If': STATEMENTS | {'Condition'}, 'ElseIf': STATEMENTS | {'Condition'}, 'Else': STATEMENTS,
    'Condition': OPERANDS, 'Loop': STATEMENTS | {'Variable'},
}
REQUIRED = {
    'Store': {'Variable'}, 'Variable': {'Name'},
    'PropertySet': {'ObjectName', 'ObjectType', 'PropertyName'},
    'PropertyGet': {'ObjectName', 'ObjectType', 'PropertyName'},
    'Function': {'ClassName', 'Name'}, 'Argument': {'Name'},
    'RateQuery': {'TableCode', 'FactorName', 'Type'}, 'QueryParam': {'Name'},
    'InstanceMethod': {'FunctionName', 'ObjectName', 'ObjectType'},
    'If': {'Result'}, 'ElseIf': {'Result'}, 'Else': {'Result'},
    'Loop': set(LOOP) | {'IterableSize'}, 'Iteration': set(LOOP) | {'IterationCount'},
}
NUMERIC_TYPES = {
    'byte', 'short', 'int', 'long', 'float', 'double',
    'java.lang.Byte', 'java.lang.Short', 'java.lang.Integer', 'java.lang.Long',
    'java.lang.Float', 'java.lang.Double', 'java.math.BigInteger', 'java.math.BigDecimal',
}
BOOLEAN_TYPES = {'boolean', 'java.lang.Boolean'}
# PC marks its rounding calls by Type; their arguments hold no business input.
ROUNDING = {('Function', 'Rounding'), ('InstanceMethod', 'CostDataRounding')}
QUERY_TYPES = {'SingleFactor', 'MultiFactor'}
# Operands that record their value under their own name.
SELF_NAMED = {'Variable', 'PropertyGet', 'RateQuery'}
DEFAULT_OUTPUT = 'worksheets-normalized.json'
NUMBER = re.compile(r'([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?\Z')


def canonical_number(raw, path):
    """Canonical decimal text, without Decimal context rounding or float parsing."""
    match = NUMBER.fullmatch(raw)
    if not match or not (match[2] or match[3]):
        raise NormalizeError('unsupported_content', '{}: invalid finite typed number'.format(path))
    sign, whole, fraction, exponent = match.groups()
    fraction = fraction or ''
    # Bound expansion before allocating. Worksheet values never need huge exponents.
    exp_text = (exponent or '0').lstrip('+-').lstrip('0')
    if len(exp_text) > 6:
        raise NormalizeError('unsupported_content', '{}: numeric exponent exceeds supported limit'.format(path))
    scale = int(exp_text or '0') * (-1 if (exponent or '').startswith('-') else 1) - len(fraction)
    digits = (whole + fraction).lstrip('0')
    if not digits:
        return '0'
    trimmed = digits.rstrip('0')
    scale += len(digits) - len(trimmed)
    digits = trimmed
    expanded_digits = len(digits) + scale if scale >= 0 else max(len(digits), 1 - scale)
    if expanded_digits > 100000:
        raise NormalizeError('unsupported_content', '{}: expanded number exceeds 100000 digits'.format(path))
    if scale >= 0:
        value = digits + '0' * scale
    elif len(digits) + scale > 0:
        value = digits[:scale] + '.' + digits[scale:]
    else:
        value = '0.' + '0' * (-scale - len(digits)) + digits
    return ('-' if sign == '-' else '') + value


def recorded_value(node, path):
    """Plain JSON value: canonical number text, string, boolean, null, or recorded opaque text."""
    attr = 'Result' if node.tag in {'Store', 'PropertySet'} else 'Value'
    value_type = node.get(attr + 'Type')
    raw = node.get(attr)
    if raw is None:
        return None
    if value_type in NUMERIC_TYPES:
        return canonical_number(raw, path)
    if value_type in BOOLEAN_TYPES:
        if raw not in {'true', 'false'}:
            raise NormalizeError('unsupported_content', '{}: invalid typed boolean'.format(path))
        return raw == 'true'
    # Strings, typekeys and enum-like values keep their exact serialized form.
    return raw


def validate(node, path):
    if node.tag not in ATTRIBUTES:
        raise NormalizeError('unsupported_content', '{}: unsupported tag'.format(path))
    unknown = set(node.attrib) - ATTRIBUTES[node.tag]
    if unknown:
        raise NormalizeError('unsupported_content', '{}: unsupported attributes {}'.format(path, ', '.join(sorted(unknown))))
    missing = [a for a in REQUIRED.get(node.tag, ()) if not node.get(a)]
    if missing:
        raise NormalizeError('unsupported_content', '{}: missing identity/shape attributes {}'.format(path, ', '.join(sorted(missing))))
    if (node.text or '').strip() or (node.tail or '').strip():
        raise NormalizeError('unsupported_content', '{}: text content is unsupported; values must be attributes'.format(path))
    if node.tag == 'RateQuery' and node.get('Type') not in QUERY_TYPES:
        raise NormalizeError('unsupported_content', '{}: only SingleFactor and MultiFactor queries are supported'.format(path))
    if node.tag in {'If', 'ElseIf', 'Else'} and node.get('Result') not in {'true', 'false'}:
        raise NormalizeError('unsupported_content', '{}: branch Result must be true or false'.format(path))
    if node.tag == 'Store' and node.get('Declaration', 'false') not in {'true', 'false'}:
        raise NormalizeError('unsupported_content', '{}: Declaration must be true or false'.format(path))
    if node.tag == 'Worksheet':
        if not len(node) or any(c.tag != 'Routine' for c in node):
            raise NormalizeError('unsupported_content', '{}: expected one or more Routine elements and nothing else'.format(path))
        codes = {}
        for index, routine in enumerate(node, 1):
            routine_path = '{}/Routine[{}]'.format(path, index)
            code = routine.get('RoutineCode')
            if code in codes:
                raise NormalizeError('unsupported_content', '{} and {}: both have RoutineCode {}'.format(codes[code], routine_path, json.dumps(code)))
            codes[code] = routine_path
    if node.tag in {'If', 'ElseIf'} and (not len(node) or node[0].tag != 'Condition' or sum(c.tag == 'Condition' for c in node) != 1):
        raise NormalizeError('unsupported_content', '{}: expected one leading Condition'.format(path))
    if node.tag in {'If', 'ElseIf', 'Else'} and node.get('Result') == 'false' and any(c.tag != 'Condition' for c in node):
        raise NormalizeError('unsupported_content', '{}: unexecuted branch contains statements'.format(path))
    if node.tag == 'ConditionalGroup' and not len(node):
        raise NormalizeError('unsupported_content', '{}: empty ConditionalGroup'.format(path))
    if node.tag in {'Routine', 'If', 'ElseIf', 'Else', 'Loop'}:
        sequence(node, path)
    if 'Value' in node.attrib or 'ResultType' in node.attrib:
        recorded_value(node, path)  # Validate even non-identifier constants.
    for index, child in enumerate(node, 1):
        child_path = '{}/{}[{}]'.format(path, child.tag, index)
        if child.tag not in CHILDREN.get(node.tag, set()):
            raise NormalizeError('unsupported_content', '{}: unsupported child of {}'.format(child_path, node.tag))
        validate(child, child_path)


def steps(container, path):
    """A container's children with ConditionalGroups flattened, in document order.

    PC opens a ConditionalGroup at an If followed by ElseIf or Else and never
    closes it, so every later entry of the same routine, branch or loop lands inside
    it, nested groups included. The group is layout only."""
    for index, child in enumerate(container, 1):
        child_path = '{}/{}[{}]'.format(path, child.tag, index)
        if child.tag == 'ConditionalGroup':
            yield from steps(child, child_path)
        else:
            yield child, child_path


def passes(loop, path):
    """A Loop's steps after its iterable Variable, checked against its Iteration markers."""
    if not len(loop) or loop[0].tag != 'Variable':
        raise NormalizeError('unsupported_content', '{}: expected the iterable Variable first'.format(path))
    found = list(steps(loop, path))[1:]
    for node, node_path in found:
        if node.tag == 'Variable':
            raise NormalizeError('unsupported_content', '{}: second iterable Variable in a Loop'.format(node_path))
    size = loop.get('IterableSize')
    if not re.fullmatch(r'[0-9]+', size):
        raise NormalizeError('unsupported_content', '{}: IterableSize {} is not a non-negative integer'.format(path, json.dumps(size)))
    markers = [(node, node_path) for node, node_path in found if node.tag == 'Iteration']
    # Compare digit text: int() refuses very long strings on current Python.
    if str(len(markers)) != (size.lstrip('0') or '0'):
        raise NormalizeError('unsupported_content', '{}: IterableSize {} but {} Iteration markers'.format(path, size, len(markers)))
    if found and found[0][0].tag != 'Iteration':
        raise NormalizeError('unsupported_content', '{}: step before the first Iteration'.format(found[0][1]))
    for position, (marker, marker_path) in enumerate(markers):
        if marker.get('IterationCount') != str(position):
            raise NormalizeError('unsupported_content', '{}: IterationCount {} at position {}'.format(marker_path, json.dumps(marker.get('IterationCount')), position))
        differ = [a for a in LOOP if marker.get(a) != loop.get(a)]
        if differ:
            raise NormalizeError('unsupported_content', '{}: {} not equal to its Loop\'s'.format(marker_path, ', '.join(differ)))
    return found


def sequence(container, path):
    """Each conditional chain is If, any ElseIf, at most one Else, EndIf, as PC logs
    it: only branches it reached, at most one taken, opened and closed within one loop pass."""
    found = passes(container, path) if container.tag == 'Loop' else steps(container, path)
    chain = opened = taken = None
    for node, node_path in found:
        tag, ran = node.tag, node.get('Result') == 'true'
        if tag == 'Condition':
            continue  # Placement is checked with its branch.
        if tag == 'Iteration':
            if container.tag != 'Loop':
                raise NormalizeError('unsupported_content', '{}: Iteration outside a Loop'.format(node_path))
            if chain:
                raise NormalizeError('unsupported_content', '{}: conditional chain at {} crosses a loop pass'.format(node_path, opened))
        elif tag == 'If' and not chain:
            chain, opened, taken = 'if', node_path, ran
        elif tag == 'ElseIf' and chain == 'if':
            if taken and ran:
                raise NormalizeError('unsupported_content', '{}: a second branch of one chain ran'.format(node_path))
            taken = taken or ran
        elif tag == 'Else' and chain == 'if':
            if ran == taken:
                # Else runs exactly when no earlier branch did.
                raise NormalizeError('unsupported_content', '{}: Else Result {} after {} branch'.format(node_path, node.get('Result'), 'a taken' if taken else 'no taken'))
            chain = 'else'
        elif tag == 'EndIf' and chain:
            chain = None
        elif tag in BRANCHES:
            where = 'after Else' if chain == 'else' else 'inside an open chain' if chain else 'without an open If'
            raise NormalizeError('unsupported_content', '{}: {} {}'.format(node_path, tag, where))
        elif chain:
            raise NormalizeError('unsupported_content', '{}: statement between branches of the chain at {}'.format(node_path, opened))
    if chain:
        raise NormalizeError('unsupported_content', '{}: conditional chain has no EndIf'.format(opened))


RESERVED = re.compile(r"[\s.\[\]()\"':=\\]")


def segment(text, path):
    """A name part, single-quoted when it holds whitespace or grammar punctuation."""
    if not text:
        raise NormalizeError('unsupported_content', '{}: empty name part'.format(path))
    return quoted(text) if RESERVED.search(text) else text


def quoted(text):
    return "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"


def redundant(argument):
    """An argument whose only operand records the same value under its own name."""
    found = operands(argument)
    return len(found) == 1 and found[0].tag in SELF_NAMED


def operands(node):
    return [c for c in node if c.tag in OPERANDS]


def shared_functions(routine):
    """Function names that two classes share in one routine."""
    owners = {}
    for node in routine.iter('Function'):
        owners.setdefault(node.get('Name'), set()).add(node.get('ClassName'))
    return {name for name, found in owners.items() if len(found) > 1}


def normalize(root):
    if root.tag != 'Worksheets':
        raise NormalizeError('unsupported_content', 'Expected a Worksheets document root')
    validate(root, '/Worksheets')
    if not len(root):
        raise NormalizeError('unsupported_content', 'Worksheets contains no Worksheet elements')
    worksheets = []
    for index, worksheet in enumerate(root, 1):
        for routine_index, routine in enumerate(worksheet, 1):
            identifiers = routine_values(routine, '/Worksheets/Worksheet[{}]/Routine[{}]'.format(index, routine_index))
            worksheets.append({'metadata': dict(worksheet.attrib), 'routine': dict(routine.attrib),
                               'identifiers': {n: identifiers[n] for n in sorted(identifiers)}})
    return {'format': 'pc-worksheet-final-values', 'version': 3, 'worksheets': worksheets}


def routine_values(routine, routine_path):
    """One routine's final values: every name keeps its last value in document order."""
    final, types, classes = {}, {}, {}
    shared = shared_functions(routine)

    def obj(node, path):
        name, object_type = segment(node.get('ObjectName'), path), node.get('ObjectType')
        if types.setdefault(name, object_type) != object_type:
            raise NormalizeError('unsupported_content', '{}: object {} has types {} and {} in one routine'.format(path, name, types[name], object_type))
        return name

    def function(node, path):
        name = segment(node.get('Name'), path)
        if name not in shared:
            return name
        full = node.get('ClassName')
        qualified = segment(full.rsplit('.', 1)[-1], path) + '.' + name
        if classes.setdefault(qualified, full) != full:
            raise NormalizeError('unsupported_content', '{}: classes {} and {} share the simple name and function {}'.format(path, classes[qualified], full, qualified))
        return qualified

    def loop(node, path):
        # Bookkeeping gives no rows: the iterable Variable, the markers (which have no
        # name), and a Store of the loop's index or element variable directly in its passes.
        own = {node.get('LoopIndexVariable'), node.get('LoopVariable')}
        for child, child_path in passes(node, path):
            if not (child.tag == 'Store' and child.get('Variable') in own):
                visit(child, '', child_path)

    def visit(node, prefix, path, owned=False):
        """prefix places a call: 'target := ', an enclosing argument 'call().arg.', or '' (bare).
        owned: the node is the only operand of an assignment or argument that already holds its value."""
        name, child_prefix = None, prefix
        if node.tag == 'Loop':
            return loop(node, path)
        if (node.tag, node.get('Type')) in ROUNDING:
            # The same name checks as any call, though only a kept result row shows a name.
            if node.tag == 'Function':
                call = prefix + function(node, path) + '()'
            else:
                obj(node, path)
                segment(node.get('FunctionName'), path)
            # No argument rows: operands inside are operands of the enclosing assignment.
            for i, argument in enumerate(node, 1):
                argument_path = '{}/Argument[{}]'.format(path, i)
                segment(argument.get('Name'), argument_path)
                sole = len(operands(argument)) == 1
                for j, child in enumerate(argument, 1):
                    visit(child, prefix, '{}/{}[{}]'.format(argument_path, child.tag, j), sole)
            if node.tag == 'Function' and not owned:
                record(call, node, path)
            return
        if node.tag in {'Store', 'Variable'}:
            name = segment(node.get('Variable' if node.tag == 'Store' else 'Name'), path)
            child_prefix = name + ' := '
        elif node.tag in {'PropertySet', 'PropertyGet'}:
            name = obj(node, path) + '.' + segment(node.get('PropertyName'), path)
            child_prefix = name + ' := '
        elif node.tag == 'Function':
            call = prefix + function(node, path) + '()'
            arguments = [c for c in node if c.tag == 'Argument']
            # A call with no target whose every argument is a self-named read only
            # restates values already recorded; with no arguments it keeps its row.
            bare = not prefix and arguments and all(redundant(a) for a in arguments)
            name = None if owned or bare else call
            child_prefix = call + '.'
        elif node.tag == 'InstanceMethod':
            child_prefix = obj(node, path) + '.' + segment(node.get('FunctionName'), path) + '().'
        elif node.tag == 'RateQuery':
            factor = quoted(node.get('FactorName'))
            if node.get('FactorSource'):
                factor += ', ' + quoted(node.get('FactorSource'))
            name = segment(node.get('TableCode'), path) + '[' + factor + ']'
            child_prefix = name + '.'
        elif node.tag in {'Argument', 'QueryParam'}:
            name = prefix + segment(node.get('Name'), path)
            child_prefix = name + '.'
            if node.tag == 'Argument' and redundant(node):
                name = None  # Its only operand records the same value under its own name.
        # An assignment's or argument's only operand is the value it already holds.
        sole = node.tag in {'Store', 'PropertySet', 'Argument'} and len(operands(node)) == 1
        for i, child in enumerate(node, 1):
            visit(child, child_prefix, '{}/{}[{}]'.format(path, child.tag, i), sole)
        if name is not None:
            record(name, node, path)

    def record(name, node, path):
        # Post-order: an assignment's result follows the reads it was computed from.
        final[name] = recorded_value(node, path)

    visit(routine, '', routine_path)
    return final


class SafeTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise NormalizeError('unsupported_content', 'DTD/entity declarations are unsupported; supply plain worksheet XML')


def read_xml(source):
    try:
        with source.open('rb') as stream:
            return ET.parse(stream, parser=ET.XMLParser(target=SafeTreeBuilder())).getroot()
    except OSError as exc:
        raise NormalizeError('input_read', 'Cannot read input {}: {}'.format(source, exc)) from exc
    except ET.ParseError as exc:
        raise NormalizeError('malformed_xml', 'Malformed XML in {}: {}'.format(source, exc)) from exc


def save_json(result, destination, overwrite):
    temporary = None
    cleanup_warning = None
    try:
        if os.path.lexists(destination) and not overwrite:
            raise NormalizeError('output_exists', 'Destination exists: {}; choose a new path or explicitly use --overwrite'.format(destination))
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=str(destination.parent), prefix='.worksheet-normalize-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(str(temporary), str(destination))
        else:
            # Atomic no-clobber publication; an exists() check alone has a race.
            os.link(str(temporary), str(destination))
    except FileExistsError as exc:
        raise NormalizeError('output_exists', 'Destination exists: {}; choose a new path or explicitly use --overwrite'.format(destination)) from exc
    except OSError as exc:
        raise NormalizeError('output_write', 'Cannot write {}: {}; check the parent directory and permissions'.format(destination, exc)) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass  # Successful replace already removed the temporary name.
            except OSError as exc:
                # Never turn successful publication into a reported conversion
                # failure, or mask the original write error during cleanup.
                cleanup_warning = 'Could not remove temporary file {}: {}'.format(temporary, exc)
    return cleanup_warning


def local_path(value, label, category):
    try:
        path = Path(value).expanduser().absolute()
        return path, path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        # expanduser and older pathlib symlink-loop handling raise RuntimeError.
        raise NormalizeError(category, 'Cannot resolve {} path {!r}: {}; check the path and home directory'.format(label, str(value), exc)) from exc


def convert(source, destination=None, overwrite=False):
    source, source_resolved = local_path(source, 'input', 'input_read')
    if destination is None:
        destination = source.parent / DEFAULT_OUTPUT
    destination, destination_resolved = local_path(destination, 'output', 'output_write')
    # Refuse aliases, including symlinks and hard links, even with --overwrite.
    try:
        same = source_resolved == destination_resolved or (destination.exists() and source.exists() and os.path.samefile(source, destination))
    except OSError as exc:
        raise NormalizeError('input_read', 'Cannot resolve input/output paths: {}'.format(exc)) from exc
    if same:
        raise NormalizeError('input_output_same', 'Input and destination must be different files, even with --overwrite')
    try:
        result = normalize(read_xml(source))
    except RecursionError as exc:
        raise NormalizeError('unsupported_content', 'XML nesting exceeds the supported depth') from exc
    warning = save_json(result, destination, overwrite)
    status = {'status': 'ok', 'output': str(destination), 'worksheet_count': len(result['worksheets']),
              'identifier_count': sum(len(w['identifiers']) for w in result['worksheets'])}
    if warning:
        status['warnings'] = [warning]
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Local Worksheets XML file')
    parser.add_argument('--output', help='Destination JSON file; parent must exist (default: {} beside the input)'.format(DEFAULT_OUTPUT))
    parser.add_argument('--overwrite', action='store_true', help='Explicitly replace an existing destination')
    args = parser.parse_args()
    try:
        result = convert(args.input, args.output, args.overwrite)
    except NormalizeError as exc:
        print(json.dumps({'status': 'error', 'category': exc.category, 'message': str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
