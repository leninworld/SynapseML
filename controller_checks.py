"""Production qualification guards with matching pre-download fixtures."""
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def make_wheel_lock(records):
    seen, lines = set(), []
    if not records:
        raise ValueError('Empty wheel closure')
    for record in records:
        name, version, digest = record['name'], record['version'], record['sha256']
        key = re.sub(r'[-_.]+', '-', name).lower()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
            raise ValueError('Invalid distribution name')
        if key in seen:
            raise ValueError('Duplicate normalized distribution: ' + name)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.!+_-]*', version):
            raise ValueError('Invalid distribution version')
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('Invalid wheel SHA256')
        seen.add(key)
        lines.append(name + '==' + version + ' --hash=sha256:' + digest)
    return '\n'.join(lines) + '\n'


def validate_status(raw, allowed):
    entries, result, i = raw.split(b'\0'), [], 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        if len(entry) < 4 or entry[2:3] != b' ':
            raise ValueError('Malformed porcelain status entry')
        status, path = entry[:2].decode(), entry[3:].decode()
        paths = [path]
        if 'R' in status or 'C' in status:
            if i >= len(entries) or not entries[i]:
                raise ValueError('Missing rename source')
            paths.append(entries[i].decode())
            i += 1
        for name in paths:
            if Path(name).is_absolute() or '..' in Path(name).parts or not name.startswith(allowed):
                raise ValueError('Unauthorized source change: ' + name)
        result.append({'status': status, 'paths': paths})
    return result


class PageIdentity(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title, self.title, self.canonical = False, '', None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title':
            self.in_title = True
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical = attrs.get('href')

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, text):
        if self.in_title:
            self.title += text


def inspect_page(text, expected_route):
    page = PageIdentity()
    page.feed(text)
    route_matches = bool(page.canonical and unquote(urlsplit(page.canonical).path) == expected_route)
    return {'title': page.title, 'canonical': page.canonical,
            'route_matches': route_matches, 'required_content': 'ICETransformer' in text,
            'is_target': route_matches and 'ICETransformer' in text}


def verify_projection(input_root, output_root, tracked):
    files = sorted(path for path in input_root.rglob('*') if path.is_file())
    if not files or any(path.is_symlink() for path in input_root.rglob('*')):
        raise ValueError('Empty or symlinked doc input')
    generated = {str(path.relative_to(input_root).with_suffix('.md') if path.suffix == '.ipynb'
                     else path.relative_to(input_root)) for path in files}
    if len(generated) != len(files):
        raise ValueError('Output filename collision')
    expected = generated | set(tracked)
    actual = {str(path.relative_to(output_root)) for path in output_root.rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('Incomplete or extra generated output: ' + repr(sorted(actual ^ expected)))
    for path in files:
        output = output_root / path.relative_to(input_root)
        if path.suffix == '.ipynb':
            output = output.with_suffix('.md')
            if not output.read_text().startswith('---\ntitle: '):
                raise ValueError('Missing generated frontmatter')
        elif hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(output.read_bytes()).digest():
            raise ValueError('Non-notebook copy differs')
    return {'input_files': len(files), 'output_files': len(actual),
            'notebooks': sum(path.suffix == '.ipynb' for path in files), 'tracked': sorted(tracked)}


def exercise_controller_fixtures(root):
    results = []
    good = {'name': 'test-package', 'version': '1.0', 'sha256': 'a' * 64}
    assert make_wheel_lock([good]) == 'test-package==1.0 --hash=sha256:' + 'a' * 64 + '\n'
    results.append('lock-positive')
    for label, rows in [('duplicate', [good, {**good, 'name': 'test_package'}]),
                        ('bad-hash', [{**good, 'sha256': 'bad'}]),
                        ('missing-version', [{**good, 'version': ''}])]:
        try:
            make_wheel_lock(rows)
        except ValueError:
            results.append('lock-negative-' + label)
        else:
            raise AssertionError('Lock negative fixture accepted: ' + label)
    allowed = ('website/docs/', 'website/node_modules/', 'website/build/', 'website/.docusaurus/')
    assert len(validate_status(b' M website/docs/a.md\0?? website/build/\0', allowed)) == 2
    assert validate_status(b'', allowed) == []
    results.append('status-positive-and-empty')
    for label, raw in [('outside', b' M docs/canonical.ipynb\0'),
                       ('rename-outside', b'R  website/docs/a.md\0docs/canonical.ipynb\0'),
                       ('malformed', b'R  website/docs/a.md\0')]:
        try:
            validate_status(raw, allowed)
        except ValueError:
            results.append('status-negative-' + label)
        else:
            raise AssertionError('Status negative fixture accepted: ' + label)
    route = '/SynapseML/docs/next/PDP and ICE Explainers/'
    html = '<title>PDP and ICE Explainers | SynapseML</title><link rel="canonical" href="https://microsoft.github.io/SynapseML/docs/next/PDP%20and%20ICE%20Explainers/"><code>ICETransformer</code>'
    assert inspect_page(html, route)['is_target']
    assert not inspect_page(html.replace('/docs/next/', '/docs/'), route)['is_target']
    assert not inspect_page(html.replace('ICETransformer', 'Other'), route)['is_target']
    assert not inspect_page('<title>PDP and ICE Explainers</title>', route)['is_target']
    results.append('page-positive-and-three-negatives')
    for case in ('positive', 'missing', 'extra', 'bad-copy', 'bad-prefix', 'collision', 'symlink'):
        base = root / ('projection-' + case)
        source, output = base / 'input', base / 'output'
        source.mkdir(parents=True)
        output.mkdir()
        (source / 'a.ipynb').write_text('{}')
        (source / 'note.txt').write_bytes(b'note')
        if case == 'collision':
            (source / 'a.md').write_text('collision')
        if case == 'symlink':
            (source / 'alias.txt').symlink_to('note.txt')
        (output / 'a.md').write_text('bad' if case == 'bad-prefix' else '---\ntitle: a\n')
        if case != 'missing':
            (output / 'note.txt').write_bytes(b'changed' if case == 'bad-copy' else b'note')
        (output / 'existing.md').write_text('tracked')
        if case == 'extra':
            (output / 'extra.md').write_text('extra')
        try:
            info = verify_projection(source, output, {'existing.md'})
        except ValueError:
            if case == 'positive':
                raise
        else:
            assert case == 'positive' and info['output_files'] == 3
        results.append('projection-' + case)
    return [{'fixture': name, 'status': 'PASS'} for name in results]
