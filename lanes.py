"""Unmodified-master diagnostics and measured full-finish feasibility, Docker only."""
import json
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET

from validation_runtime import check_junit, require


def complete_suites(lane, label, module, task, expected_suites=(), allow_known_nan=False):
    reports = lane.source / module / 'target/test-reports'
    retained = lane.root / (label + '-junit')
    retained.mkdir()
    for old in reports.glob('*.xml'):
        shutil.copy2(old, retained / ('prior-' + old.name))
        old.unlink()  # Only copied generated reports on the owned hosted runner.
    try:
        lane.sbt(label, task, 900, expected=(0, 1) if allow_known_nan else (0,))
    finally:
        for report in reports.glob('*.xml'):
            shutil.copy2(report, retained / report.name)
    current = [p for p in retained.glob('*.xml') if not p.name.startswith('prior-')]
    ids = [(case.get('classname'), case.get('name')) for p in current
           for case in ET.parse(p).getroot().iter('testcase')]
    require(ids and set(expected_suites) <= {suite for suite, _ in ids}, 'Incomplete real test selection')
    failures = []
    for report in current:
        for case in ET.parse(report).getroot().iter('testcase'):
            errors = case.findall('failure') + case.findall('error')
            if not errors:
                continue
            key = (case.get('classname'), case.get('name'))
            text = '\n'.join((e.get('message', '') + '\n' + (e.text or '')) for e in errors)
            require(allow_known_nan and key == (
                'com.microsoft.azure.synapse.ml.train.VerifyTrainClassifier',
                'Verify you can train on a dataset that contains a vector column') and
                'Vector values MUST NOT be NaN or Infinity' in text and
                '[3.0,4.0,0.34,0.12,NaN,0.2,-1.23]' in text,
                'Unexpected baseline test failure')
            failures.append(key)
    require(lane.records[-1]['exit'] == (1 if failures else 0), 'Test exit/report mismatch')
    result = check_junit(current, ids, expected_failures=failures)
    (lane.root / (label + '-result.json')).write_text(json.dumps(
        {'observed': result, 'ids': ids, 'tasks': task, 'known_failures_not_passes': failures, 'candidate_validated': False}, indent=2))


def fixture(root):
    from types import SimpleNamespace
    root.mkdir()
    suite = 'com.microsoft.azure.synapse.ml.train.VerifyTrainClassifier'
    name = 'Verify you can train on a dataset that contains a vector column'
    cause = 'Vector values MUST NOT be NaN or Infinity [3.0,4.0,0.34,0.12,NaN,0.2,-1.23]'
    for index, (message, code, expected) in enumerate([
            (cause, 1, None), ('different failure', 1, 'Unexpected baseline test failure'),
            (cause, 0, 'Test exit/report mismatch'), (None, 0, None)]):
        owned = root / str(index)
        owned.mkdir()
        lane = SimpleNamespace(root=owned, source=owned / 'source', records=[])
        reports = lane.source / 'core/target/test-reports'
        reports.mkdir(parents=True)

        def fake_sbt(label, tasks, seconds, expected):
            document = ET.Element('testsuite', name=suite)
            case = ET.SubElement(document, 'testcase', classname=suite, name=name)
            if message is not None:
                ET.SubElement(case, 'failure', message=message)
            ET.ElementTree(document).write(reports / 'TEST-fixture.xml')
            lane.records.append({'exit': code})

        lane.sbt = fake_sbt
        try:
            complete_suites(lane, 'actual-xml-parser', 'core', ['synthetic'], [suite], True)
        except RuntimeError as error:
            require(expected is not None and str(error) == expected, 'Wrong JUnit fixture rejection')
        else:
            require(expected is None, 'Invalid baseline JUnit accepted')
            receipt = json.loads((owned / 'actual-xml-parser-result.json').read_text())
            require(len(receipt['known_failures_not_passes']) == (1 if message else 0),
                    'Known failure falsely counted as a pass')


