"""UNRUN complete four-lane candidate docs adapter; parent owns original clock/approval."""
import json
import os
from pathlib import Path
import shutil
import sys
import time
from docs_contract import BOOK, QUICK, require, sha

DATA_SHA = '71381fc8b866938bfaada5c005c08194ca0ab712697d9195dd00ccedcbf3af40'
DATA_URL = 'https://mmlspark.blob.core.windows.net/publicwasb/multi_attribution_sample.csv'
BOOK_SHA = '561100f9bee680a89178a0a736116d539999a52b88314bd20f5593ae03375e11'


def subprocess_call(lane, label, expression, seconds, expected=(0,)):
    return lane.command(label, ['python', '-c', expression], seconds, expected=expected, cwd=lane.packet)


def source_identity(lane):
    return {name: sha((lane.source / name).read_bytes()) for name in lane.manifest['candidate_files']}


def early_controls(lane):
    expression = (
        'import json,os,sys;from pathlib import Path;'
        'from docs_contract import fixture;from docs_admission import fixture as gate;'
        'from notebook_run import dependency_fixture;'
        'import nbformat,nbclient,ipykernel;'
        'root=Path(' + repr(str(lane.root)) + ');'
        'schema=fixture((Path(' + repr(str(lane.source)) + ')/' + repr(QUICK) + ').read_text(),'
        '(Path(' + repr(str(lane.source)) + ')/' + repr(BOOK) + ').read_bytes());'
        'k=root/"early-docs-kernel/kernels/dml-docs-control";k.mkdir(parents=True);'
        '(k/"kernel.json").write_text(json.dumps({"argv":[sys.executable,"-m","ipykernel_launcher",'
        '"-f","{connection_file}"],"display_name":"Docs control","language":"python"}));'
        'os.environ["JUPYTER_PATH"]=str(k.parent.parent);'
        'real=dependency_fixture("dml-docs-control",root,dict(os.environ));'
        '(root/"docs-early-controls.json").write_text(json.dumps({"schema":schema,"memoryClock":gate(),'
        '"realKernel":real,"versions":{"nbclient":nbclient.__version__,"nbformat":nbformat.__version__,'
        '"ipykernel":ipykernel.__version__}},indent=2))')
    subprocess_call(lane, 'docs-early-real-controls', expression, 90)
    subprocess_call(lane, 'docs-early-visual-closure-controls',
                    'import json;from pathlib import Path;from visual_assets import fixture;'
                    '(Path(' + repr(str(lane.root)) + ')/"docs-visual-controls.json").write_text(json.dumps(fixture()))', 30)
    receipt = json.loads((lane.root / 'docs-early-controls.json').read_text())
    require(receipt['realKernel']['realKernelPositive'] and receipt['realKernel']['realKernelFailure'],
            'REAL_KERNEL_CONTROLS_REQUIRED')


def stage_data(lane):
    from bootstrap import download
    item = lane.manifest['dataset']
    require(item['url'] == DATA_URL and item['hash_algorithm'] == 'sha256' and
            item['hash'] == DATA_SHA, 'EXACT_PUBLIC_DATA_PIN_REQUIRED')
    directory = lane.root / 'inputs'
    directory.mkdir(exist_ok=False)
    path = directory / 'multi_attribution_sample.csv'
    receipt = download(item, path, lane.deadline)
    require(receipt['bytes'] == 80817 and sha(path.read_bytes()) == DATA_SHA, 'EXACT_PUBLIC_DATA_BYTES')
    (lane.root / 'docs-data-identity.json').write_text(json.dumps(receipt, indent=2))
    return path


def config(lane, artifacts, staged):
    return {'executionApproved': True, 'uriCopyApproved': True, 'notebookSHA256': BOOK_SHA,
            'stagedCSV': str(staged), 'csvBytes': 80817, 'csvSHA256': DATA_SHA,
            'artifactManifest': str(artifacts),
            'pythonExecutable': str(lane.root / 'mamba/envs/synapseml/bin/python'),
            'originalDeadlineEpoch': lane.deadline, 'cellCapSeconds': 1200,
            'singularCalibration': str(lane.root / 'calibration-singular.json'),
            'pluralCalibration': str(lane.root / 'calibration-plural.json')}


