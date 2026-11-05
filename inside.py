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
    assert not S.exists()
    shutil.copytree('/source', S, symlinks=True)
    for relative, digest in M['baseline_source_sha256'].items():
        assert hashlib.sha256((S / relative).read_bytes()).hexdigest() == digest
    for relative, digest in M['packet_files_sha256'].items():
        assert hashlib.sha256((P / relative).read_bytes()).hexdigest() == digest
    os.environ['PATH'] = '/runtime/home/.local/bin:' + os.environ['PATH']
    run('bundled-pip-version', [sys.executable, '-m', 'pip', '--version'], 10)
    wheels = R / 'wheels'
    wheels.mkdir()
    run('resolve-and-build-wheel-closure', [sys.executable, '-m', 'pip', 'wheel', '--no-input', '--verbose',
        '--only-binary=:all:', '--no-binary=pyspark', '--wheel-dir', str(wheels),
        '-r', str(P / 'requirements.in')], 600)
    RESULT['build_isolation_version_evidence_log'] = 'resolve-and-build-wheel-closure.log; exact versions must be read before accepting or reusing the resolved build closure'
    artifacts, requirements, normalized = [], [], set()
    for wheel in sorted(wheels.glob('*.whl')):
        with zipfile.ZipFile(wheel) as z:
            metadata_paths = [name for name in z.namelist() if name.endswith('.dist-info/METADATA')]
            assert len(metadata_paths) == 1
            meta = email.message_from_bytes(z.read(metadata_paths[0]))
        name, version = meta['Name'], meta['Version']
        key = name.lower().replace('_', '-').replace('.', '-')
        assert key not in normalized, 'DUPLICATE_RESOLVED_DISTRIBUTION'
        normalized.add(key)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        artifacts.append({'name': name, 'version': version, 'file': wheel.name,
                          'bytes': wheel.stat().st_size, 'sha256': digest})
        requirements.append(name + '==' + version + ' --hash=sha256:' + digest)
    assert artifacts and size(wheels) <= M['wheelhouse_max_bytes']
    RESULT['resolved_artifacts'] = artifacts
    # This lock is produced by the approved qualification, never claimed pre-resolved.
    lock = R / 'resolved-wheel-lock.txt'
    lock.write_text('\n'.join(requirements) + '\n')
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
        from html.parser import HTMLParser
        class PageIdentity(HTMLParser):
            def __init__(self):
                super().__init__(); self.in_title=False; self.title=''; self.canonical=None
            def handle_starttag(self, tag, attrs):
                attrs=dict(attrs)
                if tag=='title': self.in_title=True
                if tag=='link' and attrs.get('rel')=='canonical': self.canonical=attrs.get('href')
            def handle_endtag(self, tag):
                if tag=='title': self.in_title=False
            def handle_data(self, text):
                if self.in_title: self.title += text
        pages=[]
        for page in (web / 'build').rglob('*.html'):
            identity=PageIdentity(); identity.feed(page.read_text(errors='replace'))
            if identity.title.startswith('PDP and ICE Explainers'):
                pages.append({'file':str(page.relative_to(web)), 'title':identity.title,
                              'canonical':identity.canonical})
        RESULT['all_title_matches_information_only'] = pages
        expected = web / M['current_target_html']
        if not expected.is_file():
            RESULT['findings'].append({'gate':'current-route-page', 'expected':M['current_target_html'], 'finding':'missing'})
            RESULT['linux_render_verified'] = False
        else:
            from urllib.parse import unquote,urlsplit
            text=expected.read_text(); identity=PageIdentity(); identity.feed(text)
            if not identity.canonical or unquote(urlsplit(identity.canonical).path) != M['current_target_route'] or 'ICETransformer' not in text:
                RESULT['findings'].append({'gate':'current-route-page','finding':'canonical/content mismatch','canonical':identity.canonical})
                RESULT['linux_render_verified'] = False
            else:
                RESULT.update(linux_render_verified=True, rendered_page={'file':M['current_target_html'],
                    'canonical':identity.canonical,'sha256':hashlib.sha256(expected.read_bytes()).hexdigest()})
    assert hashlib.sha256(notebook.read_bytes()).hexdigest() == M['baseline_source_sha256'][M['notebook']]
    run('frozen-source-status', ['git', 'status', '--porcelain=v1', '-z'], 15)
    changes=(R/'frozen-source-status.log').read_bytes().split(b'\0')
    allowed=('website/docs/','website/node_modules/','website/build/','website/.docusaurus/')
    i=0
    while i < len(changes):
        entry=changes[i];i+=1
        if not entry:continue
        status,path=entry[:2].decode(),entry[3:].decode()
        assert path.startswith(allowed), 'UNAUTHORIZED_SOURCE_CHANGE:'+path
        if 'R' in status or 'C' in status:
            original=changes[i].decode();i+=1
            assert original.startswith(allowed), 'UNAUTHORIZED_RENAME_SOURCE:'+original
    RESULT.update(status='COMPLETE_WITH_FINDINGS' if RESULT['findings'] else 'SUCCESS', lane_pass=True)
except BaseException as exc:
    RESULT.update(status='TERMINAL_'+type(exc).__name__, error=str(exc), lane_pass=False)
    raise
finally:
    RESULT['completed_at_epoch'] = time.time()
    (R / 'result.json').write_text(json.dumps(RESULT, indent=2) + '\n')
