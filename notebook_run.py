"""Execute every original cell in a real kernel; append only a private provenance observation."""
from pathlib import Path
import hashlib,json,os,sys
from notebook_contract import load_book
from validation_runtime import docker_only,require


def configure_kernel(root):
    folder=root/'jupyter-data/kernels/synapse1669';folder.mkdir(parents=True,exist_ok=True)
    (folder/'kernel.json').write_text(json.dumps({'argv':[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}'],'display_name':'Owned SynapseML validation','language':'python'}))
    os.environ['JUPYTER_PATH']=str(root/'jupyter-data')
    os.environ['JUPYTER_RUNTIME_DIR']=str(root/'tmp/jupyter-runtime')
    Path(os.environ['JUPYTER_RUNTIME_DIR']).mkdir(parents=True,exist_ok=True)


def execute(raw,destination,root,probe=None):
    docker_only();source,code=load_book(raw)
    (root/'tmp/nb-cwd').mkdir(parents=True,exist_ok=True)
    import nbformat
    from nbclient import NotebookClient
    book=nbformat.reads(raw.decode("utf-8"),as_version=4)
    if probe is not None:
        book.cells.append(nbformat.v4.new_code_cell(probe,id='private-provenance'))
    client=NotebookClient(book,timeout=300,startup_timeout=60,kernel_name='synapse1669',allow_errors=False,resources={'metadata':{'path':str(root/'tmp/nb-cwd')}})
    try:client.execute()
    finally:nbformat.write(book,destination)
    original=book.cells[:len(source['cells'])]
    require([c['source'] for c in original]==[''.join(c['source']) for c in source['cells']],'Executed original cells changed')
    counts=[]
    for cell in book.cells:
        if cell.cell_type=='code':
            require(isinstance(cell.execution_count,int),'Missing original execution count')
            require(not any(x.output_type=='error' for x in cell.outputs),'Notebook error output')
            counts.append(cell.execution_count)
    require(len(counts)==len(code)+(1 if probe else 0) and len(set(counts))==len(counts),'Missing/duplicate executed cell')
    return {'original_code_cells':len(code),'private_probe_cells':1 if probe else 0,'execution_counts':counts,'source_sha256':hashlib.sha256(raw).hexdigest(),'executed_copy_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}


def kernel_fixture(root):
    from nbclient.exceptions import CellExecutionError
    def raw(text):return json.dumps({'cells':[{'cell_type':'code','id':'fixture-cell','metadata':{},'source':[text],'execution_count':None,'outputs':[]}],'metadata':{'kernelspec':{'name':'python3','display_name':'Python 3','language':'python'}},'nbformat':4,'nbformat_minor':5}).encode()
    positive=execute(raw('assert 2 + 3 == 5\n'),root/'kernel-positive.ipynb',root)
    try:execute(raw('assert False, "intended kernel fixture"\n'),root/'kernel-negative.ipynb',root)
    except CellExecutionError as error:
        require("AssertionError" in str(error) and "intended kernel fixture" in str(error),"Wrong kernel negative cause");negative=True
    else:raise RuntimeError('Actual kernel failed to propagate assertion failure')
    (root/'kernel-fixture.json').write_text(json.dumps({'positive':positive,'negative_expected_CellExecutionError':negative,'real_kernel':True}))


def main():
    docker_only();root=Path('/runtime');configure_kernel(root)
    if sys.argv[1]=='fixture':kernel_fixture(root);return
    m=json.loads(Path('/packet/manifest.json').read_text());source=root/'source'/m['notebook_path'];raw=source.read_bytes();frozen=json.loads((root/'frozen-documentation.json').read_text());require(hashlib.sha256(raw).hexdigest()==frozen['source_sha256'][m['notebook_path']],'Frozen notebook changed')
    probe='''import importlib, json, os, sys\nfrom pathlib import Path\nsys.path.insert(0, "/packet")\nfrom artifact_provenance import verify_installed_python, verify_jvm\nexpected = json.loads(Path("/runtime/candidate-artifact-manifest.json").read_text())\npy = verify_installed_python(expected["python_modules"])\nimport synapse.ml as namespace\norigin = getattr(namespace, "__file__", None)\npaths = list(namespace.__path__)\nassert paths and all(Path(p).resolve().is_relative_to(Path(sys.prefix).resolve()) for p in paths)\nif origin is not None:\n    assert Path(origin).resolve().is_relative_to(Path(sys.prefix).resolve())\njvm = verify_jvm(spark, expected["jvm_classes"])\nassert model.getBoosterNumClasses() == 5\nproof = {"python": py, "namespace_file": origin, "namespace_paths": paths, "jvm": jvm, "spark": spark.version, "python_executable": sys.executable, "num_classes": model.getBoosterNumClasses(), "num_features": model.getBoosterNumFeatures(), "original_source_cells_unchanged": True}\nPath("/runtime/notebook-provenance.json").write_text(json.dumps(proof, indent=2))\nprint("ACTUAL_BRANCH_API_PROVENANCE=" + json.dumps(proof))\nspark.stop()  # PRIVATE harness owns this fresh kernel/session; absent from public notebook.\n'''
    result=execute(raw,root/'executed-notebook.ipynb',root,probe)
    require(source.read_bytes()==raw,'Notebook execution changed cleared source')
    require((root/'notebook-provenance.json').is_file(),'No same-kernel provenance')
    (root/'notebook-result.json').write_text(json.dumps(result,indent=2));print('FULL_NOTEBOOK_AND_SAME_KERNEL_PROVENANCE_PASS='+json.dumps(result))
if __name__=='__main__':main()
