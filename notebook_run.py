"""Private unrun nbclient draft: recorded setup/cleanup cells plus ALL15 contribution cells."""
import copy
import json
import os
from pathlib import Path
import time
from docs_contract import BOOK, PUBLIC_URI, book, runtime_book, manifest, require, sha


def execute_client(notebook, timeout, kernel_name, cwd, env, on_cell_start=None):
    from nbclient import NotebookClient
    options = ({'timeout': 600, 'timeout_func': timeout} if callable(timeout)
               else {'timeout': timeout})
    if on_cell_start is not None:
        options['on_cell_start'] = on_cell_start
    client = NotebookClient(notebook, kernel_name=kernel_name, shutdown_kernel='immediate',
                            allow_errors=False, resources={'metadata': {'path': str(cwd)}},
                            **options)
    return client.execute(env=env)


def execution_notebook(value):
    """Owned runtime copy: normalize JSON source arrays, preserving every other field."""
    import nbformat
    normalized = copy.deepcopy(value)
    for cell in normalized['cells']:
        source = cell['source']
        require(isinstance(source, str) or
                (isinstance(source, list) and all(isinstance(line, str) for line in source)),
                'INVALID_NOTEBOOK_SOURCE_REPRESENTATION')
        if isinstance(source, list):
            cell['source'] = ''.join(source)
    return nbformat.from_dict(normalized)


