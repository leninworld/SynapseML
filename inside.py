"""Full SynapseML validation lane, container-only."""
from pathlib import Path
import hashlib,json,os,re,selectors,shutil,signal,subprocess,sys,time,xml.etree.ElementTree as ET
P=Path('/packet');R=Path('/runtime');S=R/'source'
M=json.loads((P/'manifest.json').read_text());LANE=os.environ['VALIDATION_LANE'];J=M['jobs'][LANE]
END=float(os.environ['VALIDATION_DEADLINE_EPOCH']);LOGS=[]
assert os.getuid()==1001 and Path('/.dockerenv').exists() and sys.version_info[:3]==(3,11,8)
RESULT={'lane':LANE,'status':'STARTING','lane_pass':False,'baseline_product_unchanged':True}
def hash_file(path):
 digest=hashlib.sha256()
 with path.open('rb') as stream:
  while True:
   guard();chunk=stream.read(1024**2)
   if not chunk:break
   digest.update(chunk)
 return digest.hexdigest()
def verify_native(require_ivy=False):
 import zipfile
 names={'lightgbmlib-3.3.510.jar','com.microsoft.ml.lightgbm_lightgbmlib-3.3.510.jar'}
 jars=[path for path in (R/'home').rglob('*.jar') if path.name in names]
 assert jars,'NATIVE_ARTIFACT_MISSING'
 if require_ivy:assert any(path.name=='com.microsoft.ml.lightgbm_lightgbmlib-3.3.510.jar' for path in jars),'RUNTIME_IVY_NATIVE_MISSING'
 records=[]
 for jar in jars:
  digest=hash_file(jar);assert digest==M['native_jar_sha256'],'NATIVE_JAR_HASH'
  with zipfile.ZipFile(jar) as archive:
   for name in ['com/microsoft/ml/lightgbm/linux/x86_64/lib_lightgbm.so','com/microsoft/ml/lightgbm/linux/x86_64/lib_lightgbm_swig.so']:
    with archive.open(name) as stream:header=stream.read(64)
    assert header[:4]==b'\x7fELF' and header[5]==1 and int.from_bytes(header[18:20],'little')==62,'NATIVE_ARCHITECTURE'
  records.append({'path':str(jar),'sha256':digest})
 RESULT['native_jar_records']=records
def guard():
 assert time.time()<END,'ORIGINAL_TARGET_DEADLINE'
 assert shutil.disk_usage(R).free>4*1024**3,'DISK_PRESSURE'
