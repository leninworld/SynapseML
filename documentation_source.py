"""Freeze the exact three-file source, allowing only reviewed notebook formatting."""
from pathlib import Path
import hashlib,json,os,subprocess
from notebook_contract import same_semantics,decode_transport
from validation_runtime import require


def freeze(lane):
    names=lane.manifest['candidate_files'];hashes={n:hashlib.sha256((lane.source/n).read_bytes()).hexdigest() for n in names}
    tracked=subprocess.check_output(['git','diff','--binary','--abbrev=7'],cwd=lane.source)
    new=subprocess.run(['git','diff','--no-index','--binary','--abbrev=7','--','/dev/null',lane.manifest['notebook_path']],cwd=lane.source,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    require(new.returncode==1 and not new.stderr,'New notebook patch construction')
    patch=tracked+new.stdout
    (lane.root/'tested-documentation.patch').write_bytes(patch)
    result={'source_sha256':hashes,'tested_patch_sha256':hashlib.sha256(patch).hexdigest(),'input_patch_sha256':lane.manifest['patch_sha256']}
    (lane.root/'frozen-documentation.json').write_text(json.dumps(result,indent=2));return result


def author_format(lane):
    path=lane.source/lane.manifest['notebook_path'];old=path.read_bytes()
    lane.command('author-notebook-format',['python','-m','black','--quiet',str(path)],120)
    new=path.read_bytes();count=same_semantics(old,new)
    lane.manifest['candidate_files'][lane.manifest['notebook_path']]=hashlib.sha256(new).hexdigest()
    lane.verify_candidate('formatted')
    (lane.root/'author-format.json').write_text(json.dumps({'original_sha256':hashlib.sha256(old).hexdigest(),'formatted_sha256':hashlib.sha256(new).hexdigest(),'same_AST_markdown_metadata_cleared_outputs':True,'code_cells':count},indent=2))
    return freeze(lane)


def import_formatted(lane):
    path=lane.source/lane.manifest['notebook_path'];original=path.read_bytes()
    raw=decode_transport(os.environ.get('FORMATTED_NOTEBOOK_B64',''),os.environ.get('FORMATTED_NOTEBOOK_SHA256',''))
    same_semantics(original,raw);path.write_bytes(raw)
    lane.manifest['candidate_files'][lane.manifest['notebook_path']]=hashlib.sha256(raw).hexdigest();lane.verify_candidate('formatted')
    result=freeze(lane);require(result['tested_patch_sha256']==os.environ['FORMATTED_PATCH_SHA256'],'Website/native tested patch identity differs')
    return result