def dependency_fixture(kernel_name, cwd, env):
    """Future Docker-only real kernel positive/error, same production execution function."""
    import nbformat
    from nbclient.exceptions import CellExecutionError
    good = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(
        'print("DML_REAL_KERNEL_CONTROL")')])
    execute_client(good, lambda cell: 15, kernel_name, cwd, env)
    require(good.cells[0]['execution_count'] is not None and any(
        'DML_REAL_KERNEL_CONTROL' in output.get('text', '')
        for output in good.cells[0]['outputs']), 'REAL_KERNEL_POSITIVE')
    bad = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(
        'raise RuntimeError("DML_REAL_KERNEL_FAILURE_CONTROL")')])
    try:
        execute_client(bad, lambda cell: 15, kernel_name, cwd, env)
    except CellExecutionError:
        require(any(output.get('output_type') == 'error' for output in bad.cells[0]['outputs']),
                'REAL_KERNEL_ERROR_NOT_RETAINED')
    else:
        raise RuntimeError('REAL_KERNEL_NEGATIVE_ACCEPTED')
    hook_indices = []
    def record_start(cell, cell_index, **kwargs):
        hook_indices.append(cell_index)
    hooked = nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell('print("DML_HOOK_FIRST")'),
        nbformat.v4.new_code_cell('print("DML_HOOK_SECOND")')])
    execute_client(hooked, lambda cell: 15, kernel_name, cwd, env,
                   on_cell_start=record_start)
    require(hook_indices == [0, 1] and all(
        cell['execution_count'] is not None for cell in hooked.cells),
        'REAL_KERNEL_HOOK_INDICES_OR_EXECUTION')

    class HookAbortControl(RuntimeError):
        pass
    abort_indices = []
    def abort_start(cell, cell_index, **kwargs):
        abort_indices.append(cell_index)
        if cell_index == 1:
            raise HookAbortControl('DML_HOOK_ABORT_CONTROL')
    aborted = nbformat.v4.new_notebook(cells=[
        nbformat.v4.new_code_cell('print("DML_BEFORE_HOOK_ABORT")'),
        nbformat.v4.new_code_cell('raise AssertionError("ABORTED_CELL_EXECUTED")'),
        nbformat.v4.new_code_cell('raise AssertionError("SUBSEQUENT_CELL_EXECUTED")')])
    try:
        execute_client(aborted, lambda cell: 15, kernel_name, cwd, env,
                       on_cell_start=abort_start)
    except HookAbortControl as error:
        require(str(error) == 'DML_HOOK_ABORT_CONTROL' and abort_indices == [0, 1],
                'REAL_KERNEL_HOOK_ABORT_NOT_PROPAGATED')
        require(aborted.cells[0]['execution_count'] is not None and any(
            'DML_BEFORE_HOOK_ABORT' in output.get('text', '')
            for output in aborted.cells[0]['outputs']), 'REAL_KERNEL_PRE_ABORT_NOT_EXECUTED')
        require(all(cell['execution_count'] is None and cell['outputs'] == []
                    for cell in aborted.cells[1:]), 'REAL_KERNEL_HOOK_ABORT_DID_NOT_STOP')
    else:
        raise RuntimeError('REAL_KERNEL_HOOK_ABORT_ACCEPTED')
    # Reproduce the canonical on-disk JSON representation, not new_code_cell's strings.
    raw_source_lists = json.dumps({'nbformat': 4, 'nbformat_minor': 4, 'metadata': {},
        'cells': [
            {'cell_type': 'markdown', 'metadata': {}, 'source': ['# Source-list control\n',
                                                                       'Markdown remains present.\n']},
            {'cell_type': 'code', 'metadata': {}, 'execution_count': None, 'outputs': [],
             'source': ['value = 6\n', 'print("DML_SOURCE_LIST_FIRST", value)\n']},
            {'cell_type': 'code', 'metadata': {}, 'execution_count': None, 'outputs': [],
             'source': ['print("DML_SOURCE_LIST_SECOND", value + 1)\n']}]})
    raw_value = json.loads(raw_source_lists)
    invalid_source_controls = []
    for label, invalid_source in [('non-string-non-list', 17),
                                  ('non-string-list-element', ['valid line\n', 17])]:
        invalid = copy.deepcopy(raw_value)
        invalid['cells'][1]['source'] = invalid_source
        unchanged = json.dumps(invalid, sort_keys=True)
        try:
            # No execute_client call: validation must reject before any kernel launch.
            execution_notebook(invalid)
        except RuntimeError as error:
            require(str(error) == 'INVALID_NOTEBOOK_SOURCE_REPRESENTATION',
                    'WRONG_INVALID_SOURCE_FAILURE:' + label)
        else:
            raise RuntimeError('INVALID_NOTEBOOK_SOURCE_ACCEPTED:' + label)
        require(json.dumps(invalid, sort_keys=True) == unchanged,
                'INVALID_NOTEBOOK_INPUT_MUTATED:' + label)
        invalid_source_controls.append(label)
    normalized = execution_notebook(raw_value)
    require(all(isinstance(cell.source, str) and 'id' not in cell for cell in normalized.cells),
            'SOURCE_LIST_NORMALIZATION_OR_ID_MUTATION')
    expected_value = copy.deepcopy(raw_value)
    for cell in expected_value['cells']:
        cell['source'] = ''.join(cell['source'])
    require(normalized == expected_value and json.dumps(raw_value) == json.dumps(
        json.loads(raw_source_lists)), 'NORMALIZATION_CHANGED_NON_SOURCE_FIELDS_OR_INPUT')
    normalized.cells.insert(0, nbformat.v4.new_code_cell('print("DML_LIST_OPERATOR_SETUP")'))
    normalized.cells.append(nbformat.v4.new_code_cell('print("DML_LIST_OPERATOR_CLEANUP")'))
    source_list_indices = []
    def source_list_start(cell, cell_index, **kwargs):
        if cell['cell_type'] == 'code':
            source_list_indices.append(cell_index)
    execute_client(normalized, lambda cell: 15, kernel_name, cwd, env,
                   on_cell_start=source_list_start)
    require(source_list_indices == [0, 2, 3, 4] and len(normalized.cells[1:-1]) == 3,
            'SOURCE_LIST_OPERATOR_CANONICAL_MAPPING')
    require(all(normalized.cells[index]['execution_count'] is not None
                for index in (0, 2, 3, 4)) and normalized.cells[1]['cell_type'] == 'markdown',
            'SOURCE_LIST_REAL_KERNEL_EXECUTION')
    require(any('DML_SOURCE_LIST_SECOND 7' in output.get('text', '')
                for output in normalized.cells[3]['outputs']), 'SOURCE_LIST_ACTUAL_OUTPUT')
    return {'realKernelPositive': True, 'realKernelFailure': True,
            'realKernelHookIndices': hook_indices, 'realKernelHookAbort': True,
            'hookAbortSubsequentCellsUnexecuted': True,
            'rawJSONSourceListMarkdownCode': True,
            'invalidSourceNormalizationControls': invalid_source_controls,
            'sourceListOperatorCanonicalMapping': True, 'projectFits': 0}