def run(label,args,seconds,cwd=S,expected=0,cap=4*1024**2):
 guard();assert re.fullmatch('[a-z0-9-]+',label)
 dest=R/(label+'.log');assert not dest.exists()
 start=time.monotonic();chunks=[];size=0
 proc=subprocess.Popen(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
 sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
 try:
  while sel.get_map():
   guard();assert time.monotonic()-start<seconds,'STEP_TIME_'+label
   for key,_ in sel.select(.25):
    data=os.read(key.fd,8192)
    if not data:sel.unregister(key.fileobj);continue
    size+=len(data);assert size<=cap,'OUTPUT_CAP_'+label;chunks.append(data)
  code=proc.wait(timeout=3);assert code==expected,'STEP_EXIT_'+label+':'+str(code)
  return time.monotonic()-start,b''.join(chunks)
 except BaseException:
  if proc.poll() is None:
   os.killpg(proc.pid,signal.SIGTERM)
   try:proc.wait(timeout=3)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
  raise
 finally:
  sel.close();dest.write_bytes(b''.join(chunks));LOGS.append({'label':label,'args':args,'exit':proc.returncode,'expected_exit':expected,'seconds':time.monotonic()-start,'bytes':size})
def sbt(label,tasks,seconds=1200):
 version = '0.0.1-i555-SNAPSHOT' if RESULT['baseline_product_unchanged'] else '0.0.2-i555-SNAPSHOT'
 return run(label,['sbt',f'set ThisBuild / version := "{version}"']+tasks,seconds)
def projection(calibration, extra_seconds=0):
 checkpoint=json.loads((R/'memory-checkpoint.json').read_text())
 assert checkpoint['samples']>=2 and time.time()-checkpoint['at']<20,'MISSING_FRESH_MEMORY_CALIBRATION'
 memory=M['memory_projection_factor']*checkpoint['max_sampled_working_set_upper_bytes']+J['candidate_memory_extra_gib']*1024**3
 assert memory<J['memory_gib']*1024**3*M['working_set_stop_fraction'],'FULL_FINISH_MEMORY_NO_GO'
 RESULT['memory_projection']={'sampled_not_continuous_peak':checkpoint,'assumed_factor':M['memory_projection_factor'],'extra_gib':J['candidate_memory_extra_gib'],'projected_bytes':memory}
 projected=(J['remaining_factor']*calibration+J['remaining_extra_seconds']+extra_seconds)*M['projection_multiplier']
 RESULT.update(calibration_seconds=calibration,projected_remaining_seconds=projected)
 assert time.time()+projected<END,'FULL_FINISH_PROJECTION_NO_GO'

def diagnostic(name):
 dest=S/'core/src/test/scala/com/microsoft/azure/synapse/ml/train'/name
 assert not dest.exists();shutil.copy2(P/name,dest)

def candidate():
 run('apply-check',['git','apply','--check','/packet/production-proposal.patch'],10)
 run('apply-once',['git','apply','/packet/production-proposal.patch'],10)
 RESULT['baseline_product_unchanged']=False
 run('scope',['git','add','--intent-to-add']+M['new_source_paths'],10)
 _,out=run('paths',['git','diff','--name-only'],10)
 assert sorted(out.decode().splitlines())==sorted(M['source_allowlist']),'SOURCE_SCOPE'
 for path,expected in M['candidate_source_sha256'].items():
  assert hash_file(S/path)==expected,'CANDIDATE_INPUT_HASH_'+path

def verify_extracted_datasets():
 import csv
 archives=list(S.glob('**/datasets-2023-04-03.tgz'));assert len(archives)==1
 root=archives[0].parent/'datasets-2023-04-03';assert root.is_relative_to(S)
 # BuildInfo.datasetDir uses this same ThisBuild setting; also satisfy the legacy env-based helpers explicitly.
 os.environ['DATASETS_HOME']=str(root)
 proof=[]
 for row in M['dataset_headers']:
  relative=Path(row['path']).relative_to('datasets-2023-04-03')
  file=root/relative;assert file.is_file(),'MISSING_EXTRACTED_DATASET_'+str(relative)
  assert file.stat().st_size==row['file_bytes'] and hash_file(file)==row['file_sha256'],'EXTRACTED_DATASET_BYTE_IDENTITY_'+str(relative)
  with file.open(newline='',encoding='utf8') as stream:headers=next(csv.reader(stream))
  assert headers==row['headers'],'EXTRACTED_DATASET_HEADER_CHANGED_'+str(relative)
  assert not [x for x in headers if x!=row['label'] and x in row['label']],'UNREGISTERED_BENCHMARK_DRIFT'
  proof.append({'path':str(file),'bytes':file.stat().st_size,'sha256':hash_file(file),'headers':headers})
 assert len(proof)==13
 RESULT['extracted_dataset_proof']=proof

def datasets():
 sbt('public-datasets',['getDatasets'],240)
 found=list(S.glob('**/datasets-2023-04-03.tgz'));assert len(found)==1
 path=found[0];assert path.stat().st_size==M['public_dataset']['bytes']
 assert hash_file(path)==M['public_dataset']['sha256'],'PUBLIC_DATASET_SHA'
 RESULT['dataset_archive']={'bytes':path.stat().st_size,'sha256':hash_file(path)}
 verify_extracted_datasets()

def inventory(module,suites,label):
 _,out=sbt('inventory-'+label,[module+'/Test/runMain com.microsoft.azure.synapse.ml.train.TestInventory555 '+' '.join(suites)],180)
 match=re.search(rb'TEST_INVENTORY_555=(\{[^\n]+\})',out);assert match,'NO_TEST_INVENTORY'
 dataset=re.search(rb'DATASET_ROOT_555=([^\n]+)',out);assert dataset,'NO_ACTUAL_BUILDINFO_DATASET_ROOT'
 assert Path(dataset[1].decode().strip())==Path(os.environ['DATASETS_HOME']),'BUILDINFO_DATASET_ROOT_MISMATCH'
 data=json.loads(match[1]);assert set(data)==set(suites)
 ignored_match=re.search(rb'IGNORED_INVENTORY_555=(\{[^\n]+\})',out);assert ignored_match,'NO_IGNORED_INVENTORY'
 ignored=json.loads(ignored_match[1]);assert set(ignored)==set(suites)
 for name,items in ignored.items():assert sorted(items)==sorted(M['preexisting_ignored_tests'].get(name,[])),'UNEXPECTED_REGISTERED_IGNORED_'+name
 assert all(names and len(names)==len(set(names)) for names in data.values())
 return data

def full_suites(label,module,suites,seconds):
 expected=inventory(module,suites,label)
 # Earlier reports are explicitly removed only inside this new owned hosted source copy.
 # Preserve their bytes to evidence first, so no prior record can masquerade as this step.
 report_dir=S/module/'target/test-reports'
 if report_dir.exists():
  for file in report_dir.glob('*.xml'):
   backup=R/(label+'-prior-'+file.name);shutil.copy2(file,backup);file.unlink()
 sbt(label,[module+'/testOnly '+' '.join(suites)],seconds)
 facts=[]
 reports=list((S/module/'target/test-reports').glob('*.xml'))
 for name,names in expected.items():
  matches=[]
  for path in reports:
   root=ET.parse(path).getroot()
   if root.attrib.get('name')==name:matches.append((path,root))
  assert len(matches)==1,'MISSING_OR_DUPLICATE_SUITE_'+name
  path,root=matches[0];cases=list(root.iter('testcase'))
  actual=[case.attrib['name'] for case in cases]
  assert set(actual)==set(names) and len(actual)==len(names),'TEST_INVENTORY_MISMATCH_'+name
  skipped=[case.attrib['name'] for case in cases if list(case.iter('skipped'))]
  assert sorted(skipped)==sorted(M['preexisting_ignored_tests'].get(name,[])),'UNEXPECTED_SKIPPED_'+name
  assert not list(root.iter('failure')) and not list(root.iter('error')),'FAILED_OR_ERROR_'+name
  assert int(root.attrib['tests'])==len(names)
  facts.append({'suite':name,'tests':len(names),'case_names':names,'junit_sha256':hash_file(path),'failures':0,'errors':0,'passed':len(names)-len(skipped),'skipped':len(skipped),'preexisting_ignored_case_names':skipped})
 RESULT.setdefault('full_scala_reports',[]).extend(facts)

def legacy_results(mode):
 root=R/'legacy-master';parts=list((root/(mode+'-outcomes')).glob('part-*.json'));assert len(parts)==1
 records=[json.loads(line) for line in parts[0].read_text().splitlines()];byname={x['case']:x for x in records}
 assert len(byname)==len(records)
 if mode=='produce':
  for name in ['inherited-custom-label','inherited-default-label','score-without-weight','weighted-automatic-ovr','exact-substring-feature']:
   assert byname[name]['status']=='ERROR','BASELINE_NOT_EXPECTED_BEHAVIOR_FAILURE_'+name
   detail=byname[name]['detail'];assert 'Compilation' not in detail and 'ClassNotFound' not in detail
  assert 'labelCol' in byname['inherited-custom-label']['detail']
  assert 'labelCol' in byname['inherited-default-label']['detail']
  assert 'weight' in byname['score-without-weight']['detail']
  assert 'weight' in byname['weighted-automatic-ovr']['detail']
  assert 'SUBSTRING_FEATURE_WAS_EXCLUDED' in byname['exact-substring-feature']['detail']
  for name in ['shared-binary-false','shared-binary-true']:assert byname[name]['status']=='SUCCESS',byname[name]
 else:
  assert byname['old-model-still-requires-weight']['status']=='ERROR'
  assert 'weight' in byname['old-model-still-requires-weight']['detail']
  for name in ['old-estimator-refit','binary-shared-weight-parity']:assert byname[name]['status']=='SUCCESS'
  assert byname['multiclass-shared-weight-parity']['status'] in ['SUCCESS','NOT_COMPARED_MASTER_FAILED']
 RESULT['legacy_'+mode]=records
 RESULT['legacy_fixture_manifest']=[{'path':str(f.relative_to(root)),'bytes':f.stat().st_size,'sha256':hash_file(f)} for f in sorted(root.rglob('*')) if f.is_file()]

def pytest(label,files,seconds,expected=0,rootdir=S,extra_args=None):
 report=R/(label+'.xml')
 frozen=M['python_case_inventory'][label]
 options=['--rootdir='+str(rootdir),'--import-mode=importlib']+(extra_args or [])
 _,collected=run(label+'-collect',['python','-m','pytest','--collect-only','-q']+options+[str(f) for f in files],180)
 prefixes=tuple(node.split('::')[0]+'::' for node in frozen)
 nodeids=[line.strip() for line in collected.decode().splitlines() if line.startswith(prefixes)]
 assert sorted(nodeids)==sorted(frozen) and len(nodeids)==len(frozen),'PYTHON_COLLECTION_IDENTITY_'+label
 _,out=run(label,['python','-m','pytest','-q','--junitxml='+str(report)]+options+[str(f) for f in files],seconds,expected=expected)
 assert report.exists(),'NO_PYTHON_JUNIT'
 root=ET.parse(report).getroot();cases=list(root.iter('testcase'));assert cases,'NO_PYTHON_CASES'
 pairs=[]
 for node in frozen:
  parts=node.split('::');file=parts[0];module=file[:-3] if file.endswith('.py') else file
  names=[module.replace('/','.')]+parts[1:];pairs.append(('.'.join(names[:-1]),names[-1]))
 actual=[(case.attrib.get('classname'),case.attrib['name']) for case in cases]
 assert sorted(actual)==sorted(pairs) and len(actual)==len(pairs),'PYTHON_JUNIT_IDENTITY_'+label
 errors=list(root.iter('error'));failed=list(root.iter('failure'));skipped=list(root.iter('skipped'))
 assert not errors and not skipped,'PYTHON_ERROR_OR_SKIP'
 if expected==0:assert not failed,'PYTHON_ASSERTIONS_FAILED'
 else:assert len(cases)==len(failed)==1,'BASELINE_PYTHON_NOT_ONE_FAILURE'
 RESULT.setdefault('python_reports',[]).append({'label':label,'tests':len(cases),'failures':len(failed),'errors':len(errors),'skips':len(skipped),'nodeids':nodeids,'junit_pairs':actual,'junit_sha256':hash_file(report)})
 return out

try:
 assert not S.exists();shutil.copytree('/source',S)
 _,commit=run('source-commit',['git','rev-parse','HEAD'],10);assert commit.decode().strip()==M['source_commit']
 for path,expected in M['baseline_source_sha256'].items():assert hash_file(S/path)==expected,'BASELINE_SHA'
 run('versions',['bash','-euc','java -version\npython3 -V\ngit --version'],20)
 if LANE!='G':
  _,forks=sbt('fork-settings',['show core / Test / fork','show lightgbm / Test / fork'],180)
  assert len(re.findall(rb'\[info\]\s+false\s*$',forks,re.M))>=2,'UNCONFIRMED_FORK_SETTINGS'
 if LANE=='A':
  run('black-install',['python3','-m','pip','install','black[jupyter]==22.3.0'],180)
  elapsed,_=sbt('root-baseline-calibration',['compile','Test / compile'])
  projection(elapsed);candidate()
  sbt('root-candidate-checks',['scalastyle','Test / scalastyle','compile','Test / compile'])
  run('black-diff',['python3','-m','black','--diff','--color','--extend-exclude','docs/','.'],180)
  run('black-check',['python3','-m','black','--check','-q','--extend-exclude','docs/','.'],180)
 elif LANE=='B':
  diagnostic('TestInventory555.scala');datasets()
  elapsed,_=sbt('core-baseline-calibration',['core/Test/compile']);projection(elapsed)
  full_suites('original-complete-classifier','core',['com.microsoft.azure.synapse.ml.train.VerifyTrainClassifier'],1800)
  for path,expected in M['baseline_source_sha256'].items():assert hash_file(S/path)==expected,'BASELINE_MUTATION'
  RESULT['unmodified_master_baseline_complete']=True
 elif LANE=='C':
  diagnostic('TestInventory555.scala');diagnostic('LegacyClassifier555.scala');datasets()
  compile_seconds,_=sbt('baseline-core-compile',['core/Test/compile'])
  elapsed,_=sbt('actual-master-artifact-producer',['core/Test/runMain com.microsoft.azure.synapse.ml.train.LegacyClassifier555 produce /runtime/legacy-master'],600)
  legacy_results('produce');projection(elapsed,1.25*compile_seconds);candidate()
  diagnostic('WebsiteScalaSample555.scala')
  sbt('candidate-core-compile',['core/Test/compile'])
  full_suites('candidate-complete-classifier','core',[
   'com.microsoft.azure.synapse.ml.train.VerifyTrainClassifier',
   'com.microsoft.azure.synapse.ml.train.TrainClassifierColumnsSuite'],1800)
  sbt('actual-candidate-artifact-consumer',['core/Test/runMain com.microsoft.azure.synapse.ml.train.LegacyClassifier555 consume /runtime/legacy-master'],480)
  legacy_results('consume')
  sbt('exact-website-scala-example',['core/Test/runMain com.microsoft.azure.synapse.ml.train.WebsiteScalaSample555'],300)
 elif LANE=='D':
  diagnostic('TestInventory555.scala');datasets()
  elapsed,_=sbt('baseline-core-compile',['core/Test/compile']);projection(elapsed,1.25*elapsed);candidate()
  sbt('candidate-core-compile',['core/Test/compile'])
  full_suites('complete-featurize','core',['com.microsoft.azure.synapse.ml.featurize.VerifyFeaturize'],1500)
  full_suites('existing-pipeline-selection-guard','core',[
   'com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'],300)
 elif LANE=='E':
  diagnostic('TestInventory555.scala');datasets()
  elapsed,_=sbt('baseline-native-compile',['core/Test/compile','lightgbm/Test/compile'])
  verify_native();projection(elapsed,1.25*elapsed);candidate()
  sbt('candidate-core-and-native-compile',['core/Test/compile','lightgbm/Test/compile'])
  full_suites('complete-class-balancer','core',['com.microsoft.azure.synapse.ml.stages.ClassBalancerSuite'],600)
  full_suites('complete-native-weighted-classifier','lightgbm',[
   'com.microsoft.azure.synapse.ml.lightgbm.split1.TrainClassifierWeightsSuite'],1200)
 elif LANE=='G':
  web=S/'website'
  for name,digest in M['website_inputs'].items():assert hash_file(web/name)==digest,'WEBSITE_LOCK_INPUT_'+name
  run('node-versions',['bash','-euc','node --version\nnpm --version'],20,cwd=web)
  run('npm-ci',['npm','ci','--no-audit','--no-fund'],900,cwd=web,cap=8*1024**2)
  run('complete-docgen-dependencies',['python','-m','pip','install','--user','--no-input','-e','tools/docgen','nbconvert==7.16.6','beautifulsoup4','requests','pyyaml'],300)
  run('docgen-resolved-environment',['python','-m','pip','freeze'],30)
  run('baseline-complete-website-docgen',['python','/packet/website_generate.py'],600)
  run('baseline-website-tests',['npm','test'],300,cwd=web)
  elapsed,_=run('baseline-whole-website-build',['npm','run','build'],900,cwd=web,cap=8*1024**2)
  projection(elapsed);candidate()
  run('candidate-complete-website-docgen',['python','/packet/website_generate.py'],600)
  run('candidate-website-tests',['npm','test'],300,cwd=web)
  run('candidate-whole-website-build',['npm','run','build'],900,cwd=web,cap=8*1024**2)
  run('actual-npm-start-and-ssr-render',['python','/packet/website_check.py'],600,cwd=web)
 else:
  assert LANE=='F'
  original=(S/'environment.yml').read_text();block='channels:\n  - conda-forge\n  - default\n';assert original.count(block)==1
  validation=original.replace(block,'channels:\n  - conda-forge\n',1)
  (R/'environment-validation.yml').write_text(validation)
  RESULT['channel_difference']='conda-forge only; every declared dependency and pin retained'
  run('full-environment',['/opt/bootstrap/micromamba','--no-rc','create','--root-prefix','/runtime/mamba','--name','synapseml','--file',str(R/'environment-validation.yml'),'--override-channels','--channel','conda-forge','--yes'],1200,cap=8*1024**2)
  prefix=R/'mamba/envs/synapseml';assert (prefix/'bin/python').exists()
  os.environ.update(CONDA_DEFAULT_ENV='synapseml',CONDA_PREFIX=str(prefix),PATH=str(prefix/'bin')+':'+os.environ['PATH'])
  _,resolved=run('resolved-environment',['conda','list','--name','synapseml','--explicit'],60)
  assert b'repo.anaconda.com' not in resolved,'DEFAULT_CHANNEL_NOT_ALLOWED'
  run('python-versions',['python','-c','import sys; assert sys.version_info[:3] == (3,11,8); import pyspark; assert pyspark.__version__ == "3.5.0"; print(sys.version,pyspark.__version__)'],20)
  run('pip-freeze',['python','-m','pip','list','--format=json'],60)
  run('pip-check',['python','-m','pip','check'],60)
  code = """import importlib.metadata as m,json
items=[]
for dist in m.distributions():
 if (dist.read_text('INSTALLER') or '').strip()!='pip':continue
 direct=json.loads(dist.read_text('direct_url.json') or '{}')
 items.append({'name':dist.metadata['Name'],'version':dist.version,'direct_url':direct.get('url')})
print(json.dumps(items))
"""
  _,raw=run('resolved-pip-artifact-specs',['python','-c',code],60)
  pip_specs=json.loads(raw);assert pip_specs,'NO_PIP_MANAGED_DISTRIBUTIONS'
  requirements=[]
  horovod='https://mmlspark.blob.core.windows.net/installers/horovod-0.28.1-cp311-cp311-linux_x86_64.whl'
  for item in pip_specs:
   assert re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*',item['name'])
   assert re.fullmatch('[A-Za-z0-9.+!_-]+',item['version'])
   if item['direct_url']:
    assert item['direct_url']==horovod,'UNREVIEWED_DIRECT_PIP_SOURCE'
    requirements.append(item['name']+' @ '+item['direct_url'])
   else:requirements.append(item['name']+'=='+item['version'])
  locked=R/'resolved-pip-artifacts.txt';locked.write_text('\n'.join(requirements)+'\n')
  wheelhouse=R/'resolved-pip-artifacts';wheelhouse.mkdir()
  run('hashable-pip-downloads',['python','-m','pip','download','--no-deps','--dest',str(wheelhouse),'-r',str(locked)],300,cap=8*1024**2)
  artifacts=list(wheelhouse.iterdir());assert len(artifacts)==len(pip_specs),'INCOMPLETE_RESOLVED_PIP_ARTIFACTS'
  RESULT['resolved_pip_distribution_specs']=pip_specs
  RESULT['resolved_pip_downloaded_artifacts']=[{'file':path.name,'bytes':path.stat().st_size,'sha256':hash_file(path)} for path in artifacts]
  RESULT['pip_artifact_evidence']='Post-resolution exact pip-managed versions/direct wheel source; downloaded or reused from the same container cache, not a pre-resolution lock.'
  RESULT['installed_record_digests']={str(f.relative_to(prefix)):hashlib.sha256(f.read_bytes()).hexdigest() for f in prefix.glob('lib/python*/site-packages/*.dist-info/RECORD')}
  sbt('public-datasets',['getDatasets'],240)
  datasets=list(S.glob('**/datasets-2023-04-03.tgz'));assert len(datasets)==1
  assert datasets[0].stat().st_size==M['public_dataset']['bytes'],'DATASET_ARCHIVE_SIZE'
  assert hash_file(datasets[0])==M['public_dataset']['sha256'],'DATASET_ARCHIVE_SHA'
  RESULT['dataset_archive']={'bytes':datasets[0].stat().st_size,'sha256':hash_file(datasets[0])}
  verify_extracted_datasets()
  _,envlist=run('confirmed-conda-env',['conda','env','list'],20)
  assert any(line.startswith(b'synapseml ') for line in envlist.splitlines()),'CONDA_ENV_FORMAT'
  elapsed,_=sbt('baseline-full-codegen-install',['codegen','core/installPipPackage','lightgbm/installPipPackage','publishM2'],1500)
  verify_native()
  # Baseline API probe is outside the original source tree, so the contribution patch still adds its file once.
  pyfile=S/'core/src/test/python/synapsemltest/train/baseline_inherited_label_probe.py'
  pyfile.parent.mkdir(parents=True,exist_ok=True)
  assert not pyfile.exists();shutil.copy2(P/'test_train_classifier_columns.py',pyfile)
  out=pytest('python-baseline-inherited-label',[
   str(pyfile)+'::TrainClassifierColumnsSpec::test_inherits_python_configured_and_default_learner_labels'],300,expected=1)
  assert b'labelCol' in out and b'ERROR collecting' not in out and b'ImportError' not in out,'INVALID_PYTHON_BASELINE'
  projection(elapsed);candidate();diagnostic('PythonClassifierFuzz555.scala')
  sbt('candidate-core-compile',['core/Test/compile'])
  sbt('candidate-full-codegen-packages',['codegen','core/installPipPackage','lightgbm/installPipPackage','publishM2'],1200)
  jars=list(S.glob('core/target/scala-*/synapseml-core_*-0.0.2-i555-SNAPSHOT.jar'));assert len(jars)==1
  roots=list(S.glob('core/target/scala-*/generated/src/python'));assert len(roots)==1
  os.environ.update(SYNAPSEML_EXPECTED_CORE_JAR=str(jars[0]),SYNAPSEML_EXPECTED_GENERATED_ROOT=str(roots[0]),SYNAPSEML_EXPECTED_NATIVE_JAR_SHA256=M['native_jar_sha256'])
  _,provenance=run('generated-provenance-and-real-demo',['python','/packet/provenance_check.py'],300)
  RESULT['spark_resolution_and_demo']=provenance.decode(errors='replace')[-65536:]
  verify_native(require_ivy=True)
  _,out=sbt('actual-upstream-fuzz-config',['show core / testgenArgs'],180)
  configs=[]
  for line in out.decode().splitlines():
   if line.startswith('[info] {'):
    try:item=json.loads(line[len('[info] '):])
    except json.JSONDecodeError:continue
    if 'topDir' in item and 'targetDir' in item:configs.append(item)
  assert len(configs)==1,'AMBIGUOUS_TESTGEN_CONFIG'
  conf=R/'py-testgen-config.json';conf.write_text(json.dumps(configs[0]))
  assert Path(configs[0]['topDir'])==S/'core' and configs[0]['version']=='0.0.2-i555-SNAPSHOT'
  sbt('affected-upstream-python-fuzz-generation',[
   'core/Test/runMain com.microsoft.azure.synapse.ml.train.PythonClassifierFuzz555 /runtime/py-testgen-config.json'],300)
  fuzz=list(S.glob('core/target/scala-*/generated/test/python/synapsemltest/train/test_verify_train_classifier.py'))
  assert len(fuzz)==1,'NO_GENERATED_CLASSIFIER_FUZZ_SUITE'
  pytest('complete-handwritten-python',[
   S/'core/src/test/python/synapsemltest/train/test_train_classifier_columns.py',
   S/'core/src/test/python/synapsemltest/featurize/test_featurize.py'],600)
  pytest('complete-generated-python',[fuzz[0]],360)
  assert sum(x['tests'] for x in RESULT['python_reports'] if x['label'].startswith('complete-'))==9
  run('prepare-complete-website-page',['python','/packet/website_sample_copy.py'],30)
  os.environ['PYSPARK_SUBMIT_ARGS']='--master local[2] --driver-memory 1g pyspark-shell'
  pytest('complete-website-page',[R/'website-sample/_Train.md'],420,rootdir=R/'website-sample',extra_args=['--codeblocks'])
  run('core-sdist',['python','setup.py','sdist','--dist-dir',str(R/'shipping')],180,cwd=roots[0])
  run('shipping-artifact-contents',['python','/packet/shipping_check.py'],120)
 if LANE=='B':
  _,changed=run('all-tracked-baseline-scope',['git','diff','--name-only'],10);assert not changed.strip(),'BASELINE_TRACKED_MUTATION'
  expected_map=M['baseline_source_sha256']
 else:
  _,changed=run('all-tracked-candidate-scope',['git','diff','--name-only'],10)
  assert sorted(changed.decode().splitlines())==sorted(M['source_allowlist']),'UNEXPECTED_TRACKED_SOURCE_MUTATION'
  expected_map=M['candidate_source_sha256']
 for path,expected in expected_map.items():assert hash_file(S/path)==expected,'FINAL_SOURCE_MUTATION_'+path
 if LANE!='B':
  _,patch=run('final-patch',M['canonical_diff_command']+M['source_allowlist'],20)
  assert hashlib.sha256(patch).hexdigest()==M['patch_sha256'],'TESTED_PATCH_IDENTITY'
  RESULT['patch_sha256']=hashlib.sha256(patch).hexdigest()
 RESULT.update(status='SUCCESS',lane_pass=True)
except BaseException as exc:
 RESULT.update(status='FAILED_OR_PARKED',error=str(exc));raise
finally:
 RESULT['steps']=LOGS
 failures=[]
 for path in list(S.glob('**/test-reports/*.xml')):
  try:
   report=ET.parse(path).getroot()
   for case in report.iter('testcase'):
    for failed in list(case.findall('failure'))+list(case.findall('error')):
     failures.append({'report':str(path.relative_to(S)),'case':case.attrib.get('name'),'message':failed.attrib.get('message','')[:2000],'details':(failed.text or '')[:4000]})
  except (ET.ParseError,OSError) as problem:failures.append({'report':str(path),'parse_error':str(problem)})
 RESULT['failure_record_count']=len(failures)
 for limit in (4000,2000,1000,500,250,100,32):
  bounded=[{key:(value[:limit] if key in ('message','details') else value) for key,value in item.items()} for item in failures]
  encoded=json.dumps(bounded)
  if len(encoded.encode())<=65536:break
 else:
  RESULT['failure_report_complete']=False
  encoded=json.dumps([{'report':path,'cases':sum(item.get('report')==path for item in failures)} for path in sorted({item.get('report','UNKNOWN') for item in failures})])
 RESULT['failure_report_excerpt']=encoded;RESULT.setdefault('failure_report_complete',True)
 (R/'result.json').write_text(json.dumps(RESULT,indent=2))
 for step in LOGS:
  if step['exit'] != step['expected_exit']:
   print('STEP_LOG '+step['label']+'\n'+(R/(step['label']+'.log')).read_text(errors='replace')[-1024*1024:],flush=True)
 print('LANE_RESULT '+json.dumps(RESULT),flush=True)
