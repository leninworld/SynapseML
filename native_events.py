"""Source-backed Spark numerical fallback versus TestBase/Flaky test-rerun classification."""
import hashlib
import json
import re

from validation_runtime import require

SOLVER=re.compile(r'^(?:\[info\] )?\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} '
                  r'WARN Instrumentation: \[[0-9a-f]+\] Cholesky solver failed due to '
                  r'singular covariance matrix\. Retrying with Quasi-Newton solver\.$')
RERUN=re.compile(r'^(?:\[info\] )?RETRYING after [0-9]+ ms:  Caught error: .+$')
G0_SOLVER='26/11/15 23:27:47 WARN Instrumentation: [b6de1509] Cholesky solver failed due to ' \
          'singular covariance matrix. Retrying with Quasi-Newton solver.'


def classify_log(text):
    result={'solverFallbacks':[],'testReruns':[],'unclassifiedRetryLines':[],'failedDrawWarnings':[]}
    for number,line in enumerate(text.splitlines(),1):
        value=line.strip();row={'line':number,'text':line}
        if SOLVER.fullmatch(value):result['solverFallbacks'].append(row)
        elif RERUN.fullmatch(value):result['testReruns'].append(row)
        elif 'retry' in value.lower():result['unclassifiedRetryLines'].append(row)
        if 'ATE calculation got exception' in value:result['failedDrawWarnings'].append(row)
    return result


def summarize(lane):
    """All actual Runtime operation logs, including failed operations absent from lane.records.

    Root may call this once in finally for EVERY native/installed/docs job. No tail/double counting.
    """
    result={'state':'ACTUAL_JOB_EVENT_CLASSIFICATION_COMPLETE','lane':lane.name,'logs':[],
            'solverFallbackCount':0,'testRerunCount':0,'unclassifiedRetryLineCount':0,
            'failedDrawWarningCount':0,'allThirtyDrawsSucceeded':'UNESTABLISHED'}
    for receipt in sorted(lane.root.glob('*.json')):
        try:record=json.loads(receipt.read_text())
        except (ValueError,OSError):continue
        if not isinstance(record,dict) or not {'argv','exit','bytes','leader_terminal'}<=set(record):continue
        path=receipt.with_suffix('.log')
        if not path.is_file() or path.is_symlink():
            result['state']='EVENT_CLASSIFICATION_INCOMPLETE';continue
        raw=path.read_bytes();classified=classify_log(raw.decode(errors='replace'))
        result['logs'].append({'path':path.name,'bytes':len(raw),
                              'sha256':hashlib.sha256(raw).hexdigest(),**classified})
        for count,field in [('solverFallbackCount','solverFallbacks'),('testRerunCount','testReruns'),
                            ('unclassifiedRetryLineCount','unclassifiedRetryLines'),
                            ('failedDrawWarningCount','failedDrawWarnings')]:
            result[count]+=len(classified[field])
    target=lane.root/'native-event-summary.json';temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,indent=2));temporary.replace(target)
    return result


def controls(root):
    require(not root.exists(),'Event classifier fixture replay');root.mkdir()
    cases=[('real-G0-solver',G0_SOLVER,'solverFallbacks'),
           ('source-backed-Flaky','[info] RETRYING after 1000 ms:  Caught error: java.lang.Exception: fixture',
            'testReruns'),
           ('solver-is-not-rerun',G0_SOLVER,'solverFallbacks'),
           ('unrelated-network-retry','Retrying a network connection','unclassifiedRetryLines')]
    for name,line,expected in cases:
        actual=classify_log(line)
        require(len(actual[expected])==1 and sum(len(actual[k])for k in
                ['solverFallbacks','testReruns','unclassifiedRetryLines'])==1,'Classifier mismatch: '+name)
    require(not classify_log('at TestBase.tryWithRetries(TestBase.scala:206)')['testReruns'],
            'Stack frame is not a test rerun')
    require(not classify_log('[info] Tests: succeeded 10, failed 0')['testReruns'],
            'ScalaTest summary is not a rerun')
    result={'state':'EXACT_EVENT_CLASSIFIER_CONTROLS_COMPLETE','cases':6,
            'realG0SolverLine':G0_SOLVER,'rerunSource':'adopted TestBase.tryWithRetries println',
            'actualProjectFitsRun':False}
    (root/'native-events-controls.json').write_text(json.dumps(result,indent=2))
    return result
