"""Root-only wheel metadata selection and matching pre-download ZIP fixtures."""
import io
import email
import re
import zipfile


def normalize(value):
    return re.sub(r'[-_.]+', '_', value).casefold()


def select_metadata(wheel_filename, archive, record):
    pieces = wheel_filename.removesuffix('.whl').split('-')
    paths = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
    roots = [name for name in paths if re.fullmatch(r'([^/\\]+)\.dist-info/METADATA', name)]
    evidence = {'wheel_filename': wheel_filename, 'all_metadata_paths': paths,
                'root_candidates': roots, 'nested_metadata_ignored': [name for name in paths if name not in roots],
                'selected': roots[0] if len(roots) == 1 else None}
    # This callback must persist evidence BEFORE any count/name assertion.
    record(evidence)
    if not wheel_filename.endswith('.whl') or len(pieces) not in (5, 6):
        raise ValueError('Invalid wheel filename: ' + wheel_filename)
    if len(roots) != 1:
        raise ValueError(f'{wheel_filename}: expected one root METADATA, got {len(roots)}')
    expected = pieces[0] + '-' + pieces[1]
    actual = roots[0].split('/')[0].removesuffix('.dist-info')
    if normalize(actual) != normalize(expected):
        raise ValueError(f'{wheel_filename}: root dist-info identity mismatch: {actual}')
    return archive.read(roots[0])


def fixture_zip(paths):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for path in paths:
            archive.writestr(path, b'Name: test-package\nVersion: 1.0\n\n')
    return buffer.getvalue()


def exercise_zip_fixtures(record):
    filename = 'test_package-1.0-py3-none-any.whl'
    root = 'test_package-1.0.dist-info/METADATA'
    nested = 'pkg/_vendor/x-1.0.dist-info/METADATA'
    cases = [
        ('root-plus-vendor', [root, nested], None),
        ('zero-root', [nested], 'got 0'),
        ('two-roots', [root, 'other-2.0.dist-info/METADATA'], 'got 2'),
        ('wrong-root-identity', ['other-2.0.dist-info/METADATA'], 'identity mismatch'),
    ]
    results = []
    for name, paths, expected_error in cases:
        observed = []
        def save(item):
            observed.append(item)
            record({'fixture': name, **item})
        with zipfile.ZipFile(io.BytesIO(fixture_zip(paths))) as archive:
            try:
                raw = select_metadata(filename, archive, save)
            except ValueError as exc:
                if expected_error is None or expected_error not in str(exc):
                    raise
            else:
                if expected_error is not None:
                    raise AssertionError('Missing expected negative outcome: ' + name)
                metadata = email.message_from_bytes(raw)
                assert metadata['Name'] == 'test-package' and metadata['Version'] == '1.0'
                assert observed[-1]['nested_metadata_ignored'] == [nested]
        assert len(observed) == 1, 'Selection evidence not recorded before outcome'
        results.append({'fixture': name, 'status': 'PASS'})
    return results
