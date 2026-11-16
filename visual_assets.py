"""UNRUN bounded private HTML/static-asset closure. Never serves or opens a browser."""
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import stat
import urllib.parse
import zipfile
from docs_contract import require, sha

MAX_BYTES = 24 * 1024**2
MAX_FILES = 2048
MAX_FILE_BYTES = 8 * 1024**2


MAX_DIAGNOSTIC_BYTES = 2 * 1024**2


def build_asset_inventory(build, out):
    """Stat-only complete scan totals, with explicitly bounded/truncated detail rows."""
    rows, row_bytes, entries, regular_files, regular_bytes = [], 0, 0, 0, 0
    symlinks, errors, rows_truncated = 0, [], False
    for path in sorted((build / 'assets').rglob('*')):
        relative = str(path.relative_to(build))
        try:
            info = path.lstat()
        except OSError as error:
            if len(errors) < 16:
                errors.append({'path': relative, 'errorClass': type(error).__name__})
            continue
        entries += 1
        link = stat.S_ISLNK(info.st_mode)
        kind = ('symlink' if link else 'regular' if stat.S_ISREG(info.st_mode)
                else 'directory' if stat.S_ISDIR(info.st_mode) else 'other')
        symlinks += int(link)
        if kind == 'regular':
            regular_files += 1
            regular_bytes += info.st_size
        row = {'path': relative, 'size': info.st_size, 'filetype': kind, 'symlink': link}
        encoded = len(json.dumps(row).encode()) + 2
        if row_bytes + encoded <= MAX_DIAGNOSTIC_BYTES - 8192:
            rows.append(row)
            row_bytes += encoded
        else:
            rows_truncated = True
    value = {'stage': 'STAT_ONLY_WHOLE_BUILD_ASSETS_BEFORE_ADMISSION',
             'scanTotalsComplete': not errors, 'entryCount': entries,
             'regularFileCount': regular_files, 'regularFileBytes': regular_bytes,
             'symlinkCount': symlinks, 'rows': rows, 'rowsTruncated': rows_truncated,
             'rowsRetained': len(rows), 'statErrors': errors,
             'noAssetBodiesRead': True, 'maximumJSONBytes': MAX_DIAGNOSTIC_BYTES}
    raw = json.dumps(value, separators=(',', ':')).encode()
    require(len(raw) <= MAX_DIAGNOSTIC_BYTES, 'VISUAL_INVENTORY_JSON_CAP')
    (out / 'docs-visual-assets-build-inventory.json').write_bytes(raw)
    require(not errors, 'VISUAL_ASSET_STAT_SCAN_INCOMPLETE')
    return value


