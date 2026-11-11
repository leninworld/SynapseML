"""Unmodified-master diagnostics and measured full-finish feasibility, Docker only."""
import json
import re
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET

from validation_runtime import check_junit, require


def complete_suites(lane, label, module, task, expected_suites=(), allow_known_nan=False, feature_failures=None):
    feature_failures = feature_failures or {}
    reports = lane.source / module / 'target/test-reports'
    retained = lane.root / (label + '-junit')
    retained.mkdir()
    for old in reports.glob('*.xml'):
        shutil.copy2(old, retained / ('prior-' + old.name))
        old.unlink()  # Only copied generated reports on the owned hosted runner.
    try:
        lane.sbt(label, task, 900, expected=(0, 1) if allow_known_nan or feature_failures else (0,))
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
            if key in feature_failures:
                require(all(token in text.lower() for token in feature_failures[key]), 'Wrong feature failure cause')
                failures.append(key)
                continue
            require(allow_known_nan and key == (
                'com.microsoft.azure.synapse.ml.train.VerifyTrainClassifier',
                'Verify you can train on a dataset that contains a vector column') and
                'Vector values MUST NOT be NaN or Infinity' in text and
                '[3.0,4.0,0.34,0.12,NaN,0.2,-1.23]' in text,
                'Unexpected baseline test failure')
            failures.append(key)
    require(not feature_failures or set(failures) == set(feature_failures), 'Missing feature regression failure')
    require(lane.records[-1]['exit'] == (1 if failures else 0), 'Test exit/report mismatch')
    result = check_junit(current, ids, expected_failures=failures)
    (lane.root / (label + '-result.json')).write_text(json.dumps(
        {'observed': result, 'ids': ids, 'tasks': task, 'known_failures_not_passes': failures, 'candidate_validated': lane.candidate}, indent=2))
    return {'ids': ids, 'failures': failures, 'observed': result}


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
        lane = SimpleNamespace(root=owned, source=owned / 'source', records=[], candidate=False)
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


    feature_fixture(root/'feature-failures')

def feature_fixture(root):
    from types import SimpleNamespace
    root.mkdir()
    suite = 'com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitClassifier'
    messages = {'vector': 'DoubleType did not equal VectorUDT',
                'mismatch': 'Expected exception java.lang.Exception to be thrown, but no exception was thrown'}
    wanted = {(suite,'vector'): ['did not equal'], (suite,'mismatch'): ['expected exception','to be thrown']}
    for index, mutation in enumerate(['valid','wrong-cause','missing','extra']):
        owned=root/str(index);owned.mkdir()
        lane=SimpleNamespace(root=owned,source=owned/'source',records=[],candidate=False)
        reports=lane.source/'vw/target/test-reports';reports.mkdir(parents=True)
        def fake_sbt(label,tasks,seconds,expected):
            xml=ET.Element('testsuite',name=suite)
            for name,message in messages.items():
                case=ET.SubElement(xml,'testcase',classname=suite,name=name)
                if mutation=='missing' and name=='mismatch':continue
                ET.SubElement(case,'failure',type='org.scalatest.exceptions.TestFailedException',
                              message='wrong cause' if mutation=='wrong-cause' and name=='vector' else message)
            if mutation=='extra':
                extra=ET.SubElement(xml,'testcase',classname=suite,name='unexpected')
                ET.SubElement(extra,'failure',message='different assertion')
            ET.ElementTree(xml).write(reports/'TEST-feature-fixture.xml')
            lane.records.append({'exit':1})
        lane.sbt=fake_sbt
        try:complete_suites(lane,'actual-feature-classifier','vw',['synthetic'],feature_failures=wanted)
        except RuntimeError as error:
            expected={'wrong-cause':'Wrong feature failure cause','missing':'Missing feature regression failure',
                      'extra':'Unexpected baseline test failure'}
            require(mutation in expected and str(error)==expected[mutation],'Wrong feature fixture rejection')
        else:require(mutation=='valid','Invalid feature evidence accepted')


def heap_proof(lane, role):
    out = lane.command(role + '-effective-default-JVM-heap', ['java','-XX:+PrintFlagsFinal','-version'],30)
    actual = re.findall(r'\bMaxHeapSize\s*=\s*(\d+)',out)
    require(len(actual)==1 and int(actual[0])==2684354560,'Default JVM heap drifted from 10GiB baseline')
    (lane.root/(role+'-heap-proof.json')).write_text(json.dumps({'MaxHeapSize':int(actual[0]),'MaxRAM':10737418240,'SBT_Xmx_bytes':4294967296,'Spark_driver_bytes':2147483648},indent=2))


