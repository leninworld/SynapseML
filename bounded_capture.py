"""Bounded stdlib process capture; caller supplies its actual admission guard and original deadline."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time


def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(data):return hashlib.sha256(data).hexdigest()

def save(path,data):
    assert not any(p.is_symlink() for p in (path,*path.parents)),'SYMLINK_PATH'
    assert not path.exists(),'RECEIPT_EXISTS'
    raw=(json.dumps(data,indent=2)+'\n').encode()
    assert len(raw)<=131072,'RECEIPT_CAP'
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('xb') as stream:
        os.chmod(temporary,0o600);stream.write(raw)
    os.replace(temporary,path)


class Capture:
    def __init__(self,output,deadline,quota_guard):
        self.output=Path(output);self.end=deadline;self.quota_guard=quota_guard
        self.calls=0;self.received=0;self.records=[]

    def capture(self,op,argv):
        self.quota_guard()
        assert time.monotonic()<self.end,'ORIGINAL_DEADLINE'
        assert self.calls<16,'OPERATION_CAP'
        assert re.fullmatch('[a-z0-9-]+',op['id']),'BAD_OPERATION_ID'
        seconds=min(20,op.get('secondsIncludingReap',20),self.end-time.monotonic())
        assert seconds>4,'NO_CLEANUP_RESERVE'
        stdout_cap=op.get('maxStdoutBytes',op.get('maxCombinedBytes'))
        stderr_cap=op.get('maxStderrBytes',op.get('maxCombinedBytes'))
        combined=min(op.get('maxCombinedBytes',stdout_cap+stderr_cap),6291456-self.received)
        assert min(stdout_cap,stderr_cap,combined)>0,'BYTE_BUDGET_EXHAUSTED'
        base=self.output/op['id']
        save(base.with_suffix('.started.json'),{'at':utc(),'argv':argv,'operation':self.calls+1,
             'secondsIncludingReap':seconds,'stdoutCap':stdout_cap,'stderrCap':stderr_cap,
             'combinedCap':combined})
        self.calls+=1;begin=time.monotonic();deadline=begin+seconds
        proc=None;sel=selectors.DefaultSelector();counts={'stdout':0,'stderr':0}
        retained={'stdout':bytearray(),'stderr':bytearray()};error=None
        term_requested=False;kill_requested=False
        try:
            proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,start_new_session=True)
            sel.register(proc.stdout,selectors.EVENT_READ,'stdout')
            sel.register(proc.stderr,selectors.EVENT_READ,'stderr')
            while sel.get_map():
                left=deadline-4-time.monotonic()
                if left<=0:raise TimeoutError('OPERATION_WORK_DEADLINE')
                for key,_ in sel.select(min(.1,left)):
                    kind=key.data;cap=stdout_cap if kind=='stdout' else stderr_cap
                    available=min(cap-counts[kind],combined-sum(counts.values()))
                    chunk=key.fileobj.read1(min(65536,max(0,available)+1))
                    if not chunk:sel.unregister(key.fileobj);continue
                    counts[kind]+=len(chunk);retained[kind].extend(chunk[:max(0,available)])
                    if len(chunk)>available:raise ValueError('PIPE_CAP_OVERFLOW_SENTINEL')
            proc.wait(timeout=max(.001,deadline-4-time.monotonic()))
        except BaseException as exc:
            error=type(exc).__name__+':'+str(exc)[:256]
        finally:
            if proc is not None:
                # Unconditional group stop: the leader may already have exited while a child holds pipes.
                try:os.killpg(proc.pid,signal.SIGTERM);term_requested=True
                except ProcessLookupError:pass
                if proc.poll() is None:
                    try:proc.wait(timeout=max(.001,min(2,deadline-time.monotonic()-1)))
                    except subprocess.TimeoutExpired:pass
                try:os.killpg(proc.pid,signal.SIGKILL);kill_requested=True
                except ProcessLookupError:pass
                if proc.poll() is None:
                    try:proc.wait(timeout=max(.001,deadline-time.monotonic()))
                    except subprocess.TimeoutExpired:error=(error or '')+';REAP_DEADLINE'
            sel.close()
            for stream in (getattr(proc,'stdout',None),getattr(proc,'stderr',None)):
                if stream is not None:stream.close()
            self.received+=sum(counts.values())
            for kind,data in retained.items():
                target=base.with_suffix('.'+kind)
                assert not target.is_symlink() and not target.exists()
                with target.open('xb') as stream:os.chmod(target,0o600);stream.write(data)
            record={'at':utc(),'id':op['id'],'argv':argv,'seconds':time.monotonic()-begin,
                    'exit':proc.returncode if proc else None,'error':error,'received':counts,
                    'retained':{k:len(v) for k,v in retained.items()},
                    'rawSHA256':{k:sha(v) for k,v in retained.items()},
                    'leader_terminal':proc is not None and proc.poll() is not None,
                    'owned_group_TERM_requested':term_requested,'owned_group_KILL_requested':kill_requested,
                    'descendant_reaping':'NOT_CLAIMED_BY_LEADER_STATUS',
                    'callsConsumed':self.calls,'allReceivedBytes':self.received,'state':'CLOSED_NO_RETRY'}
            self.records.append(record);save(base.with_suffix('.receipt.json'),record)
        assert error is None and proc is not None and proc.poll() is not None,'TRANSPORT_CAP_OR_DEADLINE_HOLD'
        return proc.returncode,bytes(retained['stdout']),bytes(retained['stderr'])


def controls(root,deadline):
    """Real owned subprocess controls; called only inside the approved Docker D0."""
    assert not root.exists();root.mkdir()
    capture=Capture(root,deadline,lambda:None)
    def op(name,cap=65536,seconds=20):
        return {'id':name,'maxStdoutBytes':cap,'maxStderrBytes':cap,
                'maxCombinedBytes':cap,'secondsIncludingReap':seconds}
    code,out,_=capture.capture(op('positive'),[sys.executable,'-c',"print('fixture')"])
    assert code==0 and out==b'fixture\n'
    code,_,_=capture.capture(op('nonzero'),[sys.executable,'-c','raise SystemExit(3)'])
    assert code==3
    child_observations=[]
    for label,script,cap in [('timeout','import time;time.sleep(30)',65536),
                             ('overflow',"print('x'*8192)",1024),
                             ('exited-leader-child',
                              'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c",'
                              '"import time;time.sleep(30)"]);print(p.pid,flush=True)',65536)]:
        try:capture.capture(op(label,cap,4.5),[sys.executable,'-c',script])
        except AssertionError:pass
        else:raise RuntimeError('Capture negative accepted: '+label)
        assert capture.records[-1]['leader_terminal']
        if label=='exited-leader-child':
            pid=int((root/(label+'.stdout')).read_text().strip())
            stat=Path('/proc')/str(pid)/'stat'
            poll_end=min(deadline,time.monotonic()+1)
            while True:
                try:child_state=stat.read_text().split(') ',1)[1][0]
                except FileNotFoundError:child_state='ABSENT'
                child_observations.append({'at':utc(),'state':child_state})
                if child_state in ('ABSENT','Z','X'):break
                assert time.monotonic()<poll_end,'Owned child still running after group cleanup'
                time.sleep(min(.01,max(0,poll_end-time.monotonic())))
    result={'state':'REAL_CAPTURE_CONTROLS_COMPLETE','caseCount':5,
            'cases':['positive','nonzero','timeout','overflow','exited-leader-child'],
            'projectTestsRun':False,'samePrimitiveAsAdministrativePublisher':True,'childTerminalObservations':child_observations}
    save(root/'capture-controls.json',result)
    return result
