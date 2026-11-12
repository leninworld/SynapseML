"""One complete native and website candidate run; no automatic retry."""
import ast
import hashlib
import json
import os
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

from bootstrap import download
from validation_runtime import require

GENERATOR = 'lightgbm/src/test/resources/python-interop/generate.py'
SUITE = 'com.microsoft.azure.synapse.ml.lightgbm.split6.PythonModelInteropSuite'
DOC_SUITE = 'com.microsoft.azure.synapse.ml.lightgbm.split6.PythonInteropDocumentationExample'


def junit(lane, suite, filename, expected_cases=1):
    module = 'core' if '.core.' in suite else 'lightgbm'
    found = [(path, ET.parse(path).getroot()) for path in (lane.source / module / 'target/test-reports').glob('*.xml')
             if ET.parse(path).getroot().get('name') == suite]
    require(len(found) == 1, 'Missing/ambiguous exact report ' + suite)
    path, report = found[0]
    require(len(list(report.iter('testcase'))) == expected_cases if expected_cases is not None
            else len(list(report.iter('testcase'))) > 0, 'Unexpected test selection')
    require(not any(list(report.iter(tag)) for tag in ['failure', 'error', 'skipped']),
            'Failure/error/skip in ' + suite)
    shutil.copy2(path, lane.root / filename)


def format_and_freeze(lane):
    path = lane.source / GENERATOR
    original = path.read_text()
    lane.command('author-generator-format', ['python', '-m', 'black', '--quiet', str(path)], 30)
    formatted = path.read_text()
    require(ast.dump(ast.parse(original), include_attributes=False) ==
            ast.dump(ast.parse(formatted), include_attributes=False), 'Formatter changed generator AST')
    lane.manifest['candidate_files'][GENERATOR] = hashlib.sha256(path.read_bytes()).hexdigest()
    lane.verify_candidate('formatted')
    from documentation_run import blocks
    require(len(blocks((lane.source / 'docs/Explore Algorithms/LightGBM/Python model inputs.md').read_text())) == 3,
            'Documentation code selection')
    patch = lane.command('tested-tracked-patch', ['git', 'diff', '--binary', '--abbrev=7'], 10).encode()
    for name, baseline in lane.manifest['baseline_files'].items():
        if baseline is None:
            patch += lane.command('tested-new-patch-' + str(len(patch)),
                                  ['git', 'diff', '--no-index', '--binary', '--abbrev=7', '--', '/dev/null', name],
                                  10, expected=(1,)).encode()
    (lane.root / 'tested-documentation.patch').write_bytes(patch)
    result = {'source_sha256': lane.manifest['candidate_files'],
              'tested_patch_sha256': hashlib.sha256(patch).hexdigest(),
              'input_patch_sha256': lane.manifest['patch_sha256'], 'only_formatting': GENERATOR,
              'same_generator_AST': True, 'generator_before_sha256': hashlib.sha256(original.encode()).hexdigest()}
    (lane.root / 'frozen-documentation.json').write_text(json.dumps(result, indent=2))
    evidence = lane.root / 'interop874'
    evidence.mkdir()
    shutil.copy2(path, evidence / 'formatted-generate.py')


