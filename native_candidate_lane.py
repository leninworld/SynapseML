"""Proposed exact native candidate acceptance; caller provides approved G0-derived Lab and clock."""
import hashlib
import json
from pathlib import PurePosixPath
import shutil
import time
import xml.etree.ElementTree as ET

from validation_runtime import require
from junit_reader import verified_report
from g0_contract import clock_admission
from candidate_contract import candidate_unchanged
from native_events import classify_log
from causal_diagnostics import run_with_causal_snapshot

REGISTRATION='com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_report(path,suite,wanted,allowed_skips=()):
    result=verified_report(path,suite,wanted,(),allowed_skips)
    root=ET.parse(path).getroot()
    result['test_seconds']={case.get('name'):float(case.get('time','0'))for case in root.iter('testcase')}
    return result


def complete_suite(lane,config,label,suite,seconds,module='core'):
    reports=lane.source/('target/test-reports' if module=='root' else module+'/target/test-reports')
    prior=lane.root/(label+'-prior-junit');prior.mkdir()
    for path in reports.glob('*.xml'):
        shutil.copy2(path,prior/path.name)
        path.unlink()  # Only this owned ephemeral report directory; preserve raw prior bytes first.
    selector=('root/testOnly ' if module=='root' else module+'/testOnly ')+suite
    output=lane.sbt(label,[selector],seconds)
    matches=[path for path in reports.glob('*.xml')if ET.parse(path).getroot().get('name')==suite]
    require(len(matches)==1,'Missing/duplicate actual native suite')
    target=lane.root/(label+'.xml');shutil.copy2(matches[0],target)
    result=checked_report(target,suite,config['suite_cases'][suite],
                          config.get('suite_allowed_skips',{}).get(suite,()))
    result.update(events=classify_log(output),
                  per_draw_timing='UNESTABLISHED_UNLESS_ACTUAL_LOG_UID_AND_TEST_CONTEXT_ALLOW_PAIRING',
                  full_unfiltered_selector=selector)
    (lane.root/(label+'-selection.json')).write_text(json.dumps(result,indent=2))
    return result


def validate(lane):
    config=lane.manifest['candidate_native']
    require(lane.manifest['execution_enabled'] is True,'Candidate execution not approved')
    require(lane.name in config['lanes'],'Unknown candidate native lane')
    limits=config['lanes'][lane.name]
    candidate_unchanged(lane,'pre-native')
    grouped=lane.name in config.get('causal_groups',{})
    discovery_seconds=limits.get('discovery_seconds',0) if grouped else 0
    after_compile=discovery_seconds+limits['suite_seconds']+limits['registration_seconds']+limits.get('fuzzing_seconds',0)+\
        limits['reserve_seconds']
    compile_cap=min(limits['compile_max_seconds'],lane.deadline-time.time()-after_compile)
    require(compile_cap>0,'HOLD complete native acceptance cannot fit original clock')
    # Compile catches actual imports/API/codegen compile dependencies before expensive fits.
    compile_tasks=['core/scalastyle','core/Test/scalastyle']
    compile_tasks+=['compile','Test / compile'] if lane.name=='candidate-legacy' else \
        ['core/compile','core/Test/compile']
    lane.sbt('candidate-native-lint-import-compile',compile_tasks,compile_cap)
    clock_admission(time.time(),lane.deadline,after_compile)
    if grouped:
        validate_causal_groups(config)
        raw=lane.sbt('candidate-causal-complete-discovery',['show core/Test/definedTests'],discovery_seconds)
        discovery=causal_discovery(raw,config['causal_suite_inventory'])
        (lane.root/'candidate-causal-discovery.json').write_text(json.dumps(discovery,indent=2))
        actual_discovery_controls(raw,config['causal_suite_inventory'],lane.root)
        clock_admission(time.time(),lane.deadline,limits['suite_seconds']+limits['reserve_seconds'])
        try:return causal_package(lane,config,limits)
        finally:candidate_unchanged(lane,'final-native-causal')
    suite=complete_suite(lane,config,'candidate-full-native-suite',limits['suite'],limits['suite_seconds'])
    registration=None
    if limits['registration_seconds']:
        clock_admission(time.time(),lane.deadline,limits['registration_seconds']+
                        limits.get('fuzzing_seconds',0)+limits['reserve_seconds'])
        registration=complete_suite(lane,config,'candidate-pipeline-registration',REGISTRATION,
                                    limits['registration_seconds'])
    fuzzing=None
    if limits.get('fuzzing_seconds'):
        clock_admission(time.time(),lane.deadline,limits['fuzzing_seconds']+limits['reserve_seconds'])
        fuzzing=complete_suite(lane,config,'candidate-root-fuzzing',
            'com.microsoft.azure.synapse.ml.core.test.fuzzing.FuzzingTest',limits['fuzzing_seconds'],'root')
    candidate_unchanged(lane,'final-native')
    result={'state':'REQUIRED_NATIVE_CANDIDATE_LANE_COMPLETED_FINAL_REVIEW_REQUIRED',
            'lane':lane.name,'sourcePin':config['source_commit'],'candidateFiles':config['candidate_files'],
            'suite':suite,'registration':registration,'rootFuzzing':fuzzing,'fullProductValidation':False,
            'generatedInstalledPythonAcceptance':'SEPARATE_REQUIRED_ARTIFACT_CONSUMER_NOT_PROVED_HERE',
            'notebookWebsiteAcceptance':'SEPARATE_REQUIRED_NOT_PROVED_HERE'}
    (lane.root/'native-candidate-result.json').write_text(json.dumps(result,indent=2))
    return result


