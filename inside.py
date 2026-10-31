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
 run('scope',['git','add','--intent-to-add',M['source_new_test_path']],10)
 _,out=run('paths',['git','diff','--no-renames','--name-only'],10)
 assert sorted(out.decode().splitlines())==sorted(M['source_allowlist']),'SOURCE_SCOPE'
def attest_reports():
 reports=list(S.glob('**/test-reports/*.xml'))
 facts=[]
 for path in reports:
  root=ET.parse(path).getroot()
  facts.append({'path':str(path.relative_to(S)),'suite':root.attrib.get('name'),'tests':int(root.attrib.get('tests',0)),'failures':int(root.attrib.get('failures',0)),'errors':int(root.attrib.get('errors',0)),'skipped':int(root.attrib.get('skipped',0))})
 assert facts,'NO_SCALA_REPORTS'
 assert all(x['failures']==x['errors']==0 for x in facts),'FAILED_SCALA_REPORT'
 assert any(x['suite']==M['scala_suite'] and x['tests']==M['expected_new_scala_cases'] and x['skipped']==0 for x in facts),'NEW_SCALA_SUITE_INCOMPLETE'
 assert any(x['suite']==M['existing_scala_suites'][1] for x in facts),'EXISTING_COMMON_SUITE_NOT_RUN'
 RESULT['scala_reports']=facts