def run(config, source, out):
    require(config['executionApproved'] is True and config['uriCopyApproved'] is True,
            'NOTEBOOK_EXECUTION_OR_URI_COPY_NOT_APPROVED')
    require(time.time() < config['originalDeadlineEpoch'] - 120, 'ORIGINAL_DEADLINE')
    raw = (source / BOOK).read_bytes()
    require(sha(raw) == config['notebookSHA256'], 'FROZEN_NOTEBOOK_CHANGED')
    candidate_path = Path(config['stagedCSV'])
    require(not any(p.is_symlink() for p in (candidate_path, *candidate_path.parents)),
            'STAGED_CSV_SYMLINK')
    staged = candidate_path.resolve(strict=True)
    require(staged.is_file() and not staged.is_symlink(), 'STAGED_CSV_NOT_REGULAR')
    require(staged.stat().st_size == config['csvBytes'] and sha(staged.read_bytes()) == config['csvSHA256'],
            'STAGED_CSV_IDENTITY')
    artifact_path = Path(config['artifactManifest']).resolve(strict=True)
    info = manifest(artifact_path)
    original = book(raw)
    runtime = runtime_book(raw, staged.as_uri())
    cell_hash = lambda c: sha(json.dumps(c, sort_keys=True).encode())
    other14 = {str(i): cell_hash(c) for i, c in enumerate(original['cells']) if i != 5}
    require(all(cell_hash(runtime['cells'][int(i)]) == digest for i, digest in other14.items()),
            'OTHER_FOURTEEN_CELLS_CHANGED')
    receipt = {'complete': False, 'canonicalCells': 15, 'canonicalCodeCells': 7,
               'kernelSetupCells': 1, 'kernelCleanupCells': 1, 'canonicalSHA256': sha(raw),
               'csvSHA256': config['csvSHA256'], 'artifactManifestSHA256': sha(artifact_path.read_bytes()),
               'runtimeOnlyURICopy': {'cell': 5, 'from': PUBLIC_URI, 'to': staged.as_uri(),
                                    'runtimeCopySHA256': sha(json.dumps(runtime, sort_keys=True).encode()),
                                    'oneConstantASTVerified': True, 'otherFourteenCellHashes': other14},
               'noLiveWASBSConnectorClaim': True,
               'executionSourceNormalization': {
                   'method': 'Join JSON source arrays into strings on deep runtime copy only',
                   'canonicalSourceRepresentationUnchanged': True,
                   'allNonSourceFieldsPreserved': True, 'cellIDsAddedByNormalization': False}}
    (out / 'notebook-execution-receipt.json').write_text(json.dumps(receipt, indent=2))
    import nbformat
    import ipykernel
    # No package installation or project source import before the reviewed execution boundary.
    helper_dir = str(Path(__file__).resolve().parent)
    prelude = ('import json,sys\nfrom pathlib import Path\n'
               'sys.path.insert(0,' + repr(helper_dir) + ')\n'
               'from docs_contract import manifest,verify_runtime\n'
               'from pyspark import SparkContext\n'
               'assert SparkContext._active_spark_context is None\n'
               'from pyspark.sql import SparkSession\n'
               '_docs_info=manifest(' + repr(str(artifact_path)) + ')\n'
               'spark=SparkSession.builder.master("local[3]").appName("full DML notebook").config('
               '"spark.jars", ",".join(r["path"] for r in _docs_info["jars"])).getOrCreate()\n'
               '_docs_provenance=verify_runtime(spark,_docs_info)\n'
               'import os\n'
               '_docs_pids=[os.getpid(),int(spark._jvm.java.lang.ProcessHandle.current().pid())]\n'
               '_docs_provenance["ownedProcesses"]=[{"pid":p,"startTimeTicks":'
               'int(Path("/proc/"+str(p)+"/stat").read_text().rsplit(")",1)[1].split()[19])}'
               ' for p in _docs_pids]\n'
               'Path(' + repr(str(out / 'notebook-installed-provenance.json')) + ').write_text('
               'json.dumps(_docs_provenance,indent=2))\n')
    operator_cell = nbformat.v4.new_code_cell(prelude)
    operator_cell.pop('id', None)
    # Setup is visible and separate, never presented as an authored contribution cell.
    expanded = execution_notebook(runtime)
    expanded.cells.insert(0, operator_cell)
    cleanup_cell = nbformat.v4.new_code_cell(
        'spark.stop()\nprint("DML_NOTEBOOK_SPARK_STOP_COMPLETE")\n')
    cleanup_cell.pop('id', None)
    expanded.cells.append(cleanup_cell)
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONPATH', 'PYTHONSTARTUP', 'SPARK_HOME')}
    require(Path(config['pythonExecutable']).resolve() == Path(os.sys.executable).resolve(),
            'WRONG_BRANCH_PYTHON_INTERPRETER')
    kernel_root = out / 'jupyter'
    kernel_dir = kernel_root / 'kernels' / 'dml-branch'
    kernel_dir.mkdir(parents=True, exist_ok=False)
    kernel = {'argv': [config['pythonExecutable'], '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
              'display_name': 'Verified branch DML', 'language': 'python',
              'env': {'PYSPARK_PYTHON': config['pythonExecutable'],
                      'PYSPARK_DRIVER_PYTHON': config['pythonExecutable'],
                      'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                      'PYSPARK_SUBMIT_ARGS': '--master local[3] --driver-memory 4g --jars ' +
                      ','.join(r['path'] for r in info['jars']) + ' pyspark-shell'}}
    (kernel_dir / 'kernel.json').write_text(json.dumps(kernel, indent=2))
    env['JUPYTER_PATH'] = str(kernel_root)
    previous = os.environ.get('JUPYTER_PATH')
    os.environ['JUPYTER_PATH'] = str(kernel_root)
    try:
        def timeout(cell):
            left = int(config['originalDeadlineEpoch'] - time.time() - 120)
            require(left > 0, 'ORIGINAL_NOTEBOOK_CLOCK_EXPIRED')
            return min(config['cellCapSeconds'], left)
        from docs_admission import notebook_gate
        gate_state = {'baseline': None, 'seen': []}
        def on_cell_start(cell, cell_index):
            if cell_index == 8:
                gate_state['baseline'] = notebook_gate(config, out, 'before-singular')
                gate_state['seen'].append('singular')
            elif cell_index == 12:
                notebook_gate(config, out, 'before-plural', gate_state['baseline'])
                gate_state['seen'].append('plural')
        execute_client(expanded, timeout, 'dml-branch', out, env, on_cell_start=on_cell_start)
        require(gate_state['seen'] == ['singular', 'plural'], 'BOTH_ACTUAL_PREFIT_GATES_REQUIRED')
        require(len(expanded.cells) == 17, 'SETUP_FIFTEEN_CLEANUP_REQUIRED')
        for index, (before, after) in enumerate(zip(runtime['cells'], expanded.cells[1:-1])):
            require(before['cell_type'] == after['cell_type'] and
                    ''.join(before['source']) == ''.join(after['source']), 'CELL_SOURCE_CHANGED')
            if before['cell_type'] == 'code':
                require(after['execution_count'] is not None, 'UNEXECUTED_CANONICAL_CELL:' + str(index))
                require(not any(o.get('output_type') == 'error' for o in after['outputs']),
                        'CELL_ERROR_OUTPUT:' + str(index))
        receipt['complete'] = True
        require(expanded.cells[-1]['execution_count'] is not None, 'CLEANUP_UNEXECUTED')
        receipt['actualKernelCodeCellsExecuted'] = 9
        receipt['sparkCleanupCellCompleted'] = True
    finally:
        if previous is None:
            os.environ.pop('JUPYTER_PATH', None)
        else:
            os.environ['JUPYTER_PATH'] = previous
        # Retain partial actual outputs before surfacing any failure. No synthesized cell PASS.
        # Preserve absence of source IDs; do not let an output writer normalize them.
        for index, canonical in enumerate(runtime['cells']):
            if 'id' not in canonical:
                expanded.cells[index + 1].pop('id', None)
        (out / 'executed-notebook-with-operator-setup.ipynb').write_text(
            json.dumps(expanded, indent=1) + '\n')
        contribution_view = copy.deepcopy(expanded)
        contribution_view.cells = contribution_view.cells[1:-1]
        (out / 'executed-notebook-fifteen-cell-view.ipynb').write_text(
            json.dumps(contribution_view, indent=1) + '\n')
        receipt['canonicalSourceUnchanged'] = sha((source / BOOK).read_bytes()) == sha(raw)
        receipt['expandedExecutionSHA256'] = sha(
            (out / 'executed-notebook-with-operator-setup.ipynb').read_bytes())
        receipt['fifteenCellViewSHA256'] = sha(
            (out / 'executed-notebook-fifteen-cell-view.ipynb').read_bytes())
        provenance_path = out / 'notebook-installed-provenance.json'
        stop_rows = []
        if provenance_path.exists():
            processes = json.loads(provenance_path.read_text()).get('ownedProcesses', [])
            for owned in processes:
                deadline = min(time.monotonic() + 2, time.monotonic() + max(
                    0, config['originalDeadlineEpoch'] - time.time() - 120))
                original_alive = True
                while original_alive and time.monotonic() < deadline:
                    try:
                        fields = Path('/proc/' + str(owned['pid']) + '/stat').read_text().rsplit(')', 1)[1].split()
                        original_alive = int(fields[19]) == owned['startTimeTicks'] and fields[0] != 'Z'
                    except FileNotFoundError:
                        original_alive = False
                    if original_alive:
                        time.sleep(.05)
                stop_rows.append({**owned, 'originalProcessStopped': not original_alive})
        receipt['ownedStopProof'] = stop_rows
        receipt['ownedStopVerified'] = len(stop_rows) == 2 and all(
            row['originalProcessStopped'] for row in stop_rows)
        if not receipt['ownedStopVerified']:
            receipt['complete'] = False
        (out / 'notebook-execution-receipt.json').write_text(json.dumps(receipt, indent=2))
    require(receipt['ownedStopVerified'], 'OWNED_KERNEL_OR_SPARK_STOP_UNVERIFIED')
    require(receipt['canonicalSourceUnchanged'], 'CONTRIBUTION_NOTEBOOK_MUTATED')
    return receipt


def render_evidence(notebook_path, destination):
    """Offline evidence view, not a Jupyter theme or replacement website screenshot."""
    import html
    import markdown
    value = json.loads(notebook_path.read_text())
    sections = []
    for index, cell in enumerate(value['cells']):
        source = ''.join(cell['source'])
        if cell['cell_type'] == 'markdown':
            body = markdown.markdown(source, extensions=['tables', 'fenced_code'])
        else:
            body = '<pre class="code">' + html.escape(source) + '</pre>'
            for output in cell.get('outputs', []):
                if output['output_type'] == 'stream':
                    text = output.get('text', '')
                    if isinstance(text, list):
                        text = ''.join(text)
                elif output['output_type'] in ('execute_result', 'display_data'):
                    data = output.get('data', {})
                    require('text/plain' in data and not any(k.startswith('image/') for k in data),
                            'RICH_OUTPUT_NEEDS_EXACT_RENDER_CLOSURE')
                    text = data['text/plain']
                    if isinstance(text, list):
                        text = ''.join(text)
                elif output['output_type'] == 'error':
                    text = '\n'.join(output.get('traceback', []))
                else:
                    raise RuntimeError('UNRECOGNIZED_NOTEBOOK_OUTPUT')
                body += '<pre class="output">' + html.escape(text) + '</pre>'
        sections.append('<section><h2>Cell ' + str(index) + '</h2>' + body + '</section>')
    document = ('<!doctype html><html><head><meta charset="utf-8"><title>DML executed notebook evidence</title>'
                '<style>body{font:16px sans-serif;max-width:1100px;margin:2em auto;padding:1em}'
                'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:1em}'
                'table,td,th{border:1px solid #888;border-collapse:collapse;padding:.4em}'
                'section{border-top:1px solid #ccc}</style></head><body>'
                '<h1>Executed notebook evidence: all fifteen cells</h1>'
                '<p>Offline evidence view; actual textual outputs. Not a Jupyter theme or website UI.</p>' +
                ''.join(sections) + '</body></html>')
    destination.write_text(document)
    return {'cells': len(value['cells']), 'htmlSHA256': sha(document.encode()),
            'view': 'all-cell offline evidence, no external scripts/styles/fonts'}
