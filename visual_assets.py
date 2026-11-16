"""UNRUN bounded private HTML/static-asset closure. Never serves or opens a browser."""
from html.parser import HTMLParser
import hashlib
import os
import json
from pathlib import Path, PurePosixPath
import re
import stat
import urllib.parse
import zipfile
from docs_contract import require, sha

MAX_BYTES = 36 * 1024**2  # Expanded read/decode ceiling: NEW admission required.
MAX_ZIP_BYTES = 24 * 1024**2  # Compressed ZIP transport ceiling, not the old raw gate.
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
    inventory['compression'] = {'method': 'ZIP_DEFLATED', 'level': 9,
                                'compressedLimitBytes': MAX_ZIP_BYTES,
                                'expandedLimitBytes': MAX_BYTES,
                                'losslessVerificationRequired': True,
                                'ratioMeasured': False}
    inventory['publicUpload'] = True
    inventory['publicUploadQualification'] = 'Intended public Actions evidence; not emitted until unchanged whole-export preflight.'
    (out / 'docs-visual-assets.json').write_text(json.dumps(inventory, indent=2))
    write_verified_zip(selected, out, inventory)
    # Preserve available evidence before refusing an incomplete offline closure.
    require(not unbound, 'ACTIVE_EXTERNAL_ASSET_CLOSURE_UNBOUND_NO_BROWSER')
    return inventory


class CappedZipWriter:
    """Bound every ZIP write/central-directory rewrite before touching disk."""
    def __init__(self, stream, cap):
        self.stream, self.cap = stream, cap

    def write(self, data):
        require(self.stream.tell() + len(data) <= self.cap, 'VISUAL_COMPRESSED_ZIP_CAP')
        return self.stream.write(data)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def verify_zip(path, inventory):
    """Data-only lossless verification; never execute or extract archive members."""
    require(path.is_file() and not path.is_symlink() and
            path.stat().st_size <= MAX_ZIP_BYTES, 'VISUAL_COMPRESSED_ZIP_CAP')
    expected = inventory['files']
    require(len(expected) <= MAX_FILES and
            sum(row['bytes'] for row in expected.values()) <= MAX_BYTES,
            'VISUAL_DECODE_EXPANDED_CAP')
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        require(len(infos) == len(expected) and len(infos) <= MAX_FILES,
                'VISUAL_DECODE_FILE_COUNT')
        names = [info.filename for info in infos]
        require(len(set(names)) == len(names) and set(names) == set(expected),
                'VISUAL_DECODE_NAMES')
        measured = 0
        for info in infos:
            name = info.filename
            require(not PurePosixPath(name).is_absolute() and
                    '..' not in PurePosixPath(name).parts and '\\' not in name,
                    'VISUAL_DECODE_PATH')
            require(not info.is_dir() and not (info.flag_bits & 1) and
                    stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG and
                    info.compress_type == zipfile.ZIP_DEFLATED,
                    'VISUAL_DECODE_TYPE')
            row = expected[name]
            require(info.file_size == row['bytes'] and info.file_size <= MAX_FILE_BYTES,
                    'VISUAL_DECODE_LEAF_SIZE')
            digest, count = hashlib.sha256(), 0
            with archive.open(info) as source:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    count += len(chunk)
                    measured += len(chunk)
                    require(count <= row['bytes'] and count <= MAX_FILE_BYTES and
                            measured <= MAX_BYTES, 'VISUAL_DECODE_STREAM_CAP')
                    digest.update(chunk)
            require(count == row['bytes'] and digest.hexdigest() == row['sha256'],
                    'VISUAL_DECODE_HASH')
    require(measured == inventory['bytes'], 'VISUAL_DECODE_TOTAL')
    return {'verifiedFiles': len(expected), 'verifiedExpandedBytes': measured,
            'zipBytes': path.stat().st_size, 'zipSHA256': sha(path.read_bytes()),
            'everyDecodedByteHashMatched': True, 'extractedOrExecuted': False}


