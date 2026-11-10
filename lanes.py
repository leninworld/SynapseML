"""Whole affected baseline lanes; this file is not an execution authorization."""
import json
from pathlib import Path
import shutil
import time
from baseline_checks import (PROBE, classify, freeze, BaselineClassificationError,
                             collect_outcome, outcome_require)
from validation_runtime import require


def reports(lane, label, module, tasks, seconds, probe_names=(), required_suites=()):
    source = lane.source / module / 'target/test-reports'
    retained = lane.root / (label + '-junit')
    retained.mkdir()
    for old in source.glob('*.xml'):
        shutil.copy2(old, retained / ('prior-' + old.name))
        old.unlink()  # Only owned hosted generated reports, preserved above.
    try:
        lane.sbt(label, tasks, seconds, expected=(0, 1))
    finally:
        for report in source.glob('*.xml'):
            shutil.copy2(report, retained / report.name)
    paths = [p for p in retained.glob('*.xml') if not p.name.startswith('prior-')]
    try:
        result = classify(paths, lane.manifest, probe_names, bool(probe_names), required_suites)
        exit_code = lane.records[-1].get('exit')
        outcome_require(exit_code == 0 or result['expected_product_failures'] > 0,
                        label + ': sbt exit 1 without a classified failing test case')
    except BaselineClassificationError as error:
        (lane.root / (label + '-classification-failure.json')).write_text(json.dumps({
            'error': str(error), 'retained_reports': [str(path) for path in paths]}, indent=2))
        raise
    (lane.root / (label + '-selection.json')).write_text(json.dumps(result, indent=2))
    return result



def schema_reports(lane, label, module, tasks, seconds, suites, include_new=False,
                   expect_mutation=False, original=None, expected_ids=None):
    from schema_checks import classify_schema
    source = lane.source / module / 'target/test-reports'
    retained = lane.root / (label + '-junit')
    retained.mkdir()
    for old in source.glob('*.xml'):
        shutil.copy2(old, retained / ('prior-' + old.name))
        old.unlink()  # Owned hosted generated reports, retained first.
    try:
        lane.sbt(label, tasks, seconds, expected=(0, 1))
    finally:
        for report in source.glob('*.xml'):
            shutil.copy2(report, retained / report.name)
    paths = [path for path in retained.glob('*.xml') if not path.name.startswith('prior-')]
    try:
        result = classify_schema(paths, lane.manifest, suites, include_new, expect_mutation, original)
        outcome_require(lane.records[-1]['exit'] == (1 if result['failures'] else 0),
                        'SBT exit inconsistent with complete JUnit: ' + label)
        if expected_ids is not None:
            outcome_require({tuple(x) for x in result['ids']} == {tuple(x) for x in expected_ids},
                            'Retained original test roster mismatch: ' + label)
    except BaselineClassificationError as error:
        (lane.root / (label + '-classification-failure.json')).write_text(json.dumps({
            'error': str(error), 'retained_reports': [str(path) for path in paths]}, indent=2))
        raise
    (lane.root / (label + '-selection.json')).write_text(json.dumps(result, indent=2))
    return result