def package_reports(paths,output):
    """Actual full causal collection, with independent ScalaTest summary reconciliation."""
    import re
    totals=re.findall(r'\[info\] Total number of tests run: ([0-9]+)',output)
    suite_totals=re.findall(r'\[info\] Suites: completed ([0-9]+), aborted ([0-9]+)',output)
    require(len(totals)==len(suite_totals)==1,'Missing/ambiguous full causal framework totals')
    result={'suites':{},'caseIDs':[],'failures':{},'skips':[]}
    for path in paths:
        document=ET.parse(path).getroot();name=document.get('name','')
        require(re.fullmatch(r'com\.microsoft\.azure\.synapse\.ml\.causal(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+',name),
                'Foreign or unsafe causal report name')
        require(name not in result['suites'],'Duplicate causal suite report')
        cases=list(document.iter('testcase'));names=[case.get('name')for case in cases]
        suite_errors=list(document.findall('error'))+list(document.findall('failure'))
        require((names or suite_errors) and all(names) and len(names)==len(set(names)),
                'Empty/duplicate causal case names')
        require(int(document.get('tests'))==len(cases),'Causal XML total mismatch')
        rows=[];failed=[]
        for case in cases:
            row={'name':case.get('name'),'seconds':float(case.get('time','0')),
                 'failures':[{'message':v.get('message'),'text':v.text}for v in
                             list(case.findall('failure'))+list(case.findall('error'))],
                 'skipped':bool(case.findall('skipped'))}
            rows.append(row);result['caseIDs'].append([name,row['name']])
            if row['failures']:failed.append(row)
            if row['skipped']:result['skips'].append([name,row['name']])
        result['suites'][name]=rows
        if suite_errors:failed.append({'name':'<SUITE_ABORT_NOT_A_TEST_ID>',
            'failures':[{'message':v.get('message'),'text':v.text}for v in suite_errors]})
        if failed:result['failures'][name]=failed
    require(len(result['caseIDs'])==int(totals[0]),'Causal XML versus framework case count mismatch')
    require(len(result['suites'])==sum(map(int,suite_totals[0])),
            'Causal suite reports incomplete')
    result['abortedSuites']=int(suite_totals[0][1])
    return result


CAUSAL_PREFIX='com.microsoft.azure.synapse.ml.causal.'
CAUSAL_GROUP_IDS=('candidate-causal-dml','candidate-causal-other',
                  'candidate-causal-synthetic-control','candidate-causal-synthetic-did',
                  'candidate-causal-cls')


def validate_causal_groups(config):
    groups=config['causal_groups']
    require(set(groups)==set(CAUSAL_GROUP_IDS),'Exact five causal groups required')
    inventory=config['causal_suite_inventory']
    require(len(inventory)==15 and len(set(inventory))==15,'Exact fifteen causal suites required')
    union=[suite for group in groups.values() for suite in group]
    require(len(union)==15 and len(set(union))==15 and set(union)==set(inventory),
            'Causal groups must have disjoint complete source inventory union')
    for name,suites in groups.items():
        require(suites and config['lanes'][name]['suites']==suites,'Causal lane group binding mismatch')
        require(0<config['lanes'][name]['suite_seconds']<=1500,'Causal suite budget exceeds original')
        require(0<config['lanes'][name]['discovery_seconds']<=60,'Causal discovery budget invalid')
    return union


