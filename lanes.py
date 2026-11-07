"""Five parallel lanes, with sequential processes inside each Docker job; no execution authority on import."""
import hashlib
import json
from pathlib import Path
import shutil
import time
from validation_runtime import require


def root_lane(lane):
    lane.check_forks()
    lane.datasets()
    started = time.monotonic()
    lane.sbt('baseline-root-compile', ['compile','Test / compile'], 900)
    reader = 'com.microsoft.azure.synapse.ml.io.split1.ImageReaderSuite'
    writer = 'com.microsoft.azure.synapse.ml.io.split1.ImageWriterSuite'
    coverage = 'com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'
    original = lane.suites('baseline-original-images',[reader,coverage])
    lane.admission(started, 600)
    lane.inject([lane.manifest['scala_regression_path']])
    lane.suites('baseline-fail-before',[writer],{writer:lane.manifest['expected_scala_failures']})
    lane.apply()
    lane.sbt('candidate-root-checks',['scalastyle','Test / scalastyle','compile','Test / compile'],1200)
    candidate = lane.suites('candidate-full-images',[reader,writer,coverage])
    for suite, result in original.items():
        require(result['names'] == candidate[suite]['names'], 'Lost original Scala image/coverage tests')


def python_lane(lane):
    from generated_artifacts import artifact_manifest
    from python_checks import run_pytest, whole_page, shipping
    from format_checks import azure_black
    role = lane.manifest['lanes'][lane.name]['source_kind']
    # Both commands run against pristine source in this same environment, before
    # any candidate overlay. Baseline-job results are also retained independently.
    azure_black(lane,'baseline')
    azure_black(lane,'baseline',exclude_docs=True)
    lane.initial_admission('python-cold-build', remaining_seconds=1980)
    if role == 'candidate':
        lane.apply()
        azure_black(lane,'candidate')
        azure_black(lane,'candidate',exclude_docs=True)
    else:
        lane.inject([lane.manifest['python_regression_path']])
    started = time.monotonic()
    artifacts = artifact_manifest(lane,role)
    # Cold build already finished, so there is NO second build to extrapolate.
    # Use its actual peak for real Spark/pytest/shipping admission; at most15min
    # projected remaining workflow work plus120s evidence, original clock intact.
    lane.admission(started,900,factor=0)
    test = lane.source/lane.manifest['python_regression_path']
    if role == 'baseline':
        failures = ['test_transformed_images_are_written_with_real_pixels',
                    'test_alpha_image_roundtrip_preserves_color_and_transparency']
        run_pytest(lane,'baseline-image-regressions',test,artifacts,2,failures,{
            failures[0]:['Image data source supports'],
            failures[1]:['AssertionError','Lists differ'],
        })
        whole_page(lane,'baseline',artifacts)
    else:
        run_pytest(lane,'candidate-full-image-workflow',test,artifacts,2)
        whole_page(lane,'candidate',artifacts)
        lane.command('black-diff',['python','-m','black','--diff','--color',
                                  '--extend-exclude','docs/','.'],180)
        shipping(lane)


def website_command(lane,label,args,seconds):
    return lane.command(label,args,seconds,cwd=lane.source/'website')


def website_lane(lane):
    role = lane.manifest['lanes'][lane.name]['source_kind']
    for name, expected in lane.manifest['website_input_sha256'].items():
        require(hashlib.sha256((lane.source/'website'/name).read_bytes()).hexdigest() == expected,
                'Website input changed')
    lane.initial_admission('website-full-build',remaining_seconds=2220)
    if role == 'candidate':
        lane.apply()
    website_command(lane,'npm-ci',['npm','ci','--no-audit','--no-fund'],900)
    lane.command('docgen-install',['python3','-m','pip','install','--user','--no-input','-e',
                                  'tools/docgen','nbconvert==7.16.6','beautifulsoup4','requests','pyyaml'],300)
    lane.command('docgen-environment',['python3','-m','pip','freeze'],60)
    lane.command(role+'-full-docgen',['python3',str(lane.packet/'website_generate.py')],600)
    website_command(lane,role+'-website-tests',['npm','test'],300)
    started = time.monotonic()
    website_command(lane,role+'-full-website-build',['npm','run','build'],900)
    if role == 'baseline':
        return
    # Only owned development-server verification remains; no second docgen/build.
    lane.admission(started,480,factor=0)
    matches = [p for p in (lane.source/'website/build').rglob('*.html')
               if 'Write transformed images' in p.read_text(errors='replace')]
    require(len(matches) == 1,'Missing/ambiguous rendered changed image page')
    page = matches[0]
    text = page.read_text()
    for needle in ['filenames','flipped','ImageTransformer','relative']:
        require(needle in text,'Missing rendered example content: '+needle)
    (lane.root/'rendered-page.json').write_text(json.dumps({
        'path':str(page),'bytes':page.stat().st_size,
        'sha256':hashlib.sha256(page.read_bytes()).hexdigest(),
        'scope':'Complete upstream docgen and production website; no screenshot claim',
    },indent=2))
    lane.command('actual-npm-start-and-rendered-page',['python3',str(lane.packet/'website_check.py')],600)
