"""Actual Git and changed Lab source-role guards, inside approved Docker only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from common import Lab
from validation_runtime import docker_only, require


def main(root):
    docker_only();root.mkdir()
    source=root/'source';packet=root/'packet';source.mkdir();packet.mkdir()
    def git(*args):
        return subprocess.run(['git','-C',str(source),*args],capture_output=True,check=True).stdout
    for name in ['a.py','b.py','policy.md']:(source/name).write_text('original\n')
    git('init','--quiet');git('add','a.py','b.py','policy.md')
    git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Synthetic source fixture')
    for name in ['a.py','b.py']:(source/name).write_text('candidate\n')
    patch=git('diff','HEAD');(packet/'candidate.patch').write_bytes(patch)
    for name in ['a.py','b.py']:(source/name).write_text('original\n')
    base={name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['a.py','b.py','policy.md']}
    base['nested/probe.py']=None;candidate={}
    for name in ['a.py','b.py','nested/probe.py']:
        target=packet/'candidate-files'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('candidate\n');candidate[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    manifest={'baseline_files':base,'candidate_files':candidate,'patch_sha256':hashlib.sha256(patch).hexdigest()}
    lane=Lab(packet,root,manifest,float(os.environ['VALIDATION_DEADLINE_EPOCH']))
    try:
        lane.inject(['b.py','nested/probe.py']);lane.apply();lane.verify_candidate('positive-role-check')
        (source/'policy.md').write_text('unreviewed\n')
        try:lane.verify_candidate('negative-role-check')
        except RuntimeError as error:require(str(error)=='Unexpected source delta after candidate application','Wrong role guard failure')
        else:raise RuntimeError('Unrelated source change accepted')
        (source/'policy.md').write_text('original\n');(source/'a.py').write_text('wrong bytes\n')
        try:lane.verify_candidate('negative-content-check')
        except RuntimeError as error:require(str(error)=='Candidate file changed: a.py','Wrong content guard failure')
        else:raise RuntimeError('Bad candidate bytes accepted')
        (root/'fixture-result.json').write_text(json.dumps({'status':'PASS','actual_Git_apply':True,'tracked_injection':True,'nested_new_file':True,'policy_anchor_not_changed':True,'unrelated_delta_and_content_rejected':True}))
    finally:lane.runtime.memory.stop()


if __name__=='__main__':main(Path(sys.argv[1]))
