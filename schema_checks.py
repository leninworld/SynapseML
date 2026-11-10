"""Exact schema regression and original-suite comparison; Docker execution only."""
import xml.etree.ElementTree as ET
from baseline_checks import BaselineClassificationError, outcome_require


def classify_schema(paths, manifest, required_suites=(), include_new=False,
                    expect_mutation=False, original=None):
    cases, failures, skipped, suites = {}, {}, set(), set()
    for path in paths:
        root = ET.parse(path).getroot()
        suites.add(root.get('name'))
        for case in root.iter('testcase'):
            key = (case.get('classname') or root.get('name'), case.get('name'))
            outcome_require(all(key) and key not in cases, 'Missing/duplicate schema case ID')
            cases[key] = True
            errors = [*case.findall('failure'), *case.findall('error')]
            if errors:
                failures[key] = [{'type': error.get('type'),
                                  'cause': error.get('message', '') + '\n' + (error.text or '')}
                                 for error in errors]
            if case.find('skipped') is not None:
                skipped.add(key)
    outcome_require(cases and set(required_suites) <= suites, 'Missing required schema suite')
    new = {tuple(item['id']) for item in manifest['schema_cases']}
    mutation = {tuple(item['id']) for item in manifest['schema_cases'] if item['baseline_mutation_failure']}
    outcome_require(set(cases) & new == (new if include_new else set()), 'Wrong schema regression selection')
    outcome_require(skipped <= {tuple(item) for item in manifest['allowed_original_ignored_ids']},
                    'Unexpected schema validation skip')
    observed_mutation = set()
    known = manifest['known_NaN']
    nan = tuple(known['id'])
    known_failure = None
    for key, errors in failures.items():
        cause = '\n'.join(error['cause'] for error in errors)
        outcome_require('OutOfMemoryError' not in cause, 'Resource failure is not a product oracle')
        if expect_mutation and key in mutation:
            outcome_require('SCHEMA_INPUT_MUTATED: input schema must remain unchanged' in cause,
                            'Wrong mutation failure cause: ' + repr(key))
            observed_mutation.add(key)
        elif key == nan:
            outcome_require(len(errors) == 1 and errors[0]['type'] == known['type'] and
                            known['token'] in cause, 'Changed original NaN failure signature')
            known_failure = {'id': list(key), 'type': known['type'], 'token': known['token']}
        else:
            raise BaselineClassificationError('Unexpected original/candidate failure: ' + repr(key))
    outcome_require(observed_mutation == (mutation if expect_mutation else set()),
                    'Missing exact baseline mutation failures')
    if original is not None:
        original_ids = {tuple(item) for item in original['ids']}
        outcome_require(set(cases) == original_ids | (new if include_new else set()),
                        'Original test selection changed')
        outcome_require(skipped == {tuple(item) for item in original['skipped']},
                        'Original skip selection changed')
        outcome_require(known_failure == original['known_NaN_failure'],
                        'Original NaN result changed; requires investigation')
    return {'ids': sorted(cases), 'skipped': sorted(skipped),
            'mutation_failures': sorted(observed_mutation),
            'known_NaN_failure': known_failure, 'all_tests_green': not failures,
            'failures': [{'id': list(key), 'errors': errors} for key, errors in failures.items()]}
