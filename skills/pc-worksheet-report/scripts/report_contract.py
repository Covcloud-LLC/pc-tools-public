"""Strict primitives for evidence embedded in comparison v1 (stdlib only)."""
import json
import re


class ReportError(Exception):
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
        raise ReportError('invalid_input', '{}: {}'.format(path, message))


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
