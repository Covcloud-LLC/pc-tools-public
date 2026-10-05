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
STATEMENTS = {'Store', 'PropertySet', 'InstanceMethod', 'ConditionalGroup'}
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
    'ConditionalGroup': set(), 'If': {'Result'}, 'Else': {'Result'},
    'Condition': set(), 'EndIf': set(),
}
CHILDREN = {
    'Worksheets': {'Worksheet'}, 'Worksheet': {'Routine'}, 'Routine': STATEMENTS,
    'Store': OPERANDS, 'PropertySet': OPERANDS, 'Argument': OPERANDS,
    'Function': {'Argument'}, 'RateQuery': {'QueryParam'},
    'InstanceMethod': {'Argument'}, 'ConditionalGroup': {'If', 'Else', 'EndIf'},
    'If': STATEMENTS | {'Condition'}, 'Else': STATEMENTS, 'Condition': OPERANDS,
}
REQUIRED = {
    'Store': {'Variable'}, 'Variable': {'Name'},
    'PropertySet': {'ObjectName', 'ObjectType', 'PropertyName'},
    'PropertyGet': {'ObjectName', 'ObjectType', 'PropertyName'},
    'Function': {'ClassName', 'Name'}, 'Argument': {'Name'},
    'RateQuery': {'TableCode', 'FactorName', 'Type'}, 'QueryParam': {'Name'},
    'InstanceMethod': {'FunctionName', 'ObjectName', 'ObjectType'},
    'If': {'Result'}, 'Else': {'Result'},
}
NUMERIC_TYPES = {
    'byte', 'short', 'int', 'long', 'float', 'double',
    'java.lang.Byte', 'java.lang.Short', 'java.lang.Integer', 'java.lang.Long',
    'java.lang.Float', 'java.lang.Double', 'java.math.BigInteger', 'java.math.BigDecimal',
}
STRING_TYPES = {'java.lang.String', 'java.lang.Character', 'char'}
BOOLEAN_TYPES = {'boolean', 'java.lang.Boolean'}
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
    attr = 'Result' if node.tag in {'Store', 'PropertySet'} else 'Value'
    value_type = node.get(attr + 'Type')
    raw = node.get(attr)
    if raw is None:
        return {'kind': 'null', 'type': value_type, 'value': None}
    if value_type in NUMERIC_TYPES:
        return {'kind': 'number', 'type': value_type, 'value': canonical_number(raw, path)}
    if value_type in BOOLEAN_TYPES:
        if raw not in {'true', 'false'}:
            raise NormalizeError('unsupported_content', '{}: invalid typed boolean'.format(path))
        return {'kind': 'boolean', 'type': value_type, 'value': raw == 'true'}
    if value_type is None or value_type in STRING_TYPES:
        return {'kind': 'string', 'type': value_type, 'value': raw}
    # Typekeys and enum-like values also retain their exact serialized form.
    return {'kind': 'opaque', 'type': value_type, 'value': raw, 'opaque': True}


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
    if node.tag == 'RateQuery' and node.get('Type') != 'SingleFactor':
        raise NormalizeError('unsupported_content', '{}: only SingleFactor queries are supported'.format(path))
    if node.tag in {'If', 'Else'} and node.get('Result') not in {'true', 'false'}:
        raise NormalizeError('unsupported_content', '{}: branch Result must be true or false'.format(path))
    if node.tag == 'Store' and node.get('Declaration', 'false') not in {'true', 'false'}:
        raise NormalizeError('unsupported_content', '{}: Declaration must be true or false'.format(path))
    if node.tag == 'Worksheet' and (len(node) != 1 or node[0].tag != 'Routine'):
        raise NormalizeError('unsupported_content', '{}: expected exactly one Routine'.format(path))
    if node.tag == 'If' and (not len(node) or node[0].tag != 'Condition' or sum(c.tag == 'Condition' for c in node) != 1):
        raise NormalizeError('unsupported_content', '{}: expected one leading Condition'.format(path))
    if node.tag in {'If', 'Else'} and node.get('Result') == 'false' and any(c.tag != 'Condition' for c in node):
        raise NormalizeError('unsupported_content', '{}: unexecuted branch contains statements'.format(path))
    if node.tag == 'ConditionalGroup':
        # A serialized group may contain several consecutive if/else/endif sets.
        state = branch = None
        for child in node:
            if child.tag == 'If' and state is None:
                state, branch = 'if', child.get('Result')
            elif child.tag == 'Else' and state == 'if':
                if child.get('Result') == branch:
                    # An if/else runs exactly one branch; equal results are contradictory evidence.
                    raise NormalizeError('unsupported_content', '{}: If and Else results must differ'.format(path))
                state = 'else'
            elif child.tag == 'EndIf' and state is not None:
                state = None
            else:
                raise NormalizeError('unsupported_content', '{}: unsupported conditional sequence'.format(path))
        if state is not None or not len(node):
            raise NormalizeError('unsupported_content', '{}: incomplete conditional sequence'.format(path))
    if 'Value' in node.attrib or 'ResultType' in node.attrib:
        recorded_value(node, path)  # Validate even non-identifier constants.
    for index, child in enumerate(node, 1):
        child_path = '{}/{}[{}]'.format(path, child.tag, index)
        if child.tag not in CHILDREN.get(node.tag, set()):
            raise NormalizeError('unsupported_content', '{}: unsupported child of {}'.format(child_path, node.tag))
        validate(child, child_path)


