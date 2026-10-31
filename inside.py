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
def verify_native():
 import zipfile
 names={'lightgbmlib-3.3.510.jar','com.microsoft.ml.lightgbm_lightgbmlib-3.3.510.jar'}
 jars=[path for path in (R/'home').rglob('*.jar') if path.name in names]
 assert jars,'NATIVE_ARTIFACT_MISSING'
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
 version = '0.0.1-i789-SNAPSHOT' if RESULT['baseline_product_unchanged'] else '0.0.2-i789-SNAPSHOT'
 return run(label,['sbt',f'set ThisBuild / version := "{version}"']+tasks,seconds)
def projection(calibration):
 checkpoint=json.loads((R/'memory-checkpoint.json').read_text())
 assert checkpoint['samples']>=2 and time.time()-checkpoint['at']<20,'MISSING_FRESH_MEMORY_CALIBRATION'
 memory=M['memory_projection_factor']*checkpoint['max_sampled_working_set_upper_bytes']+J['candidate_memory_extra_gib']*1024**3
 assert memory<J['memory_gib']*1024**3*M['working_set_stop_fraction'],'FULL_FINISH_MEMORY_NO_GO'
 RESULT['memory_projection']={'sampled_not_continuous_peak':checkpoint,'assumed_factor':M['memory_projection_factor'],'extra_gib':J['candidate_memory_extra_gib'],'projected_bytes':memory}
 projected=(J['remaining_factor']*calibration+J['remaining_extra_seconds'])*M['projection_multiplier']
 RESULT.update(calibration_seconds=calibration,projected_remaining_seconds=projected)
 assert time.time()+projected<END,'FULL_FINISH_PROJECTION_NO_GO'
def candidate():
 run('apply-check',['git','apply','--check','/packet/production-proposal.patch'],10)
 run('apply-once',['git','apply','/packet/production-proposal.patch'],10)
 RESULT['baseline_product_unchanged']=False
 run('scope',['git','add','--intent-to-add']+M['source_allowlist'][2:],10)
 _,out=run('paths',['git','diff','--name-only'],10)
 assert sorted(out.decode().splitlines())==sorted(M['source_allowlist']),'SOURCE_SCOPE'
def attest_reports():
 reports=list(S.glob('**/test-reports/*.xml'))
 facts=[]
 for path in reports:
  root=ET.parse(path).getroot()
  facts.append({'path':str(path.relative_to(S)),'suite':root.attrib.get('name'),'tests':int(root.attrib.get('tests',0)),'failures':int(root.attrib.get('failures',0)),'errors':int(root.attrib.get('errors',0)),'skipped':int(root.attrib.get('skipped',0))})
 assert facts,'NO_SCALA_REPORTS'
 assert all(x['failures']==x['errors']==0 for x in facts),'FAILED_SCALA_REPORT'
 assert any(x['suite']==M['scala_suite'] and x['tests']>0 for x in facts),'NEW_SCALA_SUITE_NOT_RUN'
 RESULT['scala_reports']=facts