def causal_discovery(raw,expected):
    import re
    # A strict dependency gate: complete native sbt show output is retained.
    # No prior real definedTests format is claimed; unexpected shapes HOLD.
    records=re.findall(r'\bTest\s+('+re.escape(CAUSAL_PREFIX)+r'[A-Za-z_$][A-Za-z0-9_.$]*)\s*:',raw)
    all_mentions=re.findall(re.escape(CAUSAL_PREFIX)+r'[A-Za-z_$][A-Za-z0-9_.$]*',raw)
    require(records and set(records)==set(all_mentions),'Unrecognized causal definedTests record')
    require(len(records)==len(set(records)),'Duplicate causal definedTests record')
    require(set(records)==set(expected),'Complete causal definedTests/source inventory mismatch')
    return {'complete':True,'suiteIDs':sorted(records),'count':len(records),
            'rawSHA256':hashlib.sha256(raw.encode()).hexdigest(),
            'sourceInventory':sorted(expected),'scope':'Full causal discovery; no fits executed by this task'}


def actual_discovery_controls(raw,expected,root):
    causal_discovery(raw,expected)
    import re
    first=expected[0]
    mutations={'missing':raw.replace(first,'com.example.NonCausalFixture'),
               'new':raw+'\n[info] * Test '+CAUSAL_PREFIX+'UnexpectedFixture : subclass(false, org.scalatest.Suite)\n',
               'duplicate':raw+'\n[info] * Test '+first+' : subclass(false, org.scalatest.Suite)\n',
               'malformed':re.sub(r'\bTest(?=\s+'+re.escape(first)+r'\s*:)', 'Unrecognized',raw)}
    results=[]
    for name,changed in mutations.items():
        try:causal_discovery(changed,expected)
        except RuntimeError as error:results.append({'id':name,'rejected':True,'error':str(error)})
        else:raise RuntimeError('Actual-output discovery mutation accepted: '+name)
    result={'state':'ACTUAL_OUTPUT_DERIVED_DISCOVERY_CONTROLS_COMPLETE','rawSHA256':hashlib.sha256(raw.encode()).hexdigest(),
            'positive':True,'negatives':results,'projectFitsRun':False}
    (root/'candidate-causal-discovery-controls.json').write_text(json.dumps(result,indent=2))
    return result


def causal_package(lane,config,limits):
    reports=lane.source/'core/target/test-reports'
    prior=lane.root/'candidate-causal-prior-junit';prior.mkdir()
    for path in reports.glob('*.xml'):
        shutil.copy2(path,prior/path.name);path.unlink()
    selected=config['causal_groups'][lane.name]
    selector='core/testOnly '+' '.join(selected)
    retained=lane.root/'candidate-causal-junit'
    output=run_with_causal_snapshot(lane,reports,retained,limits['suite_seconds'],selector)
    exit_code=lane.records[-1]['exit']
    paths=sorted(retained.glob('*.xml'))
    actual=package_reports(paths,output)
    require(set(actual['suites'])==set(selected),'Causal group suite report set mismatch')
    actual.update(selector=selector,group=lane.name,requiredGroupSuites=selected,exit=exit_code,
                  sourcePin=config['source_commit'],candidateFiles=config['candidate_files'],
                  events=classify_log(output),baselineAttribution={},
                  state='CAUSAL_RESULTS_RETAINED_ATTRIBUTION_OR_REVIEW_PENDING',fullProductValidation=False)
    # The DML group retains both exact source-backed ID sets; other groups contain full non-DML suites.
    for suite in [config['lanes']['candidate-legacy']['suite'],config['lanes']['candidate-plural']['suite']]:
        if suite in selected:
            require(suite in actual['suites'],'Missing DML suite inside causal group')
            require({row['name']for row in actual['suites'][suite]}==set(config['suite_cases'][suite]),
                    'Causal group DML IDs mismatch')
    target=lane.root/'candidate-causal-result.json'
    try:
        failures=[suite for suite in actual['failures'] if suite not in
                  [config['lanes']['candidate-legacy']['suite'],config['lanes']['candidate-plural']['suite']]]
        if failures:
            for suite in failures:actual['baselineAttribution'][suite]={'state':'UNRUN_SAME_CLOCK_REQUIRED'}
            pristine=lane.root/'pristine-non-dml-attribution'
            require(not pristine.exists(),'Pristine attribution collision')
            needed=limits['attributionCompileSeconds']+len(failures)*limits['attributionSuiteSeconds']+240
            clock_admission(time.time(),lane.deadline,needed)
            shutil.copytree('/source',pristine,symlinks=True)
            require(lane.command('attribution-source-pin',['git','rev-parse','HEAD'],10,cwd=pristine).strip()==
                    config['source_commit'],'Pristine attribution source pin mismatch')
            require(not lane.command('attribution-source-clean',['git','status','--porcelain'],10,
                                     cwd=pristine).strip(),'Pristine attribution dirty')
            def baseline_sbt(label,tasks,seconds,expected=(0,)):
                # Same Lab retains datasets_home and the root-owned datasetDir setting.
                # Commands are sequential; always restore the candidate source before any later checks.
                candidate_source=lane.source
                try:
                    lane.source=pristine
                    return lane.sbt(label,tasks,seconds,expected)
                finally:
                    lane.source=candidate_source
            baseline_sbt('attribution-pristine-compile',['core/compile','core/Test/compile'],
                         limits['attributionCompileSeconds'])
            for index,suite in enumerate(sorted(failures)):
                require(suite.startswith('com.microsoft.azure.synapse.ml.causal.'),'Unsafe attribution suite')
                clock_admission(time.time(),lane.deadline,limits['attributionSuiteSeconds']+240)
                label='attribution-non-dml-'+str(index)
                raw=baseline_sbt(label,['core/testOnly '+suite],limits['attributionSuiteSeconds'],expected=(0,1))
                found=[path for path in (pristine/'core/target/test-reports').glob('*.xml')
                       if ET.parse(path).getroot().get('name')==suite]
                require(len(found)==1,'Missing/duplicate pristine attribution report')
                copied=lane.root/(label+'.xml');shutil.copy2(found[0],copied)
                doc=ET.parse(copied).getroot()
                actual['baselineAttribution'][suite]={'state':'BASELINE_SUITE_EXECUTED_NO_AUTOMATIC_WAIVER',
                    'source':config['source_commit'],
                    'candidateCaseIDs':[row['name']for row in actual['suites'][suite]],
                    'baselineCaseIDs':[v.get('name')for v in doc.iter('testcase')],
                    'baselineFailureCount':len(list(doc.iter('failure')))+len(list(doc.iter('error'))),
                    'baselineSkipCount':len(list(doc.iter('skipped'))),'rawLog':label+'.log',
                    'events':classify_log(raw),'automaticWaiver':False}
        actual['state']='CAUSAL_GROUP_COMPLETED_FULL_UNION_REVIEW_STILL_REQUIRED'
        require_package_success(actual,exit_code)
        return actual
    finally:
        target.write_text(json.dumps(actual,indent=2))