def validate(lane):
    from format_checks import azure_black
    from generated_artifacts import artifact_manifest
    lane.initial_admission('complete-native-and-website-finish', remaining_seconds=2100)
    lane.command('real-documentation-reader-fixtures', ['python', str(lane.packet / 'documentation_run.py'), 'fixture'],
                 30, cwd=lane.packet)
    azure_black(lane, 'baseline')
    azure_black(lane, 'baseline', exclude_docs=True)
    lane.apply()
    format_and_freeze(lane)
    azure_black(lane, 'candidate')
    azure_black(lane, 'candidate', exclude_docs=True)
    wheel = lane.manifest['lightgbm']
    destination = lane.root / 'bootstrap/lightgbm-3.3.5-py3-none-manylinux1_x86_64.whl'
    receipt = download(wheel, destination, lane.deadline)
    require(receipt['bytes'] == wheel['exact_bytes'], 'Wrong wheel')
    lane.command('extra-wheel-install', ['python', '-m', 'pip', 'install', '--no-deps', '--no-index', str(destination)], 60)
    lane.command('exact-training-documentation-and-regeneration',
                 ['python', str(lane.packet / 'documentation_run.py'), 'train'], 120)
    tasks = ['lightgbm/Test/scalastyle', 'lightgbm/testOnly ' + SUITE,
             'core/testOnly com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite',
             'set lightgbm / Test / unmanagedSources += file("/runtime/PythonInteropDocumentationExample.scala")',
             'lightgbm/testOnly ' + DOC_SUITE]
    lane.sbt('early-native-regression-registration-and-exact-scala-doc', tasks, 1200)
    junit(lane, SUITE, 'new-native-regression.xml')
    junit(lane, DOC_SUITE, 'exact-scala-documentation.xml')
    junit(lane, 'com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite',
          'CI-registration.xml', expected_cases=None)
    lane.initial_admission('full-root-artifacts-website-finish', remaining_seconds=1500)
    lane.sbt('full-root-compile-testcompile-styles', ['compile', 'Test / compile', 'scalastyle',
                                                    'Test / scalastyle'], 1200)
    artifacts = artifact_manifest(lane, 'candidate')
    lane.datasets()
    suites = ['com.microsoft.azure.synapse.ml.lightgbm.split5.VerifyLightGBMClassifierStream',
              'com.microsoft.azure.synapse.ml.lightgbm.split6.VerifyLightGBMClassifierBulk']
    label = 'Verify Multiclass LightGBM Classifier local feature importance SHAP values'
    lane.sbt('existing-native-SHAP', ['lightgbm/testOnly ' + ' '.join(suites) + ' -- -z "' + label + '"'], 600)
    for index, suite in enumerate(suites):
        junit(lane, suite, 'existing-native-' + str(index) + '.xml')
    info = json.loads(artifacts.read_text())
    classpath = ','.join(x['path'] for x in info['jars'])
    os.environ.update(PYSPARK_SUBMIT_ARGS='--master local[2] --driver-memory 1g --jars ' + classpath + ' pyspark-shell',
                      PYSPARK_PYTHON=str(lane.root / 'mamba/envs/synapseml/bin/python'),
                      PYSPARK_DRIVER_PYTHON=str(lane.root / 'mamba/envs/synapseml/bin/python'))
    lane.command('exact-installed-spark-documentation',
                 ['python', str(lane.packet / 'documentation_run.py'), 'spark'], 240, cwd=lane.packet)
    lane.verify_candidate('after-native-and-docs')
    website(lane)


def website(lane):
    from website_contract import inspect_build
    for name, expected in lane.manifest['website_input_sha256'].items():
        require(hashlib.sha256((lane.source / 'website' / name).read_bytes()).hexdigest() == expected,
                'Website dependency input changed')
    lane.initial_admission('whole-website-finish', remaining_seconds=900)
    lane.command('npm-ci', ['npm', 'ci', '--no-audit', '--no-fund'], 600, cwd=lane.source / 'website')
    lane.command('actual-header-positive-negative-fixture',
                 ['python', str(lane.packet / 'website_check.py'), '--fixture'], 30, cwd=lane.source / 'website')
    lane.command('website-article-contract-fixtures', ['python', '-c',
                 'from website_contract import fixture;fixture()'], 15, cwd=lane.packet)
    lane.command('docgen-import-closure', ['python', '-c',
                 'import nbformat,nbconvert,pypandoc,markdownify,markdown,traitlets,yaml,bs4,requests; '
                 'print(nbformat.__version__,nbconvert.__version__)'], 30)
    lane.command('docgen-install', ['python', '-m', 'pip', 'install', '--no-deps', 'tools/docgen'], 120)
    lane.command('whole-website-generate', ['python', str(lane.packet / 'website_generate.py')], 300)
    lane.command('whole-website-tests', ['npm', 'test'], 180, cwd=lane.source / 'website')
    lane.command('whole-website-production-build', ['npm', 'run', 'build'], 900, cwd=lane.source / 'website')
    page, overview, route = inspect_build(lane.source / 'website/build')
    shutil.copy2(page, lane.root / 'tutorial-rendered.html')
    shutil.copy2(overview, lane.root / 'overview-rendered.html')
    (lane.root / 'website-SSR.json').write_text(json.dumps({'route': route, 'passed': True,
        'production_article_overview_sidebar_verified': True}))
    lane.command('actual-dev-server-new-page-route', ['python', str(lane.packet / 'website_check.py')], 600)
    # Generation may update only website/docs, preserving the complete product patch.
    for name, expected in lane.manifest['candidate_files'].items():
        require(hashlib.sha256((lane.source / name).read_bytes()).hexdigest() == expected, 'Product source changed')
    changed = set(lane.command('final-tracked-scope', ['git', 'diff', '--name-only', 'HEAD'], 10).splitlines())
    wanted = {name for name, value in lane.manifest['baseline_files'].items() if value is not None}
    require({name for name in changed if not name.startswith('website/docs/')} == wanted, 'Unexpected tracked source')
    new = set(lane.command('final-untracked-scope', ['git', 'ls-files', '--others', '--exclude-standard'], 10).splitlines())
    wanted_new = {name for name, value in lane.manifest['baseline_files'].items() if value is None}
    require(new == wanted_new, 'Unexpected final untracked source')
