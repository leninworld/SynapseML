"""Private unrun DML adaptation of corrected874 normalized article reader."""
from html.parser import HTMLParser
from pathlib import Path
import json
import urllib.parse
from docs_contract import require
BOOK_MARKER = 'Simultaneous continuous treatments (synthetic example)'
QUICK_MARKER = 'Multiple continuous treatments'

class Page(HTMLParser):
    def __init__(self,text):
        super().__init__(convert_charrefs=True);self.article=0;self.aside=0;self.text=[];self.article_links=[];self.sidebar_links=[];self.canonical=None;self.anchor=None;self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='article':self.article+=1
        if tag=='aside':self.aside+=1
        if tag=='link' and a.get('rel')=='canonical':self.canonical=a.get('href')
        if tag=='a' and (self.article or self.aside):self.anchor={'href':a.get('href',''),'text':[],'article':bool(self.article),'aside':bool(self.aside)}
    def handle_data(self,data):
        if self.article:self.text.append(data)
        if self.anchor:self.anchor['text'].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor:
            item={'href':self.anchor['href'],'text':' '.join(''.join(self.anchor['text']).split())}
            if self.anchor['article']:self.article_links.append(item)
            if self.anchor['aside']:self.sidebar_links.append(item)
            self.anchor=None
        if tag=='article':self.article-=1
        if tag=='aside':self.aside-=1
    @property
    def article_text(self):return ' '.join(''.join(self.text).split())

def normalized(base,href):return urllib.parse.unquote(urllib.parse.urlsplit(urllib.parse.urljoin(base,href)).path).rstrip('/')

def inspect_build(root, diagnostic_root=None):
    books, quicks, inventory = [], [], []
    for path in sorted(Path(root).rglob('*.html')):
        page = Page(path.read_text(errors='replace'))
        inventory.append(str(path.relative_to(root)))
        if BOOK_MARKER in page.article_text:
            books.append((path, page))
        if QUICK_MARKER in page.article_text:
            quicks.append((path, page))
    if diagnostic_root is not None:
        diagnostic_root = Path(diagnostic_root)
        diagnostic_root.mkdir(parents=True, exist_ok=True)
        (diagnostic_root / 'article-candidates.json').write_text(json.dumps({
            'allHTMLPaths': inventory,
            'bookCandidates': [str(x[0].relative_to(root)) for x in books],
            'quickCandidates': [str(x[0].relative_to(root)) for x in quicks]}, indent=2))
    checks = []
    # Persist BOTH selected HTML documents and structural diagnostics before assertions.
    # The explicit upstream sidebar includes the notebook, not Quick Examples aggregate pages.
    for kind, candidates in [('notebook', books), ('quick', quicks)]:
        for index, (path, page) in enumerate(candidates):
            data = path.read_bytes()
            current_sidebar = any(normalized(page.canonical or '', link['href']) ==
                                  normalized(page.canonical or '', '')
                                  for link in page.sidebar_links)
            canonical_path = normalized(page.canonical or '', '')
            expected_suffix = ('/docs/next/Explore Algorithms/Causal Inference/'
                               'Quickstart - Measure Causal Effects' if kind == 'notebook'
                               else '/docs/next/Quick Examples/estimators/estimators_causal')
            row = {'kind': kind, 'path': str(path.relative_to(root)), 'bytes': len(data),
                   'canonical': page.canonical, 'canonicalPath': canonical_path,
                   'expectedCurrentRouteSuffix': expected_suffix,
                   'canonicalMatchesExpectedRoute': canonical_path.endswith(expected_suffix),
                   'sidebarRequiredByUpstreamConfiguration': kind == 'notebook',
                   'currentArticleSidebarRoutePresent': current_sidebar,
                   'sidebarLinks': page.sidebar_links, 'articleLinks': page.article_links,
                   'contentTokens': {token: token in page.article_text for token in
                                     ['T1', 'T2', 'synthetic', 'bootstrap', 'multiplicity']},
                   'htmlEvidenceWithinCap': len(data) <= 2 * 1024**2}
            checks.append(row)
            if diagnostic_root is not None and row['htmlEvidenceWithinCap']:
                filename = kind + ('' if len(candidates) == 1 else '-' + str(index)) + '.html'
                (diagnostic_root / filename).write_bytes(data)
    if diagnostic_root is not None:
        (diagnostic_root / 'article-checks.json').write_text(json.dumps(checks, indent=2))
    require(len(books) == len(quicks) == 1, 'MISSING_OR_AMBIGUOUS_DML_ARTICLES')
    for row in checks:
        require(row['htmlEvidenceWithinCap'], 'ARTICLE_EVIDENCE_CAP')
        require(row['canonical'], 'NO_CANONICAL_ROUTE')
        require(row['canonicalMatchesExpectedRoute'], 'WRONG_CURRENT_DML_CANONICAL_ROUTE')
        for token, present in row['contentTokens'].items():
            require(present, 'MISSING_DML_ARTICLE_CONTENT:' + row['kind'] + ':' + token)
        if row['sidebarRequiredByUpstreamConfiguration']:
            require(row['currentArticleSidebarRoutePresent'],
                    'CURRENT_ARTICLE_SIDEBAR_ROUTE_MISSING')
    return books[0][0], quicks[0][0], urllib.parse.urlsplit(books[0][1].canonical).path