def require_package_success(actual,exit_code):
    require(exit_code==0 and not actual['failures'] and not actual['skips'] and actual['abortedSuites']==0,
            'Causal candidate failures/skips/aborts require results review; no automatic waiver')


def reconcile_causal_groups(bundles,config):
    """Post-collection data reader; never turns a missing/failed group into a waiver."""
    validate_causal_groups(config)
    require(set(bundles)==set(config['causal_groups']),'Missing or extra causal group evidence')
    all_ids=[]
    for name,bundle in bundles.items():
        lane=bundle['laneResult'];result=bundle['result'];post=bundle['postSource']
        discovery=bundle['discovery']
        require(lane['lane']==name and lane['passed'] is True and
                lane['full_validation_passed'] is True,'Causal group lane not complete')
        require(post['passed'] is True and post['candidate']==config['candidate_files'] and
                post['source_pin']==config['source_commit'],'Causal post-source proof mismatch')
        require(discovery['complete'] is True and discovery['count']==15 and
                discovery['suiteIDs']==sorted(config['causal_suite_inventory']),
                'Causal complete discovery proof mismatch')
        require(result['group']==name and set(result['suites'])==set(config['causal_groups'][name]) and
                result['candidateFiles']==config['candidate_files'] and
                result['sourcePin']==config['source_commit'],'Causal group identity mismatch')
        require_package_success(result,result['exit'])
        for suite,rows in result['suites'].items():
            if suite in config['suite_cases']:
                require({row['name']for row in rows}==set(config['suite_cases'][suite]),
                        'Causal collected DML expected IDs mismatch')
        from_rows=[(suite,row['name'])for suite,rows in result['suites'].items()for row in rows]
        require(from_rows==[tuple(value)for value in result['caseIDs']],
                'Causal case ledger/rows mismatch')
        require(len(from_rows)==len(set(from_rows)),'Duplicate causal case within group')
        all_ids.extend(from_rows)
    require(len(all_ids)==len(set(all_ids)),'Duplicate causal case across groups')
    require({suite for suite,_ in all_ids}==set(config['causal_suite_inventory']),
            'Causal collected suite union is incomplete')
    return {'state':'ALL_FIVE_CAUSAL_GROUPS_RECONCILED_REQUIRES_WHOLE_RESULTS_REVIEW',
            'suiteIDs':sorted(config['causal_suite_inventory']),'caseIDs':all_ids,
            'caseCount':len(all_ids),'suiteCount':15,'fullProductValidation':False,
            'qualifiedAsGroupedCoverageNotMonolithicRun':True}