def key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def normalize(root):
    if root.tag != 'Worksheets':
        raise NormalizeError('unsupported_content', 'Expected a Worksheets document root')
    validate(root, '/Worksheets')
    if not len(root):
        raise NormalizeError('unsupported_content', 'Worksheets contains no Worksheet elements')
    worksheets = []
    for worksheet in root:
        final = {}

        def visit(node, context, path):
            identifier = None
            child_context = context
            if node.tag in {'Store', 'Variable'}:
                identifier = {'kind': 'variable', 'name': node.get('Variable' if node.tag == 'Store' else 'Name')}
            elif node.tag in {'PropertySet', 'PropertyGet'}:
                identifier = {'kind': 'property', 'name': node.get('PropertyName'),
                              'object': {'name': node.get('ObjectName'), 'type': node.get('ObjectType')}}
            elif node.tag in {'Function', 'InstanceMethod'}:
                call = {'kind': 'function' if node.tag == 'Function' else 'method',
                        'name': node.get('Name' if node.tag == 'Function' else 'FunctionName')}
                if node.tag == 'Function':
                    call['class'] = node.get('ClassName')
                    identifier = dict(call, context=context)
                else:
                    call['object'] = {'name': node.get('ObjectName'), 'type': node.get('ObjectType')}
                child_context = context + [call]
            elif node.tag == 'RateQuery':
                query = {'kind': 'query', 'table': node.get('TableCode'),
                         'factor': node.get('FactorName'), 'source': node.get('FactorSource', '')}
                identifier = dict(query, context=context)
                child_context = context + [query]
            elif node.tag in {'Argument', 'QueryParam'}:
                identifier = {'kind': 'argument' if node.tag == 'Argument' else 'parameter',
                              'name': node.get('Name'), 'context': context}
                # Nested calls under different named inputs have different owners.
                child_context = context + [{'kind': identifier['kind'], 'name': identifier['name']}]
            for i, child in enumerate(node, 1):
                visit(child, child_context, '{}/{}[{}]'.format(path, child.tag, i))
            if identifier is not None:
                record = {'identifier': identifier, 'value': recorded_value(node, path)}
                # Receiver displays are not property values or identifier identity.
                # Keep the last occurrence's recorded representation without parsing it.
                if node.tag == 'PropertyGet' and 'ObjectValue' in node.attrib:
                    record['receiver'] = {'type': node.get('ObjectType'), 'value': node.get('ObjectValue'), 'opaque': True}
                final[key(identifier)] = record

        visit(worksheet[0], [], '/Worksheet/Routine')
        worksheets.append({'metadata': dict(worksheet.attrib),
                           'routine': dict(worksheet[0].attrib),
                           'identifiers': [final[k] for k in sorted(final)]})
    return {'format': 'pc-worksheet-final-values', 'version': 2, 'worksheets': worksheets}


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


def convert(source, destination, overwrite=False):
    source, source_resolved = local_path(source, 'input', 'input_read')
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
    parser.add_argument('--output', required=True, help='Destination JSON file; parent must exist')
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