def root_lane(lane):
    lane.check_forks()
    lane.datasets()
    lane.initial_admission('root-and-VW-baseline', remaining_seconds=2100)
    started = time.monotonic()
    lane.sbt('early-vw-update', ['vw/update'], 300)
    classpath = lane.sbt('early-vw-dependency-classpath', ['export vw/Compile/dependencyClasspath'], 120)
    lines = [line.strip() for line in classpath.splitlines() if line.startswith('/runtime/')]
    require(len(lines) == 1, 'Ambiguous native dependency classpath')
    native = [p for p in lines[0].split(':') if Path(p).name == 'vw-jni-9.3.0.jar']
    require(len(native) == 1, 'Missing resolved native dependency')
    lane.command('early-real-VW-ldd', ['python3', str(lane.packet / 'native_preflight.py'), native[0],
                 str(lane.root / 'tmp/vw-native-check'), '4b696f216a6baa702458433df68e5c6dc9dcbd7e',
                 str(lane.root / 'native-dependency-proof.json')], 30)
    lane.inject([lane.manifest['scala_regression_path']])
    lane.sbt('early-compile-style', ['scalastyle', 'Test / scalastyle', 'compile', 'Test / compile'], 900)
    lane.sbt('baseline-affected-modes', ['vw/Test/runMain '
             'com.microsoft.azure.synapse.ml.vw.RawPredictionBaselineMain /runtime/scala-baseline-proof.json'], 480)
    lane.command('classify-baseline-evidence', ['python3', str(lane.packet / 'baseline_oracles.py'),
                 str(lane.root / 'scala-baseline-proof.json'), str(lane.root / 'baseline-diagnostic.json')], 15)
    lane.admission(started, extra_seconds=1500, factor=0)
    complete_suites(lane, 'baseline-full-VW', 'vw', ['vw/test'],
                    ['com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitClassifier'])
    core = ['com.microsoft.azure.synapse.ml.train.VerifyTrainClassifier',
            'com.microsoft.azure.synapse.ml.train.VerifyComputeModelStatistics',
            'com.microsoft.azure.synapse.ml.train.ComputeModelStatisticsValidationSuite',
            'com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite']
    complete_suites(lane, 'baseline-codegen-and-consumers', 'core', ['core/testOnly ' + ' '.join(core)], core, allow_known_nan=True)


def python_lane(lane):
    from generated_artifacts import artifact_manifest
    from format_checks import azure_black
    lane.command('required-Black-version', ['python', '-c',
                 'import black; assert black.__version__ == "22.3.0"; print(black.__version__)'], 15)
    azure_black(lane, 'baseline', exclude_docs=True)
    lane.initial_admission('generated-Python-baseline-build', remaining_seconds=1980)
    lane.datasets()
    started = time.monotonic()
    artifact_manifest(lane, 'baseline')
    lane.admission(started, extra_seconds=1260, factor=0)
    complete_suites(lane, 'baseline-full-Python-codegen', 'core',
                    ['core/testOnly com.microsoft.azure.synapse.ml.codegen.PyCodegenSuite'],
                    ['com.microsoft.azure.synapse.ml.codegen.PyCodegenSuite'])
    lane.command('built-Python-real-Spark-baseline', ['python', str(lane.packet / 'python_e2e.py')], 480)
    proof = json.loads((lane.root / 'python-e2e-proof.json').read_text())
    require(proof['baseline_schema_gap_reproduced'] and not proof['candidate_validated'],
            'Python evidence role or baseline missing')
    report = lane.source / 'target/python-test-results-synapseml-vw.xml'
    require(not report.exists(), 'Pre-existing Python test evidence')
    try:
        lane.sbt('baseline-full-VW-Python', ['vw/testPython'], 600)
    finally:
        if report.is_file():
            shutil.copy2(report, lane.root / 'full-VW-Python.xml')
    require(report.is_file(), 'Missing full generated VW Python JUnit')
    cases = list(ET.parse(report).getroot().iter('testcase'))
    ids = [(case.get('classname'), case.get('name')) for case in cases]
    result = check_junit([report], ids)
    (lane.root / 'full-VW-Python-result.json').write_text(json.dumps(
        {'ids': ids, 'observed': result, 'unfiltered': True, 'candidate_validated': False}, indent=2))