def calibrate_and_notebook(lane, artifacts):
    staged = stage_data(lane)
    cfg = config(lane, artifacts, staged)
    path = lane.root / 'notebook-runtime-config.json'
    path.write_text(json.dumps(cfg, indent=2))
    # A whole forecast, not total minus elapsed. Two fresh full-N waves are
    # additional diagnostics; B20/B30 canonical execution remains mandatory.
    lane.initial_admission('docs-before-full-N-calibration', remaining_seconds=1800)
    for route in ('singular', 'plural'):
        lane.command('docs-full-N-B10-' + route,
                     ['python', str(lane.packet / 'docs_calibration.py'), str(path),
                      str(lane.source), str(lane.root), route], 300, cwd=lane.packet)
        from docs_admission import calibration
        calibration(lane.root / ('calibration-' + route + '.json'))
    expression = ('import json;from pathlib import Path;from notebook_run import run,render_evidence;'
                  'root=Path(' + repr(str(lane.root)) + ');'
                  'run(json.loads((root/"notebook-runtime-config.json").read_text()),Path(' +
                  repr(str(lane.source)) + '),root);'
                  'r=render_evidence(root/"executed-notebook-fifteen-cell-view.ipynb",root/"executed-notebook.html");'
                  '(root/"executed-notebook-render.json").write_text(json.dumps(r,indent=2))')
    subprocess_call(lane, 'docs-full-fifteen-cell-notebook', expression,
                    min(1500, max(1, lane.deadline - time.time() - 240)))
    receipt = json.loads((lane.root / 'notebook-execution-receipt.json').read_text())
    require(receipt['complete'] and receipt['ownedStopVerified'] and
            receipt['actualKernelCodeCellsExecuted'] == 9, 'FULL_NOTEBOOK_INCOMPLETE')
    # Offline self-contained evidence view has no external script/CSS resources.
    html = lane.root / 'executed-notebook.html'
    require(html.is_file() and html.stat().st_size <= 8 * 1024**2, 'NOTEBOOK_HTML_MISSING_OR_OVERSIZE')


def python_quick(lane, artifacts):
    lane.initial_admission('docs-before-exact-python-quick', remaining_seconds=900)
    expression = ('from pathlib import Path;from quick_examples import python_examples;'
                  'python_examples(Path(' + repr(str(lane.source)) + '),Path(' + repr(str(artifacts)) +
                  '),Path(' + repr(str(lane.root)) + '))')
    subprocess_call(lane, 'docs-exact-two-python-fences', expression, 900)
    require(json.loads((lane.root / 'quick-python-result.json').read_text())['complete'],
            'PYTHON_QUICK_INCOMPLETE')


def scala_quick(lane, artifacts):
    from quick_examples import (scala_source, scala_tasks, compile_failure_source,
                                compile_failure_result, missing_evidence_fixture, scala_result)
    lane.initial_admission('docs-before-exact-scala-quick', remaining_seconds=1200)
    controls = lane.root / 'scala-doc-controls'
    controls.mkdir()
    bad = compile_failure_source(controls)
    lane.sbt('docs-expected-real-compile-failure',
             ['set core / Test / unmanagedSources += file(' + json.dumps(str(bad)) + ')',
              'core/Test/compile'], 120, expected=(1,))
    failed = compile_failure_result(lane.root / 'docs-expected-real-compile-failure.log')
    missing = missing_evidence_fixture(controls)
    (lane.root / 'docs-scala-controls.json').write_text(json.dumps(
        {'compileFailure': failed, 'missingEvidence': missing}, indent=2))
    temporary = scala_source(lane.source, artifacts, lane.root)
    # Remove ONLY stale report in this owned ephemeral workspace, preserving it
    # as evidence if any exists; never a checkout or external-disk cleanup.
    reports = lane.source / 'core/target/test-reports'
    suite = 'com.microsoft.azure.synapse.ml.causal.ExactDMLDocumentationExamples'
    target = reports / ('TEST-' + suite + '.xml')
    require(not target.exists(), 'UNEXPECTED_PREEXISTING_DOCUMENTATION_SUITE_REPORT')
    lane.sbt('docs-compile-and-run-two-scala-fences', scala_tasks(artifacts, temporary), 900)
    import xml.etree.ElementTree as ET
    matches = [p for p in reports.glob('*.xml') if ET.parse(p).getroot().get('name') == suite]
    require(len(matches) == 1, 'EXACT_ONE_SCALA_DOCUMENTATION_REPORT')
    copy = lane.root / 'quick-scala.xml'
    shutil.copyfile(matches[0], copy)
    result = scala_result(copy, lane.root / 'docs-compile-and-run-two-scala-fences.log', lane.root)
    require(result['complete'], 'SCALA_DOCUMENTATION_INCOMPLETE')