def root_lane(lane):
    lane.check_forks()
    lane.datasets()
    heap_proof(lane,'baseline')
    lane.initial_admission('root-paired-build', remaining_seconds=2100)
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
    lane.sbt('baseline-compile-style', ['scalastyle', 'Test / scalastyle', 'compile', 'Test / compile'], 900)
    scala_probe(lane, 'baseline')
    baseline_vw = complete_suites(lane, 'baseline-full-VW', 'vw', ['vw/test'],
                    ['com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitClassifier'])
    core = ['com.microsoft.azure.synapse.ml.train.VerifyTrainClassifier',
            'com.microsoft.azure.synapse.ml.train.VerifyComputeModelStatistics',
            'com.microsoft.azure.synapse.ml.train.ComputeModelStatisticsValidationSuite',
            'com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite']
    baseline_core = complete_suites(lane, 'baseline-core-consumers', 'core', ['core/testOnly ' + ' '.join(core)], core, True)
    lane.inject(['vw/src/test/scala/com/microsoft/azure/synapse/ml/vw/VerifyVowpalWabbitClassifier.scala'])
    wanted = {tuple(key.split('::', 1)): value for key,value in lane.manifest['feature_failures'].items()}
    regression = complete_suites(lane, 'baseline-new-regressions', 'vw',
                    ['vw/testOnly com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitClassifier -- -z "VW raw"'],
                    feature_failures=wanted)
    all_new = {tuple(key.split('::',1)) for key in lane.manifest['all_new_test_ids']}
    require(set(regression['ids']) == all_new, 'Incomplete baseline feature/control selection')
    lane.admission(started, extra_seconds=480, factor=1.25)
    lane.apply()
    heap_proof(lane,'candidate')
    lane.sbt('candidate-compile-style', ['scalastyle', 'Test / scalastyle', 'compile', 'Test / compile'], 900)
    complete_suites(lane, 'candidate-early-regressions', 'vw',
                    ['vw/testOnly com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitClassifier -- -z "VW raw"'])
    scala_probe(lane, 'candidate')
    candidate_vw = complete_suites(lane, 'candidate-full-VW', 'vw', ['vw/test'])
    candidate_core = complete_suites(lane, 'candidate-core-consumers', 'core', ['core/testOnly ' + ' '.join(core)], core, True)
    require(set(candidate_vw['ids']) == set(baseline_vw['ids']) | all_new, 'VW test scope changed')
    require(set(candidate_core['ids']) == set(baseline_core['ids']) and
            candidate_core['failures'] == baseline_core['failures'], 'Core baseline failure or scope changed')
    lane.known_failures = candidate_core['failures']


def scala_probe(lane, role):
    import os
    os.environ['VALIDATION_SOURCE_KIND'] = 'candidate' if lane.candidate else 'unmodified-master-baseline-only'
    lane.sbt(role + '-all84-modes-native18', ['vw/Test/runMain '
        'com.microsoft.azure.synapse.ml.vw.RawPredictionBaselineMain /runtime/scala-' + role + '-proof.json'], 480)
    if role == 'baseline':
        lane.command('classify-baseline-evidence', ['python3', str(lane.packet / 'baseline_oracles.py'),
            str(lane.root / 'scala-baseline-proof.json'), str(lane.root / 'baseline-diagnostic.json')], 15)
    else:
        lane.command('verify-candidate-all84', ['python3', str(lane.packet / 'candidate_oracles.py'), 'scala',
            str(lane.root / 'scala-baseline-proof.json'), str(lane.root / 'scala-candidate-proof.json'),
            str(lane.root / 'candidate-diagnostic.json')], 15)


def full_python(lane, label):
    complete_suites(lane, label + '-full-Python-codegen', 'core',
                    ['core/testOnly com.microsoft.azure.synapse.ml.codegen.PyCodegenSuite'],
                    ['com.microsoft.azure.synapse.ml.codegen.PyCodegenSuite'])
    lane.command(label + '-installed-Spark', ['python', str(lane.packet / 'python_e2e.py'), label], 480)
    if label == 'candidate':
        lane.command('candidate-installed-Python-oracle', ['python', str(lane.packet / 'candidate_oracles.py'), 'python', str(lane.root / 'python-baseline-e2e-proof.json'), str(lane.root / 'python-candidate-e2e-proof.json'), str(lane.root / 'python-candidate-oracle.json')], 15)
    report = lane.source / 'target/python-test-results-synapseml-vw.xml'
    if report.exists():
        shutil.copy2(report, lane.root / (label + '-prior-VW-Python.xml'))
        report.unlink()  # Generated report only on this owned hosted runner.
    try:
        lane.sbt(label + '-full-VW-Python', ['vw/testPython'], 600)
    finally:
        if report.is_file(): shutil.copy2(report, lane.root / (label + '-full-VW-Python.xml'))
    require(report.is_file(), 'Missing full generated VW Python JUnit')
    cases = list(ET.parse(report).getroot().iter('testcase'))
    ids = [(case.get('classname'), case.get('name')) for case in cases]
    result = check_junit([report], ids)
    (lane.root / (label + '-full-VW-Python-result.json')).write_text(json.dumps({'ids':ids,'observed':result,'unfiltered':True,'candidate_validated':lane.candidate},indent=2))
    return ids


def python_lane(lane):
    from generated_artifacts import artifact_manifest
    from format_checks import azure_black
    lane.command('required-Black-version', ['python','-c','import black; assert black.__version__ == "22.3.0"'],15)
    azure_black(lane, 'baseline', exclude_docs=True)
    lane.initial_admission('paired-Python-build', remaining_seconds=1980)
    lane.datasets()
    heap_proof(lane,'baseline')
    lane.inject([lane.manifest['scala_regression_path']])
    started = time.monotonic()
    artifact_manifest(lane, 'baseline')
    baseline_ids = full_python(lane, 'baseline')
    lane.admission(started, extra_seconds=480, factor=1.25)
    lane.apply()
    heap_proof(lane,'candidate')
    lane.sbt('candidate-early-compile-style', ['vw/scalastyle','vw/Test/scalastyle','vw/compile','vw/Test/compile'],900)
    complete_suites(lane, 'candidate-early-regressions', 'vw', ['vw/testOnly com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitClassifier -- -z "VW raw"'])
    artifact_manifest(lane, 'candidate')
    candidate_ids = full_python(lane, 'candidate')
    require(set(candidate_ids) == set(baseline_ids), 'Python suite scope changed')
