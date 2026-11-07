"""Two sequential hosted jobs. All native/documentation evidence remains separate from baseline checks."""
from pathlib import Path
import hashlib,importlib.util,json,os,shutil,xml.etree.ElementTree as ET
from validation_runtime import require
from documentation_source import author_format,import_formatted


def native_scala_checks(lane):
    lane.datasets()
    suites=['com.microsoft.azure.synapse.ml.lightgbm.split5.VerifyLightGBMClassifierStream','com.microsoft.azure.synapse.ml.lightgbm.split6.VerifyLightGBMClassifierBulk']
    reports=lane.source/'lightgbm/target/test-reports'
    require(not list(reports.glob('*.xml')),'Unexpected pre-existing native test reports')
    label='Verify Multiclass LightGBM Classifier local feature importance SHAP values'
    lane.sbt('baseline-existing-native-SHAP',['lightgbm/testOnly '+' '.join(suites)+' -- -z "'+label+'"'],600)
    output=[]
    for name in suites:
        found=[(p,ET.parse(p).getroot()) for p in reports.glob('*.xml') if ET.parse(p).getroot().get('name')==name]
        require(len(found)==1,'Missing/ambiguous existing Scala SHAP report')
        path,root=found[0];cases=list(root.iter('testcase'))
        require(len(cases)==1 and cases[0].get('name','').startswith(label),'Wrong existing native test selection')
        require(not list(root.iter('failure')) and not list(root.iter('error')) and not list(root.iter('skipped')),'Existing native SHAP check failed/skipped')
        shutil.copy2(path,lane.root/(name.rsplit('.',1)[1]+'.xml'));output.append({'suite':name,'case':cases[0].get('name'),'status':'PASS','scope':'existing baseline shape check, not new notebook proof'})
    (lane.root/'baseline-native-tests.json').write_text(json.dumps(output,indent=2))


def notebook_lane(lane):
    from format_checks import azure_black
    from generated_artifacts import artifact_manifest
    lane.command('real-kernel-positive-negative-fixture',['python',str(lane.packet/'notebook_run.py'),'fixture'],180,cwd=lane.packet)
    azure_black(lane,'baseline');azure_black(lane,'baseline',exclude_docs=True)
    lane.apply();author_format(lane)
    azure_black(lane,'candidate');azure_black(lane,'candidate',exclude_docs=True)
    lane.initial_admission('native-root-and-full-finish',remaining_seconds=1920)
    lane.sbt('baseline-root-compile-testcompile-styles',['compile','Test / compile','scalastyle','Test / scalastyle','show lightgbm / Test / fork','show lightgbm / Test / javaOptions'],1200)
    artifacts=artifact_manifest(lane,'candidate')
    native_scala_checks(lane)
    info=json.loads(artifacts.read_text());classpath=','.join(x['path'] for x in info['jars'])
    os.environ.update(PYSPARK_SUBMIT_ARGS='--master local[2] --driver-memory 1g --jars '+classpath+' pyspark-shell',PYSPARK_PYTHON=str(lane.root/'mamba/envs/synapseml/bin/python'),PYSPARK_DRIVER_PYTHON=str(lane.root/'mamba/envs/synapseml/bin/python'),SHAP_ARTIFACT_MANIFEST=str(artifacts))
    # Existing recursive NotebookFiles/CPU filename filters traced in pinned source;
    # execute the real pure Python changed-path impact function too, with no Azure API.
    spec=importlib.util.spec_from_file_location('actual_e2e_impact',lane.source/'tools/ci/e2e_impact.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    actual=module.required_suites(list(lane.manifest['candidate_files']));require(actual==module.ALL_SUITES,'Notebook lost CI impact selection')
    (lane.root/'CI-notebook-selection.json').write_text(json.dumps({'actual_python_impact':sorted(actual),'Scala_NotebookFiles_CPU_filters':'static verified pinned recursive glob; filename matches no exclusion','Databricks_Fabric_run':False}))
    lane.command('full-authored-notebook-real-native',['python',str(lane.packet/'notebook_run.py'),'actual'],1200,cwd=lane.packet)
    require(json.loads((lane.root/'notebook-result.json').read_text())['original_code_cells']==6,'Wrong authored notebook code-cell count')
    lane.verify_candidate('after-notebook')


def website_lane(lane):
    from website_contract import inspect_build
    for name,expected in lane.manifest['website_input_sha256'].items():require(hashlib.sha256((lane.source/'website'/name).read_bytes()).hexdigest()==expected,'Website inputs changed')
    lane.apply();import_formatted(lane)
    lane.initial_admission('whole-website-finish',remaining_seconds=660)
    lane.command('npm-ci',['npm','ci','--no-audit','--no-fund'],600,cwd=lane.source/'website')
    lane.command('docgen-install',['python3','-m','pip','install','--user','--no-input','tools/docgen','nbconvert==7.16.6','beautifulsoup4','requests','pyyaml'],300)
    lane.command('docgen-environment',['python3','-m','pip','freeze'],30)
    lane.command('whole-website-generate',['python3',str(lane.packet/'website_generate.py')],300)
    lane.command('whole-website-tests',['npm','test'],180,cwd=lane.source/'website')
    lane.command('whole-website-production-build',['npm','run','build'],900,cwd=lane.source/'website')
    book,overview,route=inspect_build(lane.source/'website/build')
    shutil.copy2(book,lane.root/'notebook-rendered.html');shutil.copy2(overview,lane.root/'overview-rendered.html')
    (lane.root/'website-SSR.json').write_text(json.dumps({'book_path':str(book),'book_sha256':hashlib.sha256(book.read_bytes()).hexdigest(),'overview_path':str(overview),'overview_sha256':hashlib.sha256(overview.read_bytes()).hexdigest(),'route':route,'article_link_and_sidebar_verified':True,'actual_rendered_proof':'production SSR; dev compilation/liveness separate'},indent=2))
    lane.command('actual-dev-server-new-page-route',['python3',str(lane.packet/'website_check.py')],600)
