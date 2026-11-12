"""Shared production-page, article-link and sidebar contract, separate from dev liveness."""
from html.parser import HTMLParser
from pathlib import Path
import re,urllib.parse
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

def inspect_build(root):
    books=[];overviews=[]
    for path in Path(root).rglob('*.html'):
        text=path.read_text(errors='replace')
        if BOOK_MARKER not in text and OVERVIEW_MARKER not in text:continue
        page=Page(text)
        if BOOK_MARKER in page.article_text:books.append((path,page))
        if OVERVIEW_MARKER in page.article_text:overviews.append((path,page))
    require(len(books)==len(overviews)==1,'Missing/ambiguous new notebook or Overview article')
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
