"""Shared stdlib notebook/source contract. No execution on import."""
import ast,base64,hashlib,json
from pathlib import Path
from validation_runtime import require


def load_book(raw):
    require(len(raw)<=65536,'Notebook input cap')
    book=json.loads(raw)
    require(book['nbformat']==4 and isinstance(book['cells'],list),'Notebook format')
    cells=book['cells'];ids=[c['id'] for c in cells]
    require(len(ids)==len(set(ids)),'Duplicate notebook cell IDs')
    code=[]
    for cell in cells:
        require(cell['cell_type'] in ('code','markdown'),'Unexpected cell type')
        require(isinstance(cell['source'],list) and all(isinstance(x,str) for x in cell['source']),'Cell source shape')
        require(not cell.get('metadata',{}).get('tags'),'No skip or hidden execution tags')
        if cell['cell_type']=='code':
            require(cell.get('execution_count') is None and cell.get('outputs')==[],'Authored notebook must be cleared')
            text=''.join(cell['source']);ast.parse(text);code.append(text)
    require(code,'No real code cells')
    return book,code


def same_semantics(original,formatted):
    a,ac=load_book(original);b,bc=load_book(formatted)
    require(len(a['cells'])==len(b['cells']) and len(ac)==len(bc),'Notebook cells changed')
    require({k:v for k,v in a.items() if k!='cells'}=={k:v for k,v in b.items() if k!='cells'},'Notebook metadata changed')
    for x,y in zip(a['cells'],b['cells']):
        require({k:v for k,v in x.items() if k!='source'}=={k:v for k,v in y.items() if k!='source'},'Cell identity or metadata changed')
        if x['cell_type']=='markdown':require(x['source']==y['source'],'Markdown changed during formatting')
        else:require(ast.dump(ast.parse(''.join(x['source'])),include_attributes=False)==ast.dump(ast.parse(''.join(y['source'])),include_attributes=False),'Formatter changed Python semantics')
    return len(ac)


def decode_transport(encoded,digest):
    require(isinstance(encoded,str) and 0<len(encoded)<=90000,'Notebook transport cap')
    raw=base64.b64decode(encoded,validate=True)
    require(hashlib.sha256(raw).hexdigest()==digest,'Notebook transport identity')
    load_book(raw)
    return raw