def fixture():
    import tempfile
    book_route = '/SynapseML/docs/next/Explore Algorithms/Causal Inference/Quickstart - Measure Causal Effects/'
    quick_route = '/SynapseML/docs/next/Quick Examples/estimators/estimators_causal/'
    def html(marker, route, sidebar=True):
        return ('<link rel="canonical" href="https://example.invalid' + route + '">' +
                ('<aside><a href="' + route + '">Current</a></aside>' if sidebar else '') + '<article>' +
                marker.replace('continuous', '<code>continuous</code>') +
                ' T1 T2 synthetic bootstrap multiplicity</article>')
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        a, b = root / 'book.html', root / 'quick.html'
        a.write_text(html(BOOK_MARKER, book_route))
        b.write_text(html(QUICK_MARKER, quick_route, sidebar=False))
        inspect_build(root)
        b.write_text('<aside>' + QUICK_MARKER + '</aside><article>Unrelated</article>')
        try:
            inspect_build(root)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('NAVIGATION_ONLY_FALSE_POSITIVE')
        b.write_text(html(QUICK_MARKER, quick_route, sidebar=False))
        a.write_text(html(BOOK_MARKER, book_route, sidebar=False))
        try:
            inspect_build(root, root / 'diagnostics')
        except RuntimeError as error:
            require(str(error) == 'CURRENT_ARTICLE_SIDEBAR_ROUTE_MISSING', 'WRONG_SIDEBAR_NEGATIVE')
            require((root / 'diagnostics/quick.html').is_file() and
                    (root / 'diagnostics/article-checks.json').is_file(),
                    'BOTH_PAGES_NOT_CAPTURED_BEFORE_ASSERT')
        else:
            raise RuntimeError('MISSING_CONFIGURED_NOTEBOOK_SIDEBAR_ACCEPTED')
        # Diagnostics are outside the scanned build in real use; remove this synthetic copy.
        import shutil
        shutil.rmtree(root / 'diagnostics')
        a.write_text(html(BOOK_MARKER, book_route))
        b.write_text(html(QUICK_MARKER, '/SynapseML/docs/next/wrong-aggregate/', sidebar=False))
        try:
            inspect_build(root)
        except RuntimeError as error:
            require(str(error) == 'WRONG_CURRENT_DML_CANONICAL_ROUTE', 'WRONG_CANONICAL_NEGATIVE')
        else:
            raise RuntimeError('WRONG_QUICK_CANONICAL_ACCEPTED')
        b.write_text(html(QUICK_MARKER, quick_route, sidebar=False))
        (root / 'duplicate.html').write_bytes(b.read_bytes())
        try:
            inspect_build(root)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('DUPLICATE_ARTICLE_FALSE_POSITIVE')
    return {'positive': 1, 'negative': 4, 'controlsOnly': True}