def write_verified_zip(selected, out, inventory):
    partial = out / 'docs-visual-assets.zip.partial'
    try:
        with partial.open('xb') as stream:
            with zipfile.ZipFile(CappedZipWriter(stream, MAX_ZIP_BYTES), 'w',
                                 compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, (path, _, override) in sorted(selected.items()):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.external_attr = 0o100600 << 16
                    archive.writestr(info, override if override is not None else path.read_bytes(),
                                     compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        proof = verify_zip(partial, inventory)
        final = out / 'docs-visual-assets.zip'
        require(not final.exists(), 'VISUAL_ZIP_DESTINATION_EXISTS')
        partial.rename(final)
        inventory['compression'].update(proof)
        inventory['compression']['ratioMeasured'] = True
        inventory['compression']['zipOverExpandedRatio'] = proof['zipBytes'] / max(1, inventory['bytes'])
    except Exception as error:
        inventory['complete'] = False
        inventory['compression']['failureClass'] = type(error).__name__
        inventory['compression']['failure'] = str(error)
        inventory['compression']['partialBytes'] = partial.stat().st_size if partial.exists() else 0
        inventory['compression']['finalZipCreated'] = False
        (out / 'docs-visual-assets.json').write_text(json.dumps(inventory, indent=2))
        raise
    (out / 'docs-visual-assets.json').write_text(json.dumps(inventory, indent=2))


def compression_controls():
    """Future same-Docker real-file controls, not executed by authoring."""
    import tempfile
    results = {}
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        def make(label, random):
            build, out = root / label / 'build', root / label / 'out'
            (build / 'assets').mkdir(parents=True)
            out.mkdir()
            pages = [build / 'book.html', build / 'quick.html']
            for page in pages:
                page.write_text('<html><head></head><body>Owned lossless control</body></html>')
            for index in range(4):
                with (build / 'assets' / str(index)).open('xb') as stream:
                    for _ in range(96):
                        stream.write(os.urandom(65536) if random else b'x' * 65536)
            return build, pages, out
        build, pages, out = make('compressible', False)
        good = export(build, pages, out)
        require(good['bytes'] > MAX_ZIP_BYTES and good['compression']['zipBytes'] < MAX_ZIP_BYTES,
                'VISUAL_COMPRESSION_REAL_BENEFIT_CONTROL')
        require(verify_zip(out / 'docs-visual-assets.zip', good)['everyDecodedByteHashMatched'],
                'VISUAL_COMPRESSION_ROUNDTRIP')
        results['over24MiBExpandedLosslessSuccess'] = True
        bad_hash = json.loads(json.dumps(good))
        bad_hash['files'][next(iter(bad_hash['files']))]['sha256'] = '0' * 64
        try:
            verify_zip(out / 'docs-visual-assets.zip', bad_hash)
        except RuntimeError as error:
            require(str(error) == 'VISUAL_DECODE_HASH', 'VISUAL_WRONG_HASH_CONTROL')
        else:
            raise RuntimeError('VISUAL_WRONG_HASH_ACCEPTED')
        results['wrongDecodedHashRejected'] = True
        # Real high-entropy bytes exceed compressed24MiB while expanded36MiB fits.
        build, pages, out = make('incompressible', True)
        try:
            export(build, pages, out)
        except RuntimeError as error:
            require(str(error) == 'VISUAL_COMPRESSED_ZIP_CAP', 'VISUAL_WRONG_ZIP_CAP_CONTROL')
        else:
            raise RuntimeError('VISUAL_COMPRESSED_OVERSIZE_ACCEPTED')
        receipt = json.loads((out / 'docs-visual-assets.json').read_text())
        require(not receipt['complete'] and not (out / 'docs-visual-assets.zip').exists() and
                receipt['compression']['partialBytes'] <= MAX_ZIP_BYTES,
                'VISUAL_COMPRESSED_FAILURE_RECEIPT')
        results['incompressibleCompressedCapRefused'] = True
        # Exercise decoder traversal and expanded-byte refusal using real ZIP metadata.
        evil = root / 'traversal.zip'
        with zipfile.ZipFile(evil, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('../escape', b'owned')
        inv = {'bytes': 5, 'files': {'../escape': {'bytes': 5, 'sha256': sha(b'owned')}}}
        try:
            verify_zip(evil, inv)
        except RuntimeError as error:
            require(str(error) == 'VISUAL_DECODE_PATH', 'VISUAL_WRONG_TRAVERSAL_CONTROL')
        else:
            raise RuntimeError('VISUAL_TRAVERSAL_ACCEPTED')
        results['traversalRejected'] = True
        over = json.loads(json.dumps(good))
        over['files'][next(iter(over['files']))]['bytes'] = MAX_BYTES + 1
        try:
            verify_zip(root / 'compressible/out/docs-visual-assets.zip', over)
        except RuntimeError as error:
            require(str(error) == 'VISUAL_DECODE_EXPANDED_CAP', 'VISUAL_WRONG_EXPANDED_CONTROL')
        else:
            raise RuntimeError('VISUAL_EXPANDED_OVERFLOW_ACCEPTED')
        results['expandedOverflowRejected'] = True
    return {'cases': results, 'caseCount': len(results), 'allPassed': True,
            'producer': 'real stdlib ZIP/filesystem, synthetic data only',
            'productionCompressionRatioMeasured': False}


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
            ('aggregate', [7 * 1024**2] * 6, 'aggregateBytes'),
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
            'realFilesystemCapNegatives': cap_controls, 'projectCode': False, 'compressionControls': compression_controls()}