try:
 assert not S.exists();shutil.copytree('/source',S)
 _,commit=run('source-commit',['git','rev-parse','HEAD'],10);assert commit.decode().strip()==M['source_commit']
 for source,target in [('VerifyFeatureFractionByNode.scala',M['source_allowlist'][2]),('test_feature_fraction_by_node.py',M['source_allowlist'][3])]:
  dest=S/target;assert not dest.exists();dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(P/source,dest)
 run('versions',['bash','-euc','java -version\npython3 -V\ngit --version'],20)
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
  sbt('public-datasets',['getDatasets'],240)
  datasets=list(S.glob('**/datasets-2023-04-03.tgz'));assert len(datasets)==1
  assert datasets[0].stat().st_size<=4*1024**3,'DATASET_ARCHIVE_CAP'
  RESULT['dataset_archive']={'bytes':datasets[0].stat().st_size,'sha256':hash_file(datasets[0])}
  elapsed,_=sbt('scala-baseline-calibration',['core/Test/compile','lightgbm/Test/compile'])
  verify_native()
  _,out=run('baseline-negative',['sbt','set ThisBuild / version := "0.0.1-i789-SNAPSHOT"',f'lightgbm/testOnly {M["scala_suite"]} -- -z "emits explicit featureFractionByNode"'],240,expected=1)
  assert re.search(rb'Tests:\s*succeeded\s+0,\s*failed\s+3',out),'BASELINE_NOT_THREE_ASSERTION_FAILURES'
  assert b'Compilation failed' not in out and b'ImportError' not in out,'INVALID_BASELINE'
  assert out.count(b'EXPLICIT_NODE_FRACTION_MISSING')>=3,'BASELINE_NOT_EXPECTED_MAPPING_ASSERTIONS'
  RESULT['baseline_negative_assertions']=3
  RESULT['baseline_negative_excerpt']=out.decode(errors='replace')[-4000:]
  projection(elapsed);candidate()
  sbt('affected-core-suite',['core/testOnly '+M['existing_scala_suites'][0]],360)
  sbt('complete-lightgbm-suites',['lightgbm/testOnly '+M['scala_suite']+' '+M['existing_scala_suites'][1]],1200)
  attest_reports()
 else:
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
  assert datasets[0].stat().st_size<=4*1024**3,'DATASET_ARCHIVE_CAP'
  RESULT['dataset_archive']={'bytes':datasets[0].stat().st_size,'sha256':hash_file(datasets[0])}
  _,envlist=run('confirmed-conda-env',['conda','env','list'],20)
  assert any(line.startswith(b'synapseml ') for line in envlist.splitlines()),'CONDA_ENV_FORMAT'
  elapsed,_=sbt('baseline-full-codegen-install',['codegen','core/installPipPackage','lightgbm/installPipPackage','publishM2'],1500)
  verify_native()
  pyfile=S/M['source_allowlist'][3]
  _,out=run('python-baseline-negative',['python','-m','pytest',str(pyfile)+'::FeatureFractionByNodeTests::test_default_copy_clear_and_estimator_persistence','-q'],240,expected=1)
  assert b'featureFractionByNode' in out and b'FAILED' in out and b'ERROR collecting' not in out and b'ImportError' not in out,'INVALID_PYTHON_BASELINE'
  assert b'KeyError' in out or b'NoSuchElementException' in out,'BASELINE_NOT_MISSING_DEFAULT'
  RESULT['python_baseline_negative_excerpt']=out.decode(errors='replace')[-4000:]
  projection(elapsed);candidate()
  sbt('candidate-full-codegen-packages',['codegen','core/installPipPackage','lightgbm/installPipPackage','publishM2'],1200)
  jars=list(S.glob('lightgbm/target/scala-*/synapseml-lightgbm_*-0.0.2-i789-SNAPSHOT.jar'));assert len(jars)==1,'AMBIGUOUS_BUILT_JAR'
  roots=list(S.glob('lightgbm/target/scala-*/generated/src/python'));assert len(roots)==1
  os.environ.update(SYNAPSEML_EXPECTED_LIGHTGBM_JAR=str(jars[0]),SYNAPSEML_EXPECTED_GENERATED_ROOT=str(roots[0]))
  _,provenance=run('generated-provenance',['python','/packet/provenance_check.py'],180)
  RESULT['spark_resolution_excerpt']=provenance.decode(errors='replace')[-12000:]
  sbt('upstream-lightgbm-python-suite',['lightgbm/testPython'],1200)
  reports=list(S.glob('target/python-test-results-*.xml'));assert reports,'NO_PYTHON_REPORT'
  facts=[]
  for path in reports:
   root=ET.parse(path).getroot();suites=[root] if root.tag=='testsuite' else list(root)
   for suite in suites:facts.append({k:int(suite.attrib.get(k,0)) for k in ['tests','failures','errors','skipped']})
  assert sum(x['tests'] for x in facts)>0 and all(x['failures']==x['errors']==0 for x in facts),'INCOMPLETE_PYTHON_SUITE'
  RESULT['python_reports']=facts
 for path,expected in M['candidate_source_sha256'].items():
  assert hashlib.sha256((S/path).read_bytes()).hexdigest()==expected,'FINAL_SOURCE_MUTATION_'+path
 _,patch=run('final-patch',['git','diff','--binary'],20)
 RESULT.update(status='SUCCESS',lane_pass=True,patch_sha256=hashlib.sha256(patch).hexdigest())
 print('TESTED_PATCH_BASE64 '+__import__('base64').b64encode(patch).decode(),flush=True)
except BaseException as exc:
 RESULT.update(status='FAILED_OR_PARKED',error=str(exc));raise
finally:
 RESULT['steps']=LOGS
 (R/'result.json').write_text(json.dumps(RESULT,indent=2))
 # Failed-step excerpts come before success summaries; only synthetic/public execution logs.
 for step in LOGS:
  if step['exit'] != step['expected_exit']:
   print('STEP_LOG '+step['label']+'\n'+(R/(step['label']+'.log')).read_text(errors='replace')[-12000:],flush=True)
 print('LANE_RESULT '+json.dumps(RESULT),flush=True)
