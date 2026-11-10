"""Classify actual baseline reports without converting arbitrary failures to proof."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from validation_runtime import require

PROBE = 'com.microsoft.azure.synapse.ml.train.EncodedLabelMetricsBaselineProbe'


def classify(paths, manifest, required_probe=(), allow_probe=False, required_suites=()):
    cases, failures, skipped = {}, {}, {}
    suites = set()
    for path in paths:
        root = ET.parse(path).getroot()
        suites.add(root.get('name'))
        for case in root.iter('testcase'):
            key = (case.get('classname') or root.get('name'), case.get('name'))
            require(all(key) and key not in cases, 'Missing/duplicate baseline case ID')
            cases[key] = str(path)
            errors = [*case.findall('failure'), *case.findall('error')]
            if errors:
                failures[key] = '\n'.join(x.get('message', '') + '\n' + (x.text or '') for x in errors)
            if case.find('skipped') is not None:
                skipped[key] = ET.tostring(case, encoding='unicode')
    require(cases, 'No actual baseline cases')
    require(set(required_suites) <= suites, 'Missing required baseline suite')
    expected_probe = set(manifest['expected_regression_names'] + manifest['expected_control_names'])
    actual_probe = {name for suite, name in cases if suite == PROBE}
    require(actual_probe == set(required_probe), 'Private baseline witness selection mismatch')
    allowed_ignored = {tuple(x) for x in manifest['allowed_original_ignored_ids']}
    require(set(skipped) <= allowed_ignored, 'Unexpected baseline skip')
    for (suite, name), cause in failures.items():
        require(allow_probe and suite == PROBE and name in manifest['expected_regression_names'],
                'Original suite or control failed: ' + suite + '/' + name)
        require('CMS_LABEL_ENCODING_REGRESSION' in cause or
                ('key not found' in cause.lower() and 'ComputeModelStatistics' in cause),
                'Wrong baseline failure cause: ' + name)
    require(actual_probe <= expected_probe, 'Unknown private baseline case')
    return {'observed_ids': sorted(cases), 'failure_causes':
            [{'id': list(k), 'cause': v} for k, v in failures.items()],
            'ignored_ids': sorted(skipped), 'original_cases': sum(k[0] != PROBE for k in cases),
            'probe_cases': len(actual_probe), 'expected_product_failures': len(failures)}


def freeze(lane, label):
    tracked = lane.command(label + '-tracked-delta', ['git', 'diff', '--name-only', 'HEAD'], 10)
    require(not tracked.strip(), 'Baseline production or benchmark changed')
    path = lane.manifest['witness_file'].removeprefix('witness-files/')
    target = lane.source / path
    if target.exists():
        import hashlib
        require(hashlib.sha256(target.read_bytes()).hexdigest() == lane.manifest['witness_SHA256'],
                'Private baseline witness changed')
    (lane.root / (label + '-freeze.json')).write_text(json.dumps({
        'tracked_changes': [], 'witness_present': target.exists(),
        'product_source_role': 'UNMODIFIED_BASELINE'}, indent=2))
