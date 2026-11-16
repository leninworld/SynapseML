"""UNRUN bounded private HTML/static-asset closure. Never serves or opens a browser."""
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import urllib.parse
import zipfile
from docs_contract import require, sha

MAX_BYTES = 24 * 1024**2
MAX_FILES = 2048
MAX_FILE_BYTES = 8 * 1024**2


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
        require(size <= MAX_FILE_BYTES and total + size <= MAX_BYTES and
                len(selected) < MAX_FILES, 'VISUAL_CLOSURE_CAP')
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
    return {'positive': 1, 'externalNegative': 1, 'relativeFontClosure': True, 'projectCode': False}
