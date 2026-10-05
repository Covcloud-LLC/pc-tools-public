"""Hand-specified expectations: no XML parser or converter used as an oracle.

Running this file writes the synthetic and paired expected JSON artifacts. Each record
is specified from the approved rules and small, human-readable XML fixtures.
"""
import json
from pathlib import Path

DECIMAL = 'java.math.BigDecimal'
INTEGER = 'java.lang.Integer'


def value(kind, data, type_name=None):
    result = {'kind': kind, 'type': type_name, 'value': data}
    if kind == 'opaque':
        result['opaque'] = True
    return result


def var(name):
    return {'kind': 'variable', 'name': name}


def prop(name, obj, type_name):
    return {'kind': 'property', 'name': name, 'object': {'name': obj, 'type': type_name}}


def call(name, cls):
    return {'kind': 'function', 'name': name, 'class': cls}


def query(table, factor):
    return {'kind': 'query', 'table': table, 'factor': factor, 'source': ''}


def record(identifier, val):
    return {'identifier': identifier, 'value': val}


def argument(name, owner, val, kind='argument'):
    return record({'kind': kind, 'name': name, 'context': [owner]}, val)


def result(owner, val):
    return record(dict(owner, context=[]), val)


def expected(product):
    if product == 'cp':
        metadata = {'Description': 'Synthetic building coverage', 'FixedId': 'CPBuildingCovGrp1Cost:synthetic-1'}
        routine = 'coverage_premium'
        adjusted = record(prop('AdjustedRate', 'costdata', 'CostData'), value('number', '0.0992', DECIMAL))
        adjusted['receiver'] = {'type': 'CostData', 'value': 'SyntheticCostData', 'opaque': True}
        records = [record(var('x'), value('number', '11', DECIMAL)),
                   record(var('nullable'), value('null', None)),
                   record(var('code'), value('string', '001', 'java.lang.String')),
                   adjusted, record(prop('TermAmount', 'costdata', 'CostData'), value('number', '992', DECIMAL))]
    elif product == 'pa':
        metadata = {'Description': 'Synthetic driver assignment', 'FixedId': 'PersonalVehicle:synthetic-1'}
        routine = 'assign_driver'
        driver = value('opaque', 'Synthetic Driver', 'entity.VehicleDriver')
        vehicle = value('opaque', 'Synthetic Vehicle', 'entity.PersonalVehicle')
        age = value('number', '20', INTEGER)
        fn = call('getYoungestDriver', 'app.lob.pa.rating.PARatingFunctions')
        lookup = query('pa_youthful_driver', 'Age')
        method = {'kind': 'method', 'name': 'setRounding', 'object': {'name': '_costdata', 'type': 'java.lang.String'}}
        records = [record(var('youthfulDriverLimit'), age), result(lookup, age),
                   argument('JURISDICTION', lookup, value('string', 'CA'), 'parameter'),
                   record(prop('AssignedDriver', 'driverassignmentinfo', 'app.lob.pa.rating.DriverAssignmentInfo'), driver),
                   result(fn, driver), argument('vehicle', fn, vehicle),
                   argument('youthfulDriverLimit', fn, age), record(var('vehicle'), vehicle),
                   record(var('previoustermamount'), value('null', None)),
                   argument('Mode', method, value('opaque', 'HALF_UP', 'java.math.RoundingMode'))]
    else:
        metadata = {'Description': 'Synthetic key factor', 'FixedId': 'HOPDwelling:synthetic-1'}
        routine = 'key_factor'
        line = value('opaque', 'Synthetic Homeowners Line', 'productmodel.HOPLine')
        limit = value('number', '100000', DECIMAL)
        factor = value('number', '0.01', DECIMAL)
        fn = call('getPrimaryCoverageLimit', 'app.lob.hop.rating.HOPRatingFunctions')
        lookup = query('hop_key_factor', 'Factor')
        records = [record(var('limit'), limit), result(fn, limit), argument('line', fn, line),
                   record(var('policyline'), line),
                   record(prop('KeyFactor', 'hopbasepremiuminfo', 'app.lob.hop.rating.HOPBasePremiumInfo'), factor),
                   result(lookup, factor), argument('COVERAGE_PART', lookup, value('string', 'hopdwelling'), 'parameter'),
                   argument('LIMIT', lookup, value('string', '<<100000,100000>>'), 'parameter')]
    metadata.update(EffectiveDate='2026-01-01', ExpirationDate='2027-01-01')
    return {'format': 'pc-worksheet-final-values', 'version': 2, 'worksheets': [{
        'metadata': metadata,
        'routine': {'RateBookCode': ('hop' if product == 'homeowners' else product) + '_synthetic',
                    'RateBookEdition': '1', 'RoutineCode': routine, 'RoutineVersion': '1'},
        'identifiers': sorted(records, key=lambda r: json.dumps(r['identifier'], sort_keys=True, separators=(',', ':')))}]}


def comparison_expected(product, side):
    """Independent final states for the comparison illustrations; no XML reads."""
    if product == 'cp':
        description, routine, book = 'Building Coverage Basic Group I', 'cp_cov_premium_rr', 'cp_synthetic'
        rows = ([('a', 'first', '20'), ('b', 'first', '40'), ('a', 'second', '20')]
                if side == 'before' else [('b', 'first', '40'), ('a', 'second', '20'), ('a', 'first', '21')])
        subject_type = 'CPBuildingCovGrp1Cost'
    elif product == 'pa':
        description = 'Driver assignment for vehicle Synthetic Vehicle, driver Synthetic Driver'
        routine, book, subject_type = 'pa_assign_driver_style2_rr', 'pa_synthetic', 'PersonalVehicle'
        rows = ([('a', 'driver:a', '10'), ('a', 'driver:b', '20')]
                if side == 'before' else [('a', 'driver:b', '20'), ('a', 'driver:a', '11')])
    else:
        description, routine, book = 'Base Premium', 'hop_base_rate_premium_rr', 'hop_synthetic'
        subject_type = 'HOPDwellingCovCost'
        rows = ([('a', None, '20'), ('b', None, '20')]
                if side == 'before' else [('x', None, '20'), ('y', None, '20')])
    worksheets = []
    for subject, context, amount in rows:
        metadata = dict(FixedId=subject_type + ':' + subject, Description=description,
                        EffectiveDate='2026-01-01', ExpirationDate='2027-01-01')
        if product == 'pa':
            metadata['Tag'] = context
        elif product == 'cp':
            metadata['EffectiveDate' if context == 'second' else 'ExpirationDate'] = '2026-07-01'
        worksheets.append({
            'metadata': metadata,
            'routine': dict(RateBookCode=book, RateBookEdition='2' if product == 'cp' and side == 'after' else '1',
                            RoutineCode=routine, RoutineVersion='1'),
            'identifiers': [record(var('finalAmount'), value('number', amount, DECIMAL))]})
    return {'format': 'pc-worksheet-final-values', 'version': 2, 'worksheets': worksheets}


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2] / 'examples/worksheets/normalized'
    for name in ['cp', 'pa', 'homeowners']:
        (root / (name + '-synthetic.expected.json')).write_text(json.dumps(expected(name), sort_keys=True, indent=2) + '\n')
        for side in ['before', 'after']:
            (root / 'comparison' / (name + '-' + side + '.expected.json')).write_text(
                json.dumps(comparison_expected(name, side), sort_keys=True, indent=2) + '\n')
