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
    require(len(books) == len(quicks) == 1, 'MISSING_OR_AMBIGUOUS_DML_ARTICLES')
    for kind, (path, page) in [('notebook', books[0]), ('quick', quicks[0])]:
        require(page.canonical, 'NO_CANONICAL_ROUTE')
        for token in ['T1', 'T2', 'synthetic', 'bootstrap', 'multiplicity']:
            require(token in page.article_text, 'MISSING_DML_ARTICLE_CONTENT:' + kind + ':' + token)
        require(any(normalized(page.canonical, link['href']) == normalized(page.canonical, '')
                    for link in page.sidebar_links), 'CURRENT_ARTICLE_SIDEBAR_ROUTE_MISSING')
        if diagnostic_root is not None:
            data = path.read_bytes()
            require(len(data) <= 2 * 1024**2, 'ARTICLE_EVIDENCE_CAP')
            (diagnostic_root / (kind + '.html')).write_bytes(data)
    return books[0][0], quicks[0][0], urllib.parse.urlsplit(books[0][1].canonical).path


def fixture():
    import tempfile
    def html(marker, route):
        return ('<link rel="canonical" href="https://example.invalid' + route + '">'
                '<aside><a href="' + route + '">Current</a></aside><article>' +
                marker.replace('continuous', '<code>continuous</code>') +
                ' T1 T2 synthetic bootstrap multiplicity</article>')
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        a, b = root / 'book.html', root / 'quick.html'
        a.write_text(html(BOOK_MARKER, '/SynapseML/book/'))
        b.write_text(html(QUICK_MARKER, '/SynapseML/quick/'))
        inspect_build(root)
        b.write_text('<aside>' + QUICK_MARKER + '</aside><article>Unrelated</article>')
        try:
            inspect_build(root)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('NAVIGATION_ONLY_FALSE_POSITIVE')
        b.write_text(html(QUICK_MARKER, '/SynapseML/quick/'))
        (root / 'duplicate.html').write_bytes(b.read_bytes())
        try:
            inspect_build(root)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('DUPLICATE_ARTICLE_FALSE_POSITIVE')
    return {'positive': 1, 'negative': 2, 'controlsOnly': True}
