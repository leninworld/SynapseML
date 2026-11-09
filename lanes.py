"""The complete #2817 Scala and installed-Python workloads; never execute on Mac."""
import json
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET
from validation_runtime import check_junit, require

SUITE = 'com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitGeneric'
PRESERVATION = 'Generic model preserves complete input schemas for scalar and array predictions'
COLLISIONS = 'Generic prediction name collisions follow Spark case sensitivity'


def clear_reports(lane, module, label):
    reports = lane.source / module / 'target/test-reports'
    retained = lane.root / (label + '-junit'); retained.mkdir()
    for old in reports.glob('*.xml'):
        shutil.copy2(old, retained / ('prior-' + old.name))
        old.unlink()  # Only owned hosted generated XML, copied first.
    return reports, retained


def selected_vw(lane, label, filter_text, expected_names, expected_failures=()):
    reports, retained = clear_reports(lane, 'vw', label)
    output = ''
    try:
        output = lane.sbt(label, ['vw/testOnly ' + SUITE + ' -- -z "' + filter_text + '"'],
                          300, expected=(1,) if expected_failures else (0,))
    finally:
        for report in reports.glob('*.xml'): shutil.copy2(report, retained / report.name)
    matches = [p for p in retained.glob('*.xml') if not p.name.startswith('prior-')
               and ET.parse(p).getroot().get('name') == SUITE]
    require(len(matches) == 1, 'Missing or duplicate actual VW regression report')
    result = check_junit(matches, [(SUITE, name) for name in expected_names],
                         [(SUITE, name) for name in expected_failures])
    if expected_failures:
        require('GENERIC_INPUT_SCHEMA_PRESERVATION_REGRESSION' in output,
                'Baseline failure did not show the intended schema contract')
    (lane.root / (label + '-selection.json')).write_text(json.dumps(result, indent=2))


def root_lane(lane):
    lane.check_forks()
    lane.datasets()
    lane.initial_admission('full-root-and-VW', remaining_seconds=2280)
    started = time.monotonic()
    lane.sbt('early-vw-update', ['vw/update'], 300)
    classpath = lane.sbt('early-vw-dependency-classpath', ['export vw/Compile/dependencyClasspath'], 120)
    lines = [line.strip() for line in classpath.splitlines() if line.startswith('/runtime/')]
    require(len(lines) == 1, 'Ambiguous native dependency classpath')
    native = [p for p in lines[0].split(':') if Path(p).name == 'vw-jni-9.3.0.jar']
    require(len(native) == 1, 'Missing resolved vw-jni 9.3.0')
    lane.command('early-real-VW-ldd', ['python3', str(lane.packet/'native_preflight.py'), native[0],
                 str(lane.root/'tmp/vw-native-check'), '4b696f216a6baa702458433df68e5c6dc9dcbd7e',
                 str(lane.root/'native-dependency-proof.json')], 30)
    lane.inject([lane.manifest['scala_regression_path']])
    lane.sbt('baseline-root-compile', ['compile', 'Test / compile'], 900)
    selected_vw(lane, 'baseline-native-control', 'from string', ['Verify VowpalWabbitGeneric from string'])
    selected_vw(lane, 'baseline-schema-regression', 'preserves complete input schemas',
                [PRESERVATION], [PRESERVATION])
    # Real workload baseline, not a copied resource threshold or an OOM retry.
    lane.admission(started, extra_seconds=1980, factor=0)
    lane.apply()
    lane.sbt('candidate-root-checks', ['scalastyle', 'Test / scalastyle', 'compile', 'Test / compile'], 600)
    selected_vw(lane, 'candidate-schema-regression', 'preserves complete input schemas', [PRESERVATION])
    selected_vw(lane, 'candidate-collision-regression', 'name collisions', [COLLISIONS])
    reports, retained = clear_reports(lane, 'vw', 'candidate-full-VW')
    try: lane.sbt('candidate-full-VW', ['vw/test'], 1500)
    finally:
        for report in reports.glob('*.xml'): shutil.copy2(report, retained/report.name)
    current = [p for p in retained.glob('*.xml') if not p.name.startswith('prior-')]
    ids = [(case.get('classname'), case.get('name')) for p in current
           for case in ET.parse(p).getroot().iter('testcase')]
    require(ids and (SUITE, PRESERVATION) in ids and (SUITE, COLLISIONS) in ids,
            'Full VW suite omitted the new regressions')
    # vw/test has no filter. Retain every discovered suite/case and require
    # zero failures/skips; the count below is observed, not a static count claim.
    result = check_junit(current, ids)
    (lane.root/'full-VW-result.json').write_text(json.dumps({'observed': result,
        'ids': ids, 'command': 'vw/test', 'unfiltered': True}, indent=2))
    lane.suites('pipeline-coverage', ['com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'])


def python_lane(lane):
    from generated_artifacts import artifact_manifest
    from format_checks import azure_black
    lane.command('required-Black-version', ['python', '-c',
                 'import black; assert black.__version__ == "22.3.0"; print(black.__version__)'], 15)
    azure_black(lane, 'baseline', exclude_docs=True)
    lane.initial_admission('generated-Python-cold-build', remaining_seconds=1980)
    lane.apply()
    azure_black(lane, 'candidate', exclude_docs=True)
    started = time.monotonic()
    artifacts = artifact_manifest(lane, 'candidate')
    require(artifacts == lane.root/'candidate-artifact-manifest.json', 'Unexpected artifact record path')
    lane.admission(started, extra_seconds=660, factor=0)
    lane.command('generated-installed-Python-real-Spark-E2E',
                 ['python', str(lane.packet/'python_e2e.py')], 600)
    proof = json.loads((lane.root/'python-e2e-proof.json').read_text())
    require(proof['status'] == 'PASS' and len(proof['results']) == 2,
            'Missing scalar and non-scalar actual installed API proof')
