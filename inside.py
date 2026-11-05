"""Qualification only: frozen schema, actual base helpers, exact Linux website baseline."""
from pathlib import Path
import email
import hashlib
import io
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from wheel_metadata import select_metadata, exercise_zip_fixtures
from controller_checks import make_wheel_lock, validate_status, inspect_page, exercise_controller_fixtures

P, R = Path('/packet'), Path('/runtime')
S = R / 'source'
M = json.loads((P / 'manifest.json').read_text())
END = float(os.environ['VALIDATION_DEADLINE_EPOCH'])
RESULT = {'status': 'STARTING', 'lane_pass': False, 'full_validation': False,
          'source_edited': False, 'qualification_runtime': True,
          'full_canonical_notebook_executed': False, 'findings': [], 'steps': []}
assert Path('/.dockerenv').exists() and os.getuid() == 1001
assert sys.version_info[:3] == (3, 11, 8)


def guard():
    assert time.time() < END, 'ORIGINAL_Q_DEADLINE'
    assert shutil.disk_usage(R).free >= 2 * 1024**3, 'OWNED_RUNTIME_DISK_PRESSURE'


def run(label, args, seconds, cwd=None, allowed=(0,), cap=16 * 1024**2):
    guard()
    dest = R / (label + '.log')
    assert not dest.exists(), 'NO_STEP_RETRY'
    proc = subprocess.Popen(args, cwd=cwd or S, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    start, output = time.monotonic(), bytearray()
    try:
        while sel.get_map():
            guard()
            assert time.monotonic() - start < seconds, 'STEP_DEADLINE_' + label
            for key, _ in sel.select(.25):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                assert len(output) <= cap, 'OUTPUT_CAP_' + label
        code = proc.wait(timeout=3)
        assert code in allowed, 'STEP_EXIT_' + label + ':' + str(code)
        return code
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        sel.close()
        dest.write_bytes(output)
        RESULT['steps'].append({'label': label, 'argv': args, 'exit': proc.returncode,
                                'elapsed_seconds': time.monotonic() - start, 'bytes': len(output)})


def size(root):
    return sum(path.lstat().st_size for path in root.rglob('*') if path.is_file() and not path.is_symlink())


try:
    for relative, digest in M['packet_files_sha256'].items():
        assert hashlib.sha256((P / relative).read_bytes()).hexdigest() == digest
    # New controller paths are exercised on an owned tiny fixture BEFORE dependency downloads.
    fixture = R / 'controller-fixture'
    fixture.mkdir()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as archive:
        item = tarfile.TarInfo('nested/expected.txt')
        body = b'baseline expected mismatch, candidate expected match\n'
        item.size = len(body)
        archive.addfile(item, io.BytesIO(body))
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue())) as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            assert not relative.is_absolute() and '..' not in relative.parts and member.isfile()
            dest = fixture / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.extractfile(member).read())
    assert (fixture / 'nested/expected.txt').read_bytes() == body
    assert (fixture / 'nested/expected.txt').read_bytes() != b'incorrect baseline'
    assert size(fixture) == len(body)
    RESULT['tiny_controller_fixture'] = 'PASS; not a product test'
    def persist_selection(item):
        RESULT.setdefault('wheel_metadata_selections', []).append(item)
        (R/'wheel-selection-progress.json').write_text(json.dumps(RESULT['wheel_metadata_selections'], indent=2)+'\n')
    RESULT['zip_controller_fixtures'] = exercise_zip_fixtures(persist_selection)
    RESULT['other_controller_fixtures'] = exercise_controller_fixtures(fixture)
    (R/'controller-fixture-results.json').write_text(json.dumps({'ZIP':RESULT['zip_controller_fixtures'], 'other':RESULT['other_controller_fixtures']}, indent=2)+'\n')
    assert not S.exists()
    shutil.copytree('/source', S, symlinks=True)
    for relative, digest in M['baseline_source_sha256'].items():
        assert hashlib.sha256((S / relative).read_bytes()).hexdigest() == digest
    os.environ['PATH'] = '/runtime/home/.local/bin:' + os.environ['PATH']
    run('bundled-pip-version', [sys.executable, '-m', 'pip', '--version'], 10)
    build_tool_probe = '''import importlib.metadata,json
result={}
for name in ('pip','setuptools','wheel'):
    try: result[name]=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: result[name]=None
print(json.dumps(result))
'''
    run('bundled-build-tool-versions', [sys.executable, '-c', build_tool_probe], 10)
    wheels = R / 'wheels'
    wheels.mkdir()
    run('resolve-and-build-wheel-closure', [sys.executable, '-m', 'pip', 'wheel', '--no-input', '--verbose',
        '--only-binary=:all:', '--no-binary=pyspark', '--wheel-dir', str(wheels), '--constraint', str(P/'constraints-Q1-resolved.txt'),
        '-r', str(P / 'requirements.in')], 600)
    RESULT['build_isolation_version_evidence_log'] = 'resolve-and-build-wheel-closure.log; exact versions must be read before accepting or reusing the resolved build closure'
    artifacts = []
    for wheel in sorted(wheels.glob('*.whl')):
        with zipfile.ZipFile(wheel) as z:
            meta = email.message_from_bytes(select_metadata(wheel.name, z, persist_selection))
        name, version = meta['Name'], meta['Version']
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        artifacts.append({'name': name, 'version': version, 'file': wheel.name,
                          'bytes': wheel.stat().st_size, 'sha256': digest})
        (R/'resolved-artifact-progress.json').write_text(json.dumps(artifacts, indent=2)+'\n')
    assert artifacts and size(wheels) <= M['wheelhouse_max_bytes']
    RESULT['resolved_artifacts'] = artifacts
    # This lock is produced by the approved qualification, never claimed pre-resolved.
    lock = R / 'resolved-wheel-lock.txt'
    lock.write_text(make_wheel_lock(artifacts))
    run('install-verified-wheel-lock', [sys.executable, '-m', 'pip', 'install', '--user', '--no-input',
        '--no-index', '--find-links', str(wheels), '--require-hashes', '-r', str(lock)], 240)
    run('installed-pip-check', [sys.executable, '-m', 'pip', 'check'], 30)
    run('installed-versions', [sys.executable, '-m', 'pip', 'list', '--format=json'], 30)
    # FIRST real evidence gate: all16 metadata-only Spark schemas, no data-row reads.
    run('all16-parquet-schemas', [sys.executable, str(P / 'read_schema.py'), str(P / 'metadata'),
        '--state-sha256', M['metadata_state_sha256'], '--out', str(R / 'schemas.json')], 30)
    notebook = S / M['notebook']
    schema_report = json.loads((R / 'schemas.json').read_text())
    RESULT['findings'].extend(schema_report['findings'])
    run('unchanged-actual-helper-oracles', [sys.executable, str(P / 'helpers_evidence.py'), str(notebook),
        '--mode', 'baseline', '--out', str(R / 'baseline-helpers.json')], 60)
    # Preserve both exact official command sequences and their complete original failures.
    for name, command in M['black_commands'].items():
        code = run('baseline-black-' + name, ['sh', '-c', command], 300, allowed=(0, 1, 123))
        RESULT.setdefault('baseline_black_exit', {})[name] = code
    web = S / 'website'
    run('node-version', ['node', '--version'], 10, cwd=web)
    run('npm-version', ['npm', '--version'], 10, cwd=web)
    assert int((R / 'node-version.log').read_text().strip().lstrip('v').split('.')[0]) == M['official_node_major']
    for relative, digest in M['website_inputs'].items():
        assert hashlib.sha256((web / relative).read_bytes()).hexdigest() == digest
    run('npm-ci-frozen-lock', ['npm', 'ci', '--no-audit', '--no-fund'], 600, cwd=web)
    RESULT['actual_owned_dependency_bytes'] = {'wheelhouse': size(wheels),
        'installed_python': size(R / 'home/.local'), 'node_modules': size(web / 'node_modules')}
    # Existing package source is used directly. All declared dependencies were resolved above.
    os.environ['PYTHONPATH'] = str(S / 'tools/docgen')
    run('unchanged-full-website-channel', [sys.executable, str(P / 'website_generate.py')], 600)
    test_code = run('baseline-website-tests', ['npm', 'test'], 300, cwd=web, allowed=(0,1))
    if test_code:
        RESULT['findings'].append({'gate': 'baseline-npm-test', 'exit': test_code})
    code = run('baseline-complete-linux-build', ['npm', 'run', 'build'], 900, cwd=web, allowed=(0, 1))
    RESULT['baseline_linux_build_exit'] = code
    if code:
        RESULT['findings'].append({'gate':'baseline-linux-build', 'exit':code})
        RESULT.update(linux_render_verified=False, required_next='Inspect exact error; no speculative notebook edit')
    else:
        pages=[]
        for page in (web / 'build').rglob('*.html'):
            identity=inspect_page(page.read_text(errors='replace'), M['current_target_route'])
            if identity['title'].startswith('PDP and ICE Explainers'):
                pages.append({'file':str(page.relative_to(web)), 'title':identity['title'],
                              'canonical':identity['canonical']})
        RESULT['all_title_matches_information_only'] = pages
        expected = web / M['current_target_html']
        if not expected.is_file():
            RESULT['findings'].append({'gate':'current-route-page', 'expected':M['current_target_html'], 'finding':'missing'})
            RESULT['linux_render_verified'] = False
        else:
            identity=inspect_page(expected.read_text(), M['current_target_route'])
            if not identity['is_target']:
                RESULT['findings'].append({'gate':'current-route-page','finding':'canonical/content mismatch','canonical':identity['canonical']})
                RESULT['linux_render_verified'] = False
            else:
                RESULT.update(linux_render_verified=True, rendered_page={'file':M['current_target_html'],
                    'canonical':identity['canonical'],'sha256':hashlib.sha256(expected.read_bytes()).hexdigest()})
    assert hashlib.sha256(notebook.read_bytes()).hexdigest() == M['baseline_source_sha256'][M['notebook']]
    run('frozen-source-status', ['git', 'status', '--porcelain=v1', '-z'], 15)
    allowed=('website/docs/','website/node_modules/','website/build/','website/.docusaurus/')
    RESULT['allowed_generated_changes'] = validate_status((R/'frozen-source-status.log').read_bytes(), allowed)
    RESULT.update(status='COMPLETE_WITH_FINDINGS' if RESULT['findings'] else 'SUCCESS', lane_pass=True)
except BaseException as exc:
    RESULT.update(status='TERMINAL_'+type(exc).__name__, error=str(exc), lane_pass=False)
    raise
finally:
    RESULT['completed_at_epoch'] = time.time()
    (R / 'result.json').write_text(json.dumps(RESULT, indent=2) + '\n')
