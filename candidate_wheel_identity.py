"""C2: same current-wheel identity checker for actual consumers and early controls.

All controls run only inside the future approved Docker job, never at import.
"""
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import re
import zipfile

from validation_runtime import require


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(direct, wheel_path, expected_sha256):
    """Accept recorded SHA256 or absent hash plus exact actual local wheel URI."""
    wheel = Path(wheel_path).resolve(strict=True)
    require(wheel.is_file() and re.fullmatch('[0-9a-f]{64}', expected_sha256) is not None,
            'Invalid bound current wheel')
    require(sha(wheel) == expected_sha256, 'Current wheel file differs from bound SHA256')
    require(isinstance(direct, dict), 'Malformed installed direct_url')
    archive = direct.get('archive_info', {})
    require(isinstance(archive, dict), 'Malformed installed archive_info')
    hashes = archive.get('hashes', {})
    require(isinstance(hashes, dict), 'Malformed installed archive hashes')
    legacy = archive.get('hash')
    recorded = []
    if hashes:
        require('sha256' in hashes, 'Recorded wheel hashes omit SHA256')
        recorded.append(hashes['sha256'])
    if legacy is not None:
        require(isinstance(legacy, str) and legacy.startswith('sha256='),
                'Unsupported recorded legacy wheel hash')
        recorded.append(legacy.split('=', 1)[1])
    if recorded:
        require(all(value == expected_sha256 for value in recorded),
                'Installed wheel recorded hash differs from current wheel')
        route = 'DIRECT_URL_RECORDED_SHA256'
    else:
        require(direct.get('url') == wheel.as_uri(),
                'Hashless installed wheel URI is not exact current wheel file URI')
        route = 'DIRECT_URL_EXACT_FILE_URI_AND_BOUND_CURRENT_SHA256'
    return {'route': route, 'boundWheelSHA256': expected_sha256,
            'currentWheelSHA256': sha(wheel), 'recordedSHA256Count': len(recorded),
            'exactFileURIMatched': direct.get('url') == wheel.as_uri()}


def early_controls(lane, root):
    """Real no-dependency pip target install plus same-checker negative controls."""
    root = Path(root)
    root.mkdir()
    name, version = 'candidate_wheel_identity_control', '0.0.1'
    prefix = name + '-' + version + '.dist-info/'
    wheel = root / (name + '-' + version + '-py3-none-any.whl')
    members = {
        name + '/__init__.py': b'VALUE = 1\n',
        prefix + 'METADATA': ('Metadata-Version: 2.1\nName: ' + name +
                              '\nVersion: ' + version + '\n\n').encode(),
        prefix + 'WHEEL': b'Wheel-Version: 1.0\nGenerator: owned-control\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    records = []
    for member, data in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        records.append((member, 'sha256=' + digest, len(data)))
    records.append((prefix + 'RECORD', '', ''))
    stream = io.StringIO(newline='')
    csv.writer(stream, lineterminator='\n').writerows(records)
    members[prefix + 'RECORD'] = stream.getvalue().encode()
    with zipfile.ZipFile(wheel, 'w') as archive:
        for member, data in members.items():
            archive.writestr(member, data)
    expected = sha(wheel)
    target = root / 'installed-target'
    require(not target.exists(), 'Tiny wheel target replay')
    lane.command('candidate-wheel-real-pip-control',
                 ['python', '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-compile',
                  '--target', str(target), str(wheel)], 45, cwd=root)
    distributions = list(importlib.metadata.distributions(path=[str(target)]))
    require(len(distributions) == 1 and distributions[0].metadata['Name'] == name and
            distributions[0].version == version, 'Wrong actual tiny installed distribution')
    raw = distributions[0].read_text('direct_url.json')
    require(raw is not None, 'Real local pip omitted direct_url entirely')
    direct = json.loads(raw)
    (root / 'actual-direct-url.json').write_text(raw)
    actual = verify(direct, wheel, expected)
    require((target / name / '__init__.py').read_bytes() == members[name + '/__init__.py'],
            'Real tiny installed module differs')
    hashless = {'url': wheel.resolve().as_uri(), 'archive_info': {}}
    fallback = verify(hashless, wheel, expected)
    cases = ['actual-local-pip-install', 'hashless-exact-current-uri']
    controls = [
        ('missing-uri', {'archive_info': {}}),
        ('wrong-uri', {'url': (root / 'other.whl').resolve().as_uri(), 'archive_info': {}}),
        ('non-file-uri', {'url': 'https://example.invalid/control.whl', 'archive_info': {}}),
        ('wrong-recorded-sha256', {'url': wheel.resolve().as_uri(),
                                  'archive_info': {'hashes': {'sha256': '0' * 64}}}),
        ('conflicting-recorded-sha256', {'url': wheel.resolve().as_uri(),
             'archive_info': {'hashes': {'sha256': expected}, 'hash': 'sha256=' + '0' * 64}}),
    ]
    for label, metadata in controls:
        try:
            verify(metadata, wheel, expected)
        except RuntimeError:
            cases.append(label)
        else:
            raise RuntimeError('Accepted invalid wheel identity control: ' + label)
    try:
        verify(hashless, wheel, '0' * 64)
    except RuntimeError:
        cases.append('wrong-bound-current-file-sha256')
    else:
        raise RuntimeError('Accepted mismatching bound current wheel file')
    result = {'state': 'ACTUAL_LOCAL_PIP_AND_SAME_CHECKER_CONTROLS',
              'actualDirectURL': direct, 'actualIdentity': actual,
              'syntheticHashlessMetadataControl': fallback,
              'cases': cases, 'caseCount': len(cases), 'projectTestsRun': False,
              'wheelSHA256': expected, 'pipCommandLabel': 'candidate-wheel-real-pip-control',
              'checkerSourceSHA256': sha(Path(__file__))}
    (root / 'wheel-identity-controls.json').write_text(json.dumps(result, indent=2))
    return result