try:
 assert LANE=='B' and not S.exists();shutil.copytree('/source',S)
 _,commit=run('source-commit',['git','rev-parse','HEAD'],10);assert commit.decode().strip()==M['source_commit']
 for path,expected in M['baseline_source_sha256'].items():
  assert hashlib.sha256((S/path).read_bytes()).hexdigest()==expected,'BASELINE_SOURCE_HASH_'+path
 assert (S/M['source_old_test_path']).is_file() and not (S/M['source_new_test_path']).exists()
 run('versions',['bash','-euc','java -version\npython3 -V\ngit --version'],20)
 _,forks=sbt('fork-settings',['show core / Test / fork','show lightgbm / Test / fork'],180)
 assert len(re.findall(rb'\[info\]\s+false\s*$',forks,re.M))>=2,'UNCONFIRMED_FORK_SETTINGS'
 sbt('public-datasets',['getDatasets'],240)
 datasets=list(S.glob('**/datasets-2023-04-03.tgz'));assert len(datasets)==1
 assert datasets[0].stat().st_size<=4*1024**3,'DATASET_ARCHIVE_CAP'
 RESULT['dataset_archive']={'bytes':datasets[0].stat().st_size,'sha256':hash_file(datasets[0])}
 assert RESULT['dataset_archive']['sha256']=='f8f8592c30451628ef3c0f63aab3b1f85d18acf6565de0b48086b273e94622fb'
 elapsed,_=sbt('scala-baseline-calibration',['core/Test/compile','lightgbm/Test/compile'])
 verify_native()
 _,out=run('baseline-ci-orphan',['sbt','set ThisBuild / version := "0.0.1-i789-SNAPSHOT"','core/testOnly '+M['coverage_suite']],240,expected=1)
 assert re.search(rb'Tests:\s*succeeded\s+0,\s*failed\s+1',out),'BASELINE_NOT_ONE_GUARD_FAILURE'
 assert b'1 test suite(s) are never run by CI.' in out and M['old_scala_suite'].encode() in out,'BASELINE_NOT_EXACT_ORPHAN'
 assert b'Compilation failed' not in out and b'ImportError' not in out,'INVALID_BASELINE'
 RESULT['baseline_ci_orphans']=1;RESULT['baseline_ci_orphan_excerpt']=out.decode(errors='replace')[-12000:]
 projection(elapsed);candidate()
 assert not (S/M['source_old_test_path']).exists() and (S/M['source_new_test_path']).is_file()
 sbt('candidate-scala-checks',['lightgbm/Test/compile','lightgbm/Test/scalastyle'],600)
 sbt('candidate-ci-coverage',['core/testOnly '+M['coverage_suite']],240)
 sbt('affected-core-suite',['core/testOnly '+M['existing_scala_suites'][0]],360)
 sbt('complete-lightgbm-suites',['lightgbm/testOnly '+M['scala_suite']+' '+M['existing_scala_suites'][1]],600)
 attest_reports()
 assert any(x['suite']==M['coverage_suite'] and x['tests']==1 and x['skipped']==0 for x in RESULT['scala_reports']),'CI_GUARD_NOT_RUN'
 assert not any(x['suite']==M['old_scala_suite'] for x in RESULT['scala_reports']),'STALE_OLD_SUITE_REPORT'
 assert sum(x['tests']-x['skipped'] for x in RESULT['scala_reports'])==104,'INCOMPLETE_AFFECTED_SCALA_TOTAL'
 # The same actual pipeline selector is checked by the unmodified coverage guard.
 pipeline=(S/'pipeline.yaml').read_text();block=re.search(r'(?ms)^      lightgbm1:\n(.*?)(?=^      \w+:)',pipeline).group(1)
 assert 'PACKAGE: "lightgbm.split1"' in block and 'TEST_CLASSES:' not in block
 assert M['scala_suite'].startswith('com.microsoft.azure.synapse.ml.lightgbm.split1.')
 RESULT['ci_selector_proof']={'leg':'lightgbm1','resolved_test_spec':'com.microsoft.azure.synapse.ml.lightgbm.split1.**','suite':M['scala_suite'],'guard_passed':True,'full_ci_leg_executed':False}

 for path,expected in M['candidate_source_sha256'].items():
  assert hashlib.sha256((S/path).read_bytes()).hexdigest()==expected,'FINAL_SOURCE_MUTATION_'+path
 _,patch=run('final-patch',['git','diff','--binary','--full-index','--no-renames'],20)
 assert hashlib.sha256(patch).hexdigest()==M['patch_sha256'],'TESTED_PATCH_IDENTITY'
 RESULT.update(status='SUCCESS',lane_pass=True,patch_sha256=hashlib.sha256(patch).hexdigest(),tested_patch_base64=__import__('base64').b64encode(patch).decode())
 print('TESTED_PATCH_BASE64 '+__import__('base64').b64encode(patch).decode(),flush=True)
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
 # Preserve a complete JSON entry for every failing case/suite within the bound.
 RESULT['failure_record_count']=len(failures)
 if any('parse_error' in item for item in failures):RESULT['failure_report_complete']=False
 for limit in (4000,2000,1000,500,250,100,32):
  bounded=[{key:(value[:limit] if key in ('message','details') else value)
            for key,value in item.items()} for item in failures]
  encoded=json.dumps(bounded)
  if len(encoded.encode())<=65536:break
 else:
  # A labelled overflow is never represented as complete evidence.
  RESULT['failure_report_complete']=False
  encoded=json.dumps([{'report':path,'cases':sum(item.get('report')==path for item in failures)}
                      for path in sorted({item.get('report','UNKNOWN') for item in failures})])
 RESULT['failure_report_excerpt']=encoded
 RESULT.setdefault('failure_report_complete',True)
 RESULT['failure_details_character_limit']=limit
 if any(step['label']=='black-check' and step['exit']!=0 for step in LOGS):
  print('BLACK_DIFF_EVIDENCE\n'+(R/'black-diff.log').read_text(errors='replace')[-16000:],flush=True)
 (R/'result.json').write_text(json.dumps(RESULT,indent=2))
 # Failed-step excerpts come before success summaries; only synthetic/public execution logs.
 for step in LOGS:
  if step['exit'] != step['expected_exit']:
   print('STEP_LOG '+step['label']+'\n'+(R/(step['label']+'.log')).read_text(errors='replace')[-1024*1024:],flush=True)
 print('LANE_RESULT '+json.dumps(RESULT),flush=True)
