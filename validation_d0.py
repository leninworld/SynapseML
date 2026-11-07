"""Tiny real shared-path fixtures before source copy/download/build; no project tests."""
from pathlib import Path
import argparse,base64,hashlib,io,json,os,subprocess,sys,tarfile
from notebook_contract import load_book,same_semantics,decode_transport
from validation_runtime import Runtime,docker_only,extract_archive,require


def rejected(fn,token):
    try:fn()
    except (RuntimeError,ValueError,AssertionError) as error:
        require(token in str(error),"Wrong negative-fixture failure cause: "+str(error));return
    raise RuntimeError('Negative fixture unexpectedly accepted')


def main():
    docker_only();parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--original-deadline-epoch',type=float,required=True);args=parser.parse_args()
    require(not args.root.exists(),'D0 no replay');args.root.mkdir();rt=Runtime(args.original_deadline_epoch,args.root)
    probe=args.root/'write-read-probe';probe.write_bytes(b'fixture');require(probe.read_bytes()==b'fixture','UID writable path')
    rt.run('node-path',['node','--version'],args.root,10)
    actual_node=(args.root/'node-path.log').read_text().strip();require(actual_node.startswith('v') and int(actual_node[1:].split('.')[0])>=24,'Node >=24 required by actual website/package.json')
    rt.run('npm-path',['npm','--version'],args.root,10)
    book={'cells':[{'id':'tiny-code','cell_type':'code','source':['x = 1 + 2\n'],'metadata':{},'outputs':[],'execution_count':None}],'metadata':{},'nbformat':4,'nbformat_minor':5};raw=json.dumps(book).encode();load_book(raw)
    formatted=json.loads(raw);formatted['cells'][0]['source']=['x=1+2\n'];out=json.dumps(formatted).encode();require(same_semantics(raw,out)==1,'Shared notebook format comparison')
    formatted['cells'][0]['source']=['x=1+3\n'];rejected(lambda:same_semantics(raw,json.dumps(formatted).encode()),"Formatter changed Python semantics")
    require(decode_transport(base64.b64encode(raw).decode(),hashlib.sha256(raw).hexdigest())==raw,'Job-output byte transport')
    rejected(lambda:decode_transport(base64.b64encode(raw).decode(),'0'*64),'Notebook transport identity')
    bad=json.loads(raw);bad['cells'][0]['outputs']=[{'output_type':'stream','name':'stdout','text':'stale'}];rejected(lambda:load_book(json.dumps(bad).encode()),"Authored notebook must be cleared")
    archive=args.root/'tiny.tar'
    with tarfile.open(archive,'w') as t:
        member=tarfile.TarInfo('parent/a.txt');member.size=2;t.addfile(member,io.BytesIO(b'ok'))
    records=extract_archive(archive,args.root/'extracted',100);require(records[0]['sha256']==hashlib.sha256(b'ok').hexdigest(),'Real archive identity')
    with tarfile.open(args.root/'bad.tar','w') as t:
        member=tarfile.TarInfo('../escape');member.size=1;t.addfile(member,io.BytesIO(b'x'))
    rejected(lambda:extract_archive(args.root/'bad.tar',args.root/'bad-extract',100),'Unsafe archive component')
    rt.run('positive-process',[sys.executable,'-c','print("fixture")'],args.root,10)
    rejected(lambda:rt.run('wrong-exit',[sys.executable,'-c','raise SystemExit(3)'],args.root,10),'Unexpected exit 3')
    rejected(lambda:rt.run('owned-timeout',[sys.executable,'-c','import time;time.sleep(30)'],args.root,.3),'Step deadline')
    # Actual source-relative Black path observer, using synthetic output only.
    from format_checks import azure_black
    class BlackFixture:
        source=args.root/'format-source';root=args.root;manifest={'candidate_files':{}}
        def __init__(self,output):self.output=output;self.records=[{'exit':1}]
        def command(self,*a,**k):return self.output
    BlackFixture.source.mkdir();result=azure_black(BlackFixture('would reformat example.py\n'),'baseline');require(result['failures']==['example.py'],'Relative Black path interpreted against source')
    rejected(lambda:azure_black(BlackFixture('would reformat ../outside.py\n'),'baseline'),'Black path outside source')
    # Same priority exporter and reassembler used for actual retained results.
    from evidence_export import export_evidence
    from collect import reassemble
    emitted=[];receipt=export_evidence(args.root,[probe],args.original_deadline_epoch,emit=lambda text,**k:emitted.append(text))
    require(receipt['complete'],'Tiny export failed');decoded=reassemble([json.loads(x.split('EVIDENCE1669=',1)[1]) for x in emitted]);require(decoded['write-read-probe']==b'fixture','Actual exporter/collector bytes')
    from website_contract import fixture
    fixture();rt.guard()
    (args.root/'D0-result.json').write_text(json.dumps({'status':'TINY_FIXTURES_PASS','notebook_collection_and_format_identity':True,'joboutput_transport':True,'real_archive':True,'real_process_and_timeout':True,'source_relative_Black_observer':True,'actual_export_collect':True,'kernel_fixture':'after fullenv/before expensive projectbuild','project_tests_run':False}))
if __name__=='__main__':main()
