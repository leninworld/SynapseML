"""Whole affected baseline lanes; this file is not an execution authorization."""
import json
from pathlib import Path
import shutil
import time
from baseline_checks import PROBE, classify, freeze
from validation_runtime import require


def reports(lane, label, module, tasks, seconds, probe_names=(), required_suites=()):
    source = lane.source / module / 'target/test-reports'
    retained = lane.root / (label + '-junit')
    retained.mkdir()
    for old in source.glob('*.xml'):
        shutil.copy2(old, retained / ('prior-' + old.name))
        old.unlink()  # Only owned hosted generated reports, preserved above.
    try:
        lane.sbt(label, tasks, seconds, expected=(0, 1) if probe_names else (0,))
    finally:
        for report in source.glob('*.xml'):
            shutil.copy2(report, retained / report.name)
    paths = [p for p in retained.glob('*.xml') if not p.name.startswith('prior-')]
    result = classify(paths, lane.manifest, probe_names, bool(probe_names), required_suites)
    (lane.root / (label + '-selection.json')).write_text(json.dumps(result, indent=2))
    return result


def root_lane(lane):
    kind = lane.name.removeprefix('root-')
    lane.check_forks()
    lane.datasets()
    lane.initial_admission('whole-baseline-' + kind, remaining_seconds=2460)
    lane.inject_witness()
    lane.sbt('baseline-root-compile', ['compile', 'Test / compile'], 720)
    if kind == 'automl':
        lane.sbt('root-style', ['scalastyle', 'Test / scalastyle'], 180)
    started = time.monotonic()
    all_names = lane.manifest['expected_regression_names'] + lane.manifest['expected_control_names']
    if kind == 'train':
        names, filter_text = all_names, ''
    elif kind == 'automl':
        names = [n for n in all_names if n.startswith('TuneHyperparameters ')]
        filter_text = ' -- -z "TuneHyperparameters"'
    elif kind == 'features':
        names = ['ValueIndexer persistence and IndexToValue preserve the original levels']
        filter_text = ' -- -z "ValueIndexer persistence"'
    else:
        raise RuntimeError('Unknown baseline root lane')
    early = reports(lane, 'early-real-label-route', 'core',
                    ['core/testOnly ' + PROBE + filter_text], 480, names)
    if kind in ('train', 'automl'):
        required = ['TuneHyperparameters LR retains indexed labels across folds',
                    'TuneHyperparameters RF retains indexed labels across folds']
        if kind == 'train':
            required += ['indexed int labels retain their classifier indices',
                         'indexed subset with overlapping original values does not silently remap']
        observed = {x['id'][1] for x in early['failure_causes']}
        require(set(required) <= observed, 'Baseline did not reproduce all decisive label/tuning paths')
    # Actual early training is measured; no VW-writer delta assumption.
    lane.admission(started, extra_seconds=1680, factor=0)
    if kind == 'features':
        reports(lane, 'full-original-VW-regression-consumer', 'vw',
                ['vw/testOnly com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitRegressor'], 300,
                required_suites=['com.microsoft.azure.synapse.ml.vw.VerifyVowpalWabbitRegressor'])
    if kind == 'train':
        reports(lane, 'full-original-train-plus-witness', 'core',
                ['core/testOnly com.microsoft.azure.synapse.ml.train.**'], 1500, all_names,
                lane.manifest['required_original_suites']['train'])
    elif kind == 'automl':
        reports(lane, 'full-original-automl', 'core',
                ['core/testOnly com.microsoft.azure.synapse.ml.automl.**'], 1500,
                required_suites=lane.manifest['required_original_suites']['automl'])
    else:
        reports(lane, 'full-original-features-and-isolationforest', 'core',
                ['core/testOnly com.microsoft.azure.synapse.ml.featurize.** '
                 'com.microsoft.azure.synapse.ml.isolationforest.**'], 1200,
                required_suites=lane.manifest['required_original_suites']['features'])
        reports(lane, 'pipeline-coverage', 'core',
                ['core/testOnly com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'],
                120, required_suites=['com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'])
    freeze(lane, 'baseline-final')


def python_lane(lane):
    from generated_artifacts import artifact_manifest
    from format_checks import azure_black
    lane.command('required-Black-version', ['python', '-c',
                 'import black; assert black.__version__ == "22.3.0"; print(black.__version__)'], 15)
    azure_black(lane, 'baseline', exclude_docs=True)
    lane.initial_admission('generated-Python-cold-build', remaining_seconds=1980)
    started = time.monotonic()
    artifact_manifest(lane, 'baseline')
    lane.admission(started, extra_seconds=660, factor=0)
    lane.command('generated-installed-Python-real-Spark-baseline',
                 ['python', str(lane.packet / 'python_e2e.py')], 600)
    proof = json.loads((lane.root / 'python-e2e-proof.json').read_text())
    require(proof['baseline_complete'] and proof['source_kind'] == 'UNMODIFIED_BASELINE',
            'Missing actual installed baseline result')
    freeze(lane, 'python-baseline-final')
