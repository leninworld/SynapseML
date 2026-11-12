"""Shared production-page, article-link and sidebar contract, separate from dev liveness."""
from html.parser import HTMLParser
from pathlib import Path
import re,urllib.parse,json,tempfile
from validation_runtime import require
BOOK_MARKER='Categorical and missing inputs for a Python-trained model'
OVERVIEW_MARKER='A new StringIndexer fit is not a substitute for the Python training mapping.'

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

def verify_pair(book,overview):
    require(book.canonical and overview.canonical,'Missing canonical page routes')
    require(BOOK_MARKER in book.article_text and OVERVIEW_MARKER in overview.article_text,'Missing real article content')
    target=normalized(book.canonical,'')
    require(any(normalized(book.canonical,a['href'])==target and a['text']=='Python model inputs' for a in book.sidebar_links),'New sidebar entry missing')
    require(any(normalized(overview.canonical,a['href'])==target and 'Python model inputs' in a['text'] for a in overview.article_links),'Overview article link does not resolve to notebook')
    for token in ['score', 'predictLeaf', 'pandas_categorical', 'StringIndexerModel.from_labels']:
        require(token in book.article_text,'Missing rendered tutorial contract: '+token)
    return urllib.parse.urlsplit(book.canonical).path

def inspect_build(root, diagnostic_root=None):
    books=[];overviews=[];scanned=[];expected=[];excerpts={}
    for path in sorted(Path(root).rglob('*.html')):
        relative=str(path.relative_to(root));scanned.append(relative)
        text=path.read_text(errors='replace')
        # Match normalized article text, never a contiguous raw HTML substring.
        page=Page(text)
        if BOOK_MARKER in page.article_text:books.append((path,page))
        if OVERVIEW_MARKER in page.article_text:overviews.append((path,page))
        route=urllib.parse.unquote(relative).casefold().replace(' ','-')
        route=route[:-11] if route.endswith('/index.html') else route.removesuffix('.html')
        if any(route.endswith('/explore-algorithms/lightgbm/'+leaf) for leaf in ['python-model-inputs','overview']):
            expected.append((path,page))
        # Bound each excerpt, not the complete scanned-path inventory.
        if (path,page) in expected or BOOK_MARKER in page.article_text or OVERVIEW_MARKER in page.article_text:
            article=page.article_text
            positions=[article.find(token) for token in [BOOK_MARKER,OVERVIEW_MARKER,'Python-trained','StringIndexer','Python model inputs']]
            position=next((x for x in positions if x>=0),0)
            excerpts[relative]=article[max(0,position-256):position+3840]
    if diagnostic_root is not None:
        destination=Path(diagnostic_root);destination.mkdir(parents=True,exist_ok=True)
        inventory={'books':[str(x[0].relative_to(root)) for x in books],
                   'overviews':[str(x[0].relative_to(root)) for x in overviews],
                   'all_scanned_html_files':scanned,
                   'expected_route_files':[str(x[0].relative_to(root)) for x in expected],
                   'normalized_article_excerpts':excerpts,'retained_html':[]}
        # Keep route-based witnesses even when both marker-match lists are empty.
        retained=set()
        for kind,items in [('expected-route',expected),('book',books[:4]),('overview',overviews[:4])]:
            for index,(path,_) in enumerate(items):
                if path in retained:continue
                data=path.read_bytes()
                require(len(data)<=2*1024**2,'Diagnostic page cap')
                name=kind+'-'+str(index)+'.html'
                (destination/name).write_bytes(data);retained.add(path)
                inventory['retained_html'].append({'source':str(path.relative_to(root)),'evidence':name,'bytes':len(data)})
        (destination/'candidate-pages.json').write_text(json.dumps(inventory,indent=2))
    require(len(books)==len(overviews)==1,
            'Missing/ambiguous tutorial or Overview article: books='+str(len(books))+', overviews='+str(len(overviews)))
    route=verify_pair(books[0][1],overviews[0][1]);return books[0][0],overviews[0][0],route

