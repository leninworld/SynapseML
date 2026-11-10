"""Exercise the actual changed classifier with XML before dependencies/builds."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from schema_checks import classify_schema
from baseline_checks import BaselineClassificationError
from validation_runtime import docker_only, require


def main():
    docker_only()
    root = Path('/runtime') / ('schema-d0-' + __import__('os').environ['VALIDATION_PHASE'])
    root.mkdir()
    xml = root / 'actual-parser-fixture.xml'
    rules = {'schema_cases': [{'id': ['Fixture', 'mutation'], 'baseline_mutation_failure': True},
                              {'id': ['Fixture', 'append'], 'baseline_mutation_failure': False}],
             'allowed_original_ignored_ids': [],
             'known_NaN': {'id': ['Fixture', 'known'], 'type': 'org.apache.spark.SparkException',
                           'token': 'Vector values MUST NOT be NaN or Infinity'}}

    def write(failure=None, failure_type='org.scalatest.exceptions.TestFailedException', known=False):
        suite = ET.Element('testsuite', name='Fixture')
        case = ET.SubElement(suite, 'testcase', name='mutation')
        if failure:
            ET.SubElement(case, 'failure', type=failure_type, message=failure)
        ET.SubElement(suite, 'testcase', name='append')
        if known:
            case = ET.SubElement(suite, 'testcase', name='known')
            ET.SubElement(case, 'failure', type=failure_type,
                          message=rules['known_NaN']['token'])
        ET.ElementTree(suite).write(xml, encoding='unicode')

    def reject(action):
        try:
            action()
        except BaselineClassificationError:
            return
        raise RuntimeError('Incorrect schema classification accepted')

    write('SCHEMA_INPUT_MUTATED: input schema must remain unchanged')
    baseline = classify_schema([xml], rules, ['Fixture'], True, True)
    require(len(baseline['mutation_failures']) == 1, 'Mutation positive fixture')
    write('arbitrary Spark exception')
    reject(lambda: classify_schema([xml], rules, ['Fixture'], True, True))
    write()
    candidate = classify_schema([xml], rules, ['Fixture'], True)
    require(candidate['all_tests_green'], 'Candidate positive fixture')
    reject(lambda: classify_schema([xml], rules, ['Fixture'], True, True))
    reject(lambda: classify_schema([xml], rules, ['Missing'], True))
    write(known=True, failure_type='org.apache.spark.SparkException')
    accepted = classify_schema([xml], rules, ['Fixture'], True)
    require(accepted['known_NaN_failure'] and not accepted['all_tests_green'], 'NaN is not a pass')
    write(known=True, failure_type='java.lang.IllegalArgumentException')
    reject(lambda: classify_schema([xml], rules, ['Fixture'], True))
    write('SCHEMA_INPUT_MUTATED: input schema must remain unchanged')
    reject(lambda: classify_schema([xml], rules, ['Fixture'], True))
    write()
    r = ET.parse(xml)
    r.getroot().append(ET.Element('testcase', name='append'))
    r.write(xml, encoding='unicode')
    reject(lambda: classify_schema([xml], rules, ['Fixture'], True))
    (root / 'result.json').write_text(json.dumps({'passed': True, 'positive_negative_cases': 9}))


if __name__ == '__main__':
    main()