def root_lane(lane):
    kind = lane.name.removeprefix('root-')
    lane.check_forks()
    lane.datasets()
    lane.initial_admission('full-schema-' + kind, remaining_seconds=2100)
    lane.sbt('baseline-style-and-compile', ['scalastyle', 'Test / scalastyle', 'compile', 'Test / compile'], 900)
    deferred = []
    if kind == 'train':
        packages = [('train', 'core', 'com.microsoft.azure.synapse.ml.train.**',
                     lane.manifest['required_original_suites']['train'])]
    elif kind == 'automl':
        packages = [('automl', 'core', 'com.microsoft.azure.synapse.ml.automl.**',
                     lane.manifest['required_original_suites']['automl'])]
    elif kind == 'features':
        packages = [
            ('features', 'core', 'com.microsoft.azure.synapse.ml.featurize.** '
             'com.microsoft.azure.synapse.ml.isolationforest.**',
             lane.manifest['required_original_suites']['features']),
            ('vw', 'vw', 'com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitRegressor',
             ['com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitRegressor']),
            ('coverage', 'core', 'com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite',
             ['com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'])]
    else:
        raise RuntimeError('Unknown schema lane')
    baseline = {}
    for key, module, pattern, suites in packages:
        observed = collect_outcome(deferred, lambda k=key, m=module, q=pattern, ss=suites:
            schema_reports(lane, 'baseline-full-' + k, m, [m + '/testOnly ' + q], 900, ss,
                           expected_ids=lane.manifest['original_case_ids'][k]))
        if observed is not None:
            baseline[key] = observed
    freeze(lane, 'original-baseline-final')
    if kind == 'features':
        lane.inject([lane.manifest['scala_regression_path']])
        lane.sbt('baseline-new-tests-style-and-compile', ['Test / scalastyle', 'core/Test/compile'], 300)
        suites = ['com.microsoft.azure.synapse.ml.featurize.VerifyValueIndexer',
                  'com.microsoft.azure.synapse.ml.featurize.VerifyIndexToValue']
        collect_outcome(deferred, lambda: schema_reports(
            lane, 'baseline-schema-oracles', 'core', ['core/testOnly ' + ' '.join(suites)],
            600, suites, include_new=True, expect_mutation=True))
    require(not deferred, 'Baseline/oracle gate incomplete: ' + '; '.join(deferred))
    measured = sum(r.get('seconds', 0) for r in lane.records
                   if r['label'].startswith(('baseline-full-', 'baseline-schema-oracles')))
    lane.admission(time.monotonic(), extra_seconds=max(1200, 300 + 1.25 * measured), factor=0)
    lane.apply()
    lane.sbt('candidate-early-style-compile', ['scalastyle', 'Test / scalastyle', 'compile', 'Test / compile'], 900)
    if kind == 'features':
        suites = ['com.microsoft.azure.synapse.ml.featurize.VerifyValueIndexer',
                  'com.microsoft.azure.synapse.ml.featurize.VerifyIndexToValue']
        collect_outcome(deferred, lambda: schema_reports(
            lane, 'candidate-early-schema', 'core', ['core/testOnly ' + ' '.join(suites)],
            600, suites, include_new=True))
    for key, module, pattern, suites in packages:
        require(key in baseline, 'No complete original baseline available for ' + key)
        collect_outcome(deferred, lambda k=key, m=module, q=pattern, ss=suites:
            schema_reports(lane, 'candidate-full-' + k, m, [m + '/testOnly ' + q], 900, ss,
                           include_new=k == 'features', original=baseline[k]))
    lane.verify_candidate('root-final')
    (lane.root / 'deferred-outcomes.json').write_text(json.dumps(deferred, indent=2))
    require(not deferred, 'Deferred validation outcomes: ' + '; '.join(deferred))


def python_lane(lane):
    from generated_artifacts import artifact_manifest
    from format_checks import azure_black
    lane.command('required-Black-version', ['python', '-c',
                 'import black; assert black.__version__ == "22.3.0"; print(black.__version__)'], 15)
    lane.initial_admission('full-schema-python', remaining_seconds=2280)
    for role in ('baseline', 'candidate'):
        if role == 'candidate':
            measured = sum(r.get('seconds', 0) for r in lane.records
                           if r['label'].startswith('baseline-'))
            lane.admission(time.monotonic(), extra_seconds=max(1200, 300 + 1.25 * measured), factor=0)
            lane.apply()
        azure_black(lane, role, exclude_docs=True)
        artifact_manifest(lane, role)
        lane.command(role + '-installed-python-real-spark',
                     ['python', str(lane.packet / 'python_e2e.py'), role], 600)
        proof = json.loads((lane.root / (role + '-python-proof.json')).read_text())
        require(proof['complete'] and proof['role'] == role, 'Missing actual installed Python result')
        if role == 'baseline':
            freeze(lane, 'original-python-final')
        else:
            previous = json.loads((lane.root / 'baseline-python-proof.json').read_text())
            require(proof['known_long_signature'] == previous['known_long_signature'],
                    'Long result changed; requires investigation')
    lane.verify_candidate('python-final')
