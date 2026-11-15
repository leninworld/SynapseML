"""Unmodified baseline measurement, not a candidate or full-feature validation."""
import hashlib
import json
import shutil
import threading
import time
import xml.etree.ElementTree as ET

from validation_runtime import require
from junit_reader import verified_report
from g0_contract import clock_admission

SUITE = 'com.microsoft.azure.synapse.ml.causal.VerifyDoubleMLEstimator'
CASES = [
    'Get treatment effects',
    'Get treatment effects, use integer as binary treatment',
    'Get treatment effects, use integer as binary treatment but it has invalid values, will throw exception.',
    'Get treatment effects with weight column',
    'Get confidence intervals with multiple iterations',
    'Mismatch treatment model and treatment column will throw exception.',
    'Mismatch outcome model and outcome column will throw exception.',
    'Experiment Fuzzing', 'Serialization Fuzzing', 'Getters and Setters work as anticipated',
]


def baseline_unchanged(lane, label):
    for name, expected in lane.manifest['baseline_files'].items():
        require(hashlib.sha256((lane.source/name).read_bytes()).hexdigest() == expected,
                'Baseline source changed: '+name)
    diff = lane.command(label+'-source-clean', ['git','status','--porcelain','--untracked-files=no'], 10)
    require(not diff.strip(), 'Tracked baseline source changed')


def validate_report(path):
    result = verified_report(path, SUITE, CASES, ())
    document = ET.parse(path).getroot()
    result['test_seconds'] = {case.get('name'):float(case.get('time','0'))
                              for case in document.iter('testcase')}
    require(all(value >= 0 for value in result['test_seconds'].values()), 'Invalid JUnit time')
    return result


def observed_event(line):
    # Observation timestamps are receipt times, not a claim of exact internal Spark start times.
    return any(token in line for token in
               ['Executing ATE calculation', 'Completed ATE calculation', 'ATE calculation got exception',
                'retry', 'Retry', 'RETRY', *CASES])


def scala_measurement(lane):
    compile_cap=min(480,lane.deadline-time.time()-2040)
    require(compile_cap>0,'HOLD no compile capacity before full-suite reserve')
    settings=lane.sbt('native-core-compile', ['show core / Test / fork',
                     'show core / Test / javaOptions','show ThisBuild / javaOptions',
                     'core/compile', 'core/Test/compile'], compile_cap)
    (lane.root/'actual-native-fork-heap-settings.txt').write_text(settings)
    baseline_unchanged(lane,'pre-suite')
    remaining = lane.deadline-time.time()
    receipt = {'remaining_seconds':remaining, 'required_seconds':2040,
               'full_suite_cap_seconds':1800,'evidence_reserve_seconds':240,'stop_reaper_reserve_within_evidence_seconds':120}
    (lane.root/'pre-suite-clock-admission.json').write_text(json.dumps(receipt,indent=2))
    require(remaining >= 2040, 'HOLD full unfiltered suite cannot fit original clock with reserves')
    reports = lane.source/'core/target/test-reports'
    require(not list(reports.glob('*.xml')), 'Unexpected pre-existing baseline JUnit reports')
    logfile = lane.root/'native-full-VerifyDML.log'
    eventfile = lane.root/'native-observed-events.jsonl'
    stop = threading.Event()
    errors = []

    def observe():
        cursor = 0
        pending = b''
        written = 0
        try:
            with eventfile.open('x') as output:
                while not stop.is_set() or (logfile.exists() and logfile.stat().st_size > cursor):
                    if logfile.exists():
                        with logfile.open('rb') as source:
                            source.seek(cursor)
                            chunk = source.read(65536)
                        cursor += len(chunk)
                        pending += chunk
                        while b'\n' in pending:
                            raw,pending = pending.split(b'\n',1)
                            text = raw.decode(errors='replace')
                            if observed_event(text):
                                record = json.dumps({'observed_at_epoch':time.time(),'raw_line':text})+'\n'
                                written += len(record.encode())
                                require(written <= 4*1024**2,'Iteration observer output cap')
                                output.write(record);output.flush()
                        require(len(pending) <= 1024**2,'Unterminated observer line cap')
                    stop.wait(.1)
        except BaseException as error:
            errors.append(str(error))

    observer = threading.Thread(target=observe,daemon=True)
    observer.start()
    try:
        lane.sbt('native-full-VerifyDML', ['core/testOnly '+SUITE], 1800)
    finally:
        stop.set();observer.join(timeout=5)
        require(not observer.is_alive() and not errors,'Incomplete native event observation')
    matches = [path for path in reports.glob('*.xml') if ET.parse(path).getroot().get('name') == SUITE]
    require(len(matches)==1,'Missing/duplicate full VerifyDML report')
    target = lane.root/'native-full-VerifyDML.xml'
    shutil.copyfile(matches[0],target)
    result = validate_report(target)
    events=[json.loads(line) for line in eventfile.read_text().splitlines()]
    failures=[event for event in events if 'ATE calculation got exception' in event['raw_line']]
    retries=[event for event in events if 'retry' in event['raw_line'].lower()]
    result.update(failed_draw_warning_count=len(failures),retry_log_count=len(retries),
                  native_confidence_bootstraps=30,source_rows=35,parallelism=10,
                  all_confidence_draws_succeeded='NOT_ESTABLISHED_FROM_AGGREGATE_LOG',
                  native_retry_status='OBSERVED_RETRY_LOGS' if retries else 'NO_RETRY_LINE_OBSERVED_NOT_PROOF_OF_NO_RETRY',
                  warning_free_native_suite=not failures and not retries,
                  observation_timestamp_kind='log-consumption time; raw starts/completions retained')
    (lane.root/'native-full-VerifyDML-selection.json').write_text(json.dumps(result,indent=2))


def validate(lane):
    lane.command('native-architecture',['uname','-m'],10)
    require((lane.root/'native-architecture.log').read_text().strip()=='x86_64','No emulated baseline')
    java=lane.command('toolchain-java',['java','-version'],20)
    require('11.0.' in java,'Pinned master JDK11 required')
    require(__import__('os').environ['JAVA_TOOL_OPTIONS']=='-XX:ActiveProcessorCount=3',
            'Unexpected Java CPU visibility')
    if lane.name=='native-scala':
        scala_measurement(lane)
    elif lane.name=='installed-python':
        from format_checks import azure_black
        from generated_artifacts import artifact_manifest
        azure_black(lane,'baseline',exclude_docs=True)
        settings=lane.sbt('root-compile-testcompile-styles',
                          ['show core / Test / fork','show core / Test / javaOptions',
                           'show ThisBuild / javaOptions','compile','Test / compile',
                           'scalastyle','Test / scalastyle'],600)
        (lane.root/'actual-native-fork-heap-settings.txt').write_text(settings)
        clock_admission(time.time(),lane.deadline,1440)
        artifact=artifact_manifest(lane)
        clock_admission(time.time(),lane.deadline,540)
        lane.command('real-installed-singular-DML',['python',str(lane.packet/'installed_guard.py'),str(artifact)],300,
                     cwd=lane.packet)
    else:
        require(False,'Unknown G0 lane')
    baseline_unchanged(lane,'final')
    (lane.root/'baseline-identity-final.json').write_text(json.dumps({
        'source_commit':lane.manifest['source_commit'],'baseline_hashes':lane.manifest['baseline_files'],
        'candidate_applied':False,'full_feature_validation':False},indent=2))