def website(lane):
    from website_contract import inspect_build
    lane.initial_admission('docs-before-whole-website', remaining_seconds=1200)
    for name, digest in lane.manifest['website_input_sha256'].items():
        require(sha((lane.source / 'website' / name).read_bytes()) == digest, 'WEBSITE_INPUT_CHANGED')
    web = lane.source / 'website'
    lane.command('docs-npm-ci', ['npm', 'ci', '--no-audit', '--no-fund'], 300, cwd=web)
    subprocess_call(lane, 'docs-normalized-article-reader-controls',
                    'from website_contract import fixture;print(fixture())', 30)
    lane.command('docs-actual-HTTP-header-controls',
                 ['python', str(lane.packet / 'website_check.py'), '--fixture'], 30, cwd=web)
    subprocess_call(lane, 'docs-docgen-import-closure',
                    'import nbformat,nbconvert,pypandoc,markdownify,markdown,traitlets,yaml,bs4,requests', 30)
    lane.command('docs-docgen-install', ['python', '-m', 'pip', 'install', '--no-deps', 'tools/docgen'], 60)
    lane.command('docs-whole-website-generate', ['python', str(lane.packet / 'website_generate.py')], 300)
    lane.command('docs-full-node-tests', ['npm', 'test'], 120, cwd=web)
    lane.command('docs-whole-production-build', ['npm', 'run', 'build'], 900, cwd=web)
    book, quick, route = inspect_build(web / 'build', lane.root / 'docs-SSR')
    lane.command('docs-actual-both-dev-routes', ['python', str(lane.packet / 'website_check.py')], 480)
    from visual_assets import export
    result = export(web / 'build', [book, quick], lane.root)
    (lane.root / 'docs-website-result.json').write_text(json.dumps(
        {'complete': True, 'notebookCanonicalRoute': route, 'visualAssetClosure': result,
         'actualBrowserReview': False}, indent=2))


def validate(lane):
    require(lane.manifest['execution_enabled'] is True, 'PARENT_EXECUTION_BOUNDARY_NOT_ENABLED')
    require(lane.name in ('docs-python', 'docs-scala', 'docs-notebook', 'docs-website'), 'UNKNOWN_DOCS_LANE')
    before = source_identity(lane)
    early_controls(lane)
    if lane.name == 'docs-website':
        website(lane)
    else:
        from candidate_generated_artifacts import artifact_manifest
        artifacts = artifact_manifest(lane)
        if lane.name == 'docs-notebook':
            calibrate_and_notebook(lane, artifacts)
        elif lane.name == 'docs-python':
            python_quick(lane, artifacts)
        else:
            scala_quick(lane, artifacts)
    require(source_identity(lane) == before, 'FROZEN_TEN_FEATURE_FILES_MUTATED')
    (lane.root / 'docs-lane-complete.json').write_text(json.dumps(
        {'complete': True, 'lane': lane.name, 'sourceFiles': before,
         'scope': 'this full lane only; all eight jobs required', 'browserReviewPerformed': False}, indent=2))