def fixture():
    def html(route,body,href):return f'<html><head><link rel="canonical" href="https://example.invalid{route}"></head><aside><a href="/SynapseML/docs/Book/">Python model inputs</a></aside><article>{body}<a href="{href}">Python model inputs guide</a></article></html>'
    book=Page(html('/SynapseML/docs/Book/',BOOK_MARKER+' score predictLeaf pandas_categorical StringIndexerModel.from_labels','/SynapseML/docs/Book/'))
    good=Page(html('/SynapseML/docs/Overview/',OVERVIEW_MARKER,'/SynapseML/docs/Book/'));verify_pair(book,good)
    bad=Page(html('/SynapseML/docs/Overview/',OVERVIEW_MARKER,'./Book'))
    try:verify_pair(book,bad)
    except RuntimeError as error:require('Overview article link' in str(error),'Wrong article-link failure')
    else:raise RuntimeError('Relative child route wrongly accepted')

    for text, token in [(html('/SynapseML/docs/Book/', BOOK_MARKER + ' score predictLeaf pandas_categorical', '/SynapseML/docs/Book/'), 'Missing rendered tutorial contract'),
                        (html('/SynapseML/docs/Book/', BOOK_MARKER + ' score predictLeaf pandas_categorical StringIndexerModel.from_labels', '/SynapseML/docs/Book/').replace('Python model inputs</a>', 'Wrong sidebar</a>'), 'New sidebar entry missing')]:
        try: verify_pair(Page(text), good)
        except RuntimeError as error: require(token in str(error), 'Wrong negative contract failure')
        else: raise RuntimeError('Changed article/sidebar negative fixture accepted')


def inspect_build_fixture():
    """Exercise the actual discovery function with realistic MDX inline markup."""
    with tempfile.TemporaryDirectory(prefix='website-contract-',dir='/runtime/tmp') as name:
        root=Path(name)
        def html(route,body):
            return ('<html><head><link rel="canonical" href="https://example.invalid'+route+
                    '"></head><aside><a href="/SynapseML/docs/Book/">Python model inputs</a></aside>'+
                    '<article>'+body+'</article></html>')
        book=root/'book.html';overview=root/'overview.html'
        book_text=html('/SynapseML/docs/Book/',BOOK_MARKER.replace(' and ', '&#32;and ')+
            ' <code>score</code> <code>predictLeaf</code> <code>pandas_categorical</code> '+
            '<code>StringIndexerModel.from_labels</code>')
        overview_text=html('/SynapseML/docs/Overview/',
            'A new\n<code>StringIndexer</code> fit is not a substitute for the Python training mapping.'+
            '<a href="/SynapseML/docs/Book/">Python model inputs guide</a>')
        assert BOOK_MARKER not in book_text and OVERVIEW_MARKER not in overview_text
        book.write_text(book_text);overview.write_text(overview_text)
        a,b,route=inspect_build(root);assert (a,b,route)==(book,overview,'/SynapseML/docs/Book/')
        # Incidental markers in navigation do not create article candidates.
        noise=root/'noise.html';noise.write_text('<aside>'+BOOK_MARKER+'</aside><article>unrelated</article>')
        assert inspect_build(root)[2]==route
        cases=[('missing',None),('wrong-link',overview_text.replace('/SynapseML/docs/Book/">Python model inputs guide','/Wrong/">Python model inputs guide'))]
        for label,text in cases:
            overview.write_text(text if text is not None else '<article>missing</article>')
            try:inspect_build(root)
            except RuntimeError:pass
            else:raise AssertionError('Negative inspect_build accepted: '+label)
        overview.write_text(overview_text)
        versioned=root/'versioned';versioned.mkdir();duplicate=versioned/'copy.html';duplicate.write_text(book_text)
        try:inspect_build(root)
        except RuntimeError as error:assert 'books=2' in str(error)
        else:raise AssertionError('Ambiguous current/versioned duplicate accepted')
    print('Actual inspect_build: inline code/newlines/entities and navigation noise PASS; missing/link/duplicate negatives rejected')


def zero_match_diagnostics_fixture():
    with tempfile.TemporaryDirectory(prefix='website-zero-match-',dir='/runtime/tmp') as name:
        root=Path(name);build=root/'build';evidence=root/'evidence'
        expected={
            'docs/next/Explore Algorithms/LightGBM/Python model inputs/index.html':'<article>Missing tutorial heading</article>',
            'docs/next/Explore Algorithms/LightGBM/Overview/index.html':'<article>A new <code>StringIndexer</code> unexpected wording</article>',
        }
        for relative,text in expected.items():
            path=build/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        try:inspect_build(build,evidence)
        except RuntimeError as error:assert 'books=0, overviews=0' in str(error)
        else:raise AssertionError('Zero matches accepted')
        inventory=json.loads((evidence/'candidate-pages.json').read_text())
        assert set(inventory['all_scanned_html_files'])==set(expected)
        assert set(inventory['expected_route_files'])==set(expected)
        assert set(inventory['normalized_article_excerpts'])==set(expected)
        assert len(inventory['retained_html'])==2
        for item in inventory['retained_html']:
            assert (evidence/item['evidence']).read_text()==expected[item['source']]
    print('Zero-match failure retains both exact route HTML files, all scanned paths and normalized excerpts')
