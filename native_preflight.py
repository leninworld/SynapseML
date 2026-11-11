"""Read the resolved VW jar, verify its ELF and actual system dependencies."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
from validation_runtime import require

MEMBER = 'natives/linux_64/libvw_jni.so'


def inspect(jar, destination, expected_sha1):
    jar, destination = Path(jar), Path(destination)
    require(jar.is_file() and jar.stat().st_size <= 16 * 1024**2, 'Native jar size')
    raw = jar.read_bytes()
    require(hashlib.sha1(raw).hexdigest() == expected_sha1, 'Native jar identity')
    with zipfile.ZipFile(jar) as archive:
        matches = [row for row in archive.infolist() if row.filename == MEMBER]
        require(len(matches) == 1 and matches[0].file_size <= 32 * 1024**2,
                'Native member missing, duplicate or oversized')
        with archive.open(matches[0]) as source:
            data = source.read(32 * 1024**2 + 1)
    require(len(data) <= 32 * 1024**2 and len(data) >= 64 and data[:5] == b'\x7fELF\x02'
            and data[5] == 1 and int.from_bytes(data[18:20], 'little') == 62,
            'Native image is not Linux x86-64 ELF')
    destination.mkdir(exist_ok=False)
    library = destination / 'libvw_jni.so'
    library.write_bytes(data)
    completed = subprocess.run(['ldd', str(library)], capture_output=True, text=True, timeout=20)
    # Retain the real output before any assertion; missing dependencies are not a product result.
    (destination / 'ldd.stdout').write_text(completed.stdout)
    (destination / 'ldd.stderr').write_text(completed.stderr)
    (destination / 'ldd-exit.json').write_text(json.dumps({'exit': completed.returncode, 'stdout': completed.stdout[:65536], 'stderr': completed.stderr[:65536]}))
    require(len(completed.stdout) + len(completed.stderr) <= 64 * 1024, 'Native ldd output cap')
    require(completed.returncode == 0 and 'not found' not in completed.stdout + completed.stderr,
            'Native system dependency unavailable')
    return {'state': 'SYSTEM_DEPENDENCIES_RESOLVED', 'jar_sha256': hashlib.sha256(raw).hexdigest(),
            'library_sha256': hashlib.sha256(data).hexdigest(), 'member': MEMBER,
            'qualification': 'ELF/ldd only. Actual VW native execution must pass its from-string control.'}


def fixture(root):
    root = Path(root); root.mkdir(exist_ok=False)
    # Actual pinned-image ELF and ldd, not a fabricated subprocess response.
    actual = Path('/bin/true').read_bytes()
    require(b'libc.so.6' in actual, 'Fixture ELF dependency layout changed')
    cases = [('real', actual, MEMBER, False), ('wrong-member', actual, 'wrong.so', True),
             ('short-ELF', b'\x7fELF', MEMBER, True),
             ('missing-library', actual.replace(b'libc.so.6', b'zzzz.so.6'), MEMBER, True)]
    records = []
    for name, payload, member, refused in cases:
        archive = root / (name + '.jar')
        with zipfile.ZipFile(archive, 'x') as packed: packed.writestr(member, payload)
        try:
            proof = inspect(archive, root / name, hashlib.sha1(archive.read_bytes()).hexdigest())
        except RuntimeError as error:
            require(refused, 'Unexpected native fixture failure')
            records.append({'case': name, 'refused': True, 'reason': str(error)})
        else:
            require(not refused, 'Native failure fixture falsely accepted')
            records.append({'case': name, 'refused': False, 'proof': proof})
    try: inspect(root / 'real.jar', root / 'wrong-hash', '0' * 40)
    except RuntimeError as error: require(str(error) == 'Native jar identity', 'Wrong identity rejection')
    else: raise RuntimeError('Native fixture accepted wrong jar identity')
    (root / 'fixture-result.json').write_text(json.dumps(records, indent=2))


if __name__ == '__main__':
    import sys
    from validation_runtime import docker_only
    docker_only()
    if sys.argv[1] == 'fixture': fixture(sys.argv[2])
    else:
        jar, destination, expected, output = sys.argv[1:]
        try:
            proof = inspect(jar, destination, expected)
        except BaseException as error:
            record = Path(destination) / 'ldd-exit.json'
            failure = {'state': 'FAILED_NATIVE_PREREQUISITE', 'error': repr(error),
                       'ldd': json.loads(record.read_text()) if record.is_file() else None}
            Path(output).write_text(json.dumps(failure, indent=2))
            raise
        else:
            Path(output).write_text(json.dumps(proof, indent=2))
