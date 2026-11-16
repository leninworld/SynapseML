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

REGISTRATION='com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_report(path,suite,wanted):
    result=verified_report(path,suite,wanted,())
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
    result=checked_report(target,suite,config['suite_cases'][suite])
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
    after_compile=limits['suite_seconds']+limits['registration_seconds']+limits.get('fuzzing_seconds',0)+\
        limits['reserve_seconds']
    compile_cap=min(limits['compile_max_seconds'],lane.deadline-time.time()-after_compile)
    require(compile_cap>0,'HOLD complete native acceptance cannot fit original clock')
    # Compile catches actual imports/API/codegen compile dependencies before expensive fits.
    compile_tasks=['core/scalastyle','core/Test/scalastyle']
    compile_tasks+=['compile','Test / compile'] if lane.name=='candidate-legacy' else \
        ['core/compile','core/Test/compile']
    lane.sbt('candidate-native-lint-import-compile',compile_tasks,compile_cap)
    clock_admission(time.time(),lane.deadline,after_compile)
    if lane.name=='candidate-causal':
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


def causal_package(lane,config,limits):
    reports=lane.source/'core/target/test-reports'
    prior=lane.root/'candidate-causal-prior-junit';prior.mkdir()
    for path in reports.glob('*.xml'):
        shutil.copy2(path,prior/path.name);path.unlink()
    output=lane.sbt('candidate-entire-causal-package',
                    ['core/testOnly com.microsoft.azure.synapse.ml.causal.**'],limits['suite_seconds'],expected=(0,1))
    exit_code=lane.records[-1]['exit']
    paths=sorted(reports.glob('*.xml'))
    retained=lane.root/'candidate-causal-junit';retained.mkdir()
    for path in paths:shutil.copy2(path,retained/path.name)
    actual=package_reports(paths,output)
    actual.update(selector='core/testOnly com.microsoft.azure.synapse.ml.causal.**',exit=exit_code,
                  events=classify_log(output),baselineAttribution={},
                  state='CAUSAL_RESULTS_RETAINED_ATTRIBUTION_OR_REVIEW_PENDING',fullProductValidation=False)
    # Both exact DML suites must be present inside full CI as well; separate34 are not this job.
    for suite in [config['lanes']['candidate-legacy']['suite'],config['lanes']['candidate-plural']['suite']]:
        require(suite in actual['suites'],'Missing DML suite inside full causal CI')
        require({row['name']for row in actual['suites'][suite]}==set(config['suite_cases'][suite]),
                'Full causal CI DML IDs mismatch')
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
        actual['state']='CAUSAL_PACKAGE_COMPLETED_ROOT_RESULTS_REVIEW_REQUIRED'
        require_package_success(actual,exit_code)
        return actual
    finally:
        target.write_text(json.dumps(actual,indent=2))


def require_package_success(actual,exit_code):
    require(exit_code==0 and not actual['failures'] and not actual['skips'] and actual['abortedSuites']==0,
            'Causal candidate failures/skips/aborts require results review; no automatic waiver')