class References(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag in ('script', 'img', 'iframe', 'source') and attrs.get('src'):
            self.urls.append(attrs['src'])
        if tag == 'link' and any(x in attrs.get('rel', '').split() for x in
                                 ('stylesheet', 'icon', 'preload', 'modulepreload')):
            if attrs.get('href'):
                self.urls.append(attrs['href'])


def export(build, pages, out, executed_html=None):
    build = build.resolve(strict=True)
    require(len(pages) == 2 and len(set(pages)) == 2, 'EXACT_TWO_PRODUCTION_PAGES')
    build_asset_inventory(build, out)
    selected, queue, external, unbound, previews, total = {}, [], [], [], [], 0

    def add(path, destination, override=None):
        nonlocal total
        require(not any(p.is_symlink() for p in (path, *path.parents)), 'VISUAL_SYMLINK')
        path = path.resolve(strict=True)
        require(path.is_file(), 'VISUAL_NOT_REGULAR')
        require('..' not in PurePosixPath(destination).parts and not destination.startswith('/'),
                'VISUAL_ARCHIVE_PATH')
        if destination in selected:
            require(selected[destination][0] == path, 'VISUAL_DESTINATION_COLLISION')
            return
        size = len(override) if override is not None else path.stat().st_size
        failed = {'oneFileBytes': size > MAX_FILE_BYTES,
                  'aggregateBytes': total + size > MAX_BYTES,
                  'fileCount': len(selected) >= MAX_FILES}
        if any(failed.values()):
            diagnostic = {'complete': False, 'stage': 'ADD_ADMISSION_CAP_REFUSED_BEFORE_ZIP',
                          'failure': 'VISUAL_CLOSURE_CAP', 'failedCapFlags': failed,
                          'failingBuildRelativePath': str(path.relative_to(build)),
                          'failingDestination': destination, 'statBytes': path.stat().st_size,
                          'candidateBytes': size, 'acceptedCount': len(selected),
                          'acceptedBytes': total, 'prospectiveCount': len(selected) + 1,
                          'prospectiveBytes': total + size,
                          'limits': {'oneFileBytes': MAX_FILE_BYTES, 'bytes': MAX_BYTES,
                                     'files': MAX_FILES},
                          'zipCreated': False, 'files': {}, 'rowsTruncated': False}
            for name, (_, row, _) in selected.items():
                diagnostic['files'][name] = row
            raw = json.dumps(diagnostic, separators=(',', ':')).encode()
            if len(raw) > MAX_DIAGNOSTIC_BYTES:
                diagnostic['files'] = {}
                diagnostic['rowsTruncated'] = True
                raw = json.dumps(diagnostic, separators=(',', ':')).encode()
            require(len(raw) <= MAX_DIAGNOSTIC_BYTES, 'VISUAL_CAP_DIAGNOSTIC_JSON_CAP')
            (out / 'docs-visual-assets.json').write_bytes(raw)
            raise RuntimeError('VISUAL_CLOSURE_CAP')
        total += size
        selected[destination] = (path, {'bytes': size, 'sha256': sha(override if override is not None else path.read_bytes())}, override)
        queue.append((path, destination))

    for page in pages:
        require(page.resolve(strict=True).is_relative_to(build), 'PAGE_OUTSIDE_BUILD')
        original = page.read_bytes()
        text = original.decode()
        require(text.count('<head>') == 1, 'EXACT_PREVIEW_HEAD_INSERTION')
        csp = ("<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
               "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data:; font-src 'self' data:; connect-src 'none'; "
               "frame-src 'none'; base-uri 'self'; form-action 'none'\">")
        preview = text.replace('<head>', '<head>' + csp, 1).encode()
        relative = str(page.relative_to(build))
        add(page, 'original-production/' + relative + '.txt')
        add(page, 'SynapseML/' + relative, preview)
        previews.append({'originalSHA256': sha(original), 'previewSHA256': sha(preview),
                         'path': 'SynapseML/' + relative, 'adaptation': 'CSP-only private preview transport; original bytes retained as .txt'})
    # Webpack runtime constructs async chunk URLs; include the whole bounded
    # local asset tree rather than guessing chunk names from concatenated JS.
    for path in sorted((build / 'assets').rglob('*')):
        if path.is_file():
            add(path, 'SynapseML/' + str(path.relative_to(build)))
    if executed_html is not None:
        add(executed_html, 'executed-notebook.html')
    cursor = 0
    while cursor < len(queue):
        path, destination = queue[cursor]
        cursor += 1
        if destination.startswith('original-production/') or path.suffix.lower() not in ('.html', '.css'):
            continue
        text = path.read_text(errors='strict')
        if path.suffix.lower() == '.html':
            parser = References()
            parser.feed(text)
            urls = parser.urls
        else:
            urls = [m.group(1).strip(' \'"') for m in re.finditer(r'url\(([^)]+)\)', text)]
            urls += [m.group(1) for m in re.finditer(r'@import\s+[\'"]([^\'"]+)[\'"]', text)]
        for raw in urls:
            if raw.startswith(('data:', '#')):
                continue
            url = urllib.parse.urlsplit(raw)
            if url.scheme or url.netloc:
                if url.hostname != 'microsoft.github.io' or url.scheme != 'https':
                    known_analytics = raw == 'https://www.googletagmanager.com/gtag/js?id=G-RWPE0183E8'
                    record = {'referrer': destination, 'urlSHA256': sha(raw.encode()),
                              'scheme': url.scheme, 'host': url.hostname,
                              'blockedByPreviewCSP': known_analytics}
                    external.append(record)
                    if not known_analytics:
                        unbound.append(record)
                    continue
            decoded = urllib.parse.unquote(url.path)
            if decoded.startswith('/SynapseML/'):
                relative = PurePosixPath(decoded[len('/SynapseML/'):])
            elif decoded.startswith('/'):
                raise RuntimeError('ASSET_OUTSIDE_REVIEWED_BASEURL')
            else:
                require(destination != 'executed-notebook.html', 'EXTERNAL_NOTEBOOK_ASSET_UNBOUND')
                relative = PurePosixPath(str(path.relative_to(build).parent)) / decoded
            require(not relative.is_absolute(), 'ASSET_TRAVERSAL')
            asset = build.joinpath(*relative.parts)
            require(asset.resolve(strict=True).is_relative_to(build), 'ASSET_ESCAPES_BUILD')
            safe_relative = asset.resolve(strict=True).relative_to(build)
            add(asset, 'SynapseML/' + str(safe_relative))
    inventory = {'complete': not unbound, 'fileCount': len(selected), 'bytes': total,
                 'files': {name: row for name, (_, row, _) in selected.items()},
                 'activeExternalReferences': external, 'unboundExternalReferences': unbound,
                 'previewTransportAdaptations': previews,
                 'previewOnlyCSP': True, 'originalProductionBytesRetainedAsNonExecutingText': True,
                 'limits': {'bytes': MAX_BYTES, 'files': MAX_FILES, 'oneFileBytes': MAX_FILE_BYTES},
                 'browserStarted': False, 'publicUpload': False,
                 'previewRequiresPrivacyReview': True}
    (out / 'docs-visual-assets.json').write_text(json.dumps(inventory, indent=2))
    with zipfile.ZipFile(out / 'docs-visual-assets.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, (path, _, override) in sorted(selected.items()):
            archive.writestr(name, override if override is not None else path.read_bytes())
    # Preserve available evidence before refusing an incomplete offline closure.
    require(not unbound, 'ACTIVE_EXTERNAL_ASSET_CLOSURE_UNBOUND_NO_BROWSER')
    return inventory


def fixture():
    import tempfile
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        build = root / 'build'
        (build / 'assets/css').mkdir(parents=True)
        (build / 'assets/fonts').mkdir()
        (build / 'assets/fonts/test.woff2').write_bytes(b'owned-font-fixture')
        (build / 'assets/css/main.css').write_text('@font-face{src:url(../fonts/test.woff2)}')
        pages = [build / 'book.html', build / 'quick.html']
        html = ('<!doctype html><html><head><link rel="stylesheet" href="/SynapseML/assets/css/main.css">'
                '<script src="https://www.googletagmanager.com/gtag/js?id=G-RWPE0183E8"></script>'
                '</head><body>Owned visual fixture</body></html>')
        for page in pages:
            page.write_text(html)
        good = root / 'good'
        good.mkdir()
        result = export(build, pages, good)
        require(result['complete'] and result['previewOnlyCSP'] and
                len(result['previewTransportAdaptations']) == 2, 'VISUAL_POSITIVE')
        scan = json.loads((good / 'docs-visual-assets-build-inventory.json').read_text())
        require(scan['scanTotalsComplete'] and not scan['rowsTruncated'] and
                scan['regularFileCount'] == 2, 'VISUAL_POSITIVE_WHOLE_STAT_INVENTORY')
        pages[0].write_text(html.replace('www.googletagmanager.com/gtag/js?id=G-RWPE0183E8',
                                        'example.invalid/unknown.js'))
        bad = root / 'bad'
        bad.mkdir()
        try:
            export(build, pages, bad)
        except RuntimeError as error:
            require('ACTIVE_EXTERNAL_ASSET_CLOSURE_UNBOUND' in str(error), 'WRONG_VISUAL_FAILURE')
        else:
            raise RuntimeError('VISUAL_EXTERNAL_NEGATIVE_ACCEPTED')
        require((bad / 'docs-visual-assets.json').is_file(), 'EVIDENCE_BEFORE_REFUSAL')
    cap_controls = []
    # Bounded real files, never a raised limit or a mocked stat result.
    for label, sizes, flag in [
            ('leaf', [MAX_FILE_BYTES + 1], 'oneFileBytes'),
            ('aggregate', [7 * 1024**2] * 4, 'aggregateBytes'),
            ('count', [1] * 2045, 'fileCount')]:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            build, out = root / 'build', root / 'out'
            (build / 'assets').mkdir(parents=True)
            out.mkdir()
            pages = [build / 'book.html', build / 'quick.html']
            for page in pages:
                page.write_text('<html><head></head><body>Owned cap control</body></html>')
            for index, size in enumerate(sizes):
                # truncate creates bounded filesystem objects without large in-memory buffers.
                with (build / 'assets' / ('asset-' + str(index).zfill(4))).open('xb') as stream:
                    stream.truncate(size)
            try:
                export(build, pages, out)
            except RuntimeError as error:
                require(str(error) == 'VISUAL_CLOSURE_CAP', 'WRONG_CAP_CONTROL_FAILURE:' + label)
            else:
                raise RuntimeError('VISUAL_CAP_CONTROL_ACCEPTED:' + label)
            failure = json.loads((out / 'docs-visual-assets.json').read_text())
            inventory = json.loads((out / 'docs-visual-assets-build-inventory.json').read_text())
            require(failure['failedCapFlags'][flag] and not failure['complete'] and
                    failure['zipCreated'] is False and not (out / 'docs-visual-assets.zip').exists(),
                    'VISUAL_CAP_CONTROL_DIAGNOSTICS:' + label)
            require(inventory['scanTotalsComplete'] and
                    inventory['regularFileCount'] == len(sizes) and
                    inventory['regularFileBytes'] == sum(sizes),
                    'VISUAL_CAP_CONTROL_WHOLE_SCAN_TOTALS:' + label)
            require((out / 'docs-visual-assets.json').stat().st_size <= MAX_DIAGNOSTIC_BYTES and
                    (out / 'docs-visual-assets-build-inventory.json').stat().st_size <=
                    MAX_DIAGNOSTIC_BYTES, 'VISUAL_CONTROL_METADATA_CAP:' + label)
            cap_controls.append(label)
    return {'positive': 1, 'externalNegative': 1, 'relativeFontClosure': True,
            'realFilesystemCapNegatives': cap_controls, 'projectCode': False}
