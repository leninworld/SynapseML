"""Check actual SSR output and the owned npm-start compilation and HTTP liveness; never launch a browser."""
from pathlib import Path
import json
import collections, http.client
import os
import selectors
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

web=Path('/runtime/source/website')
needle='Write transformed images'
files=[f for f in (web/'build').rglob('*.html') if needle in f.read_text(errors='replace')]
assert len(files)==1, 'MISSING_OR_AMBIGUOUS_RENDERED_PAGE'
page=files[0];html=page.read_text()
assert 'filenames' in html and 'ImageTransformer' in html and 'flipped' in html
# Production HTML gives the real route, independent of the original source filename.
import re
canonical=re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"',html)
assert canonical,'NO_RENDERED_CANONICAL_ROUTE'
route=urllib.parse.urlsplit(canonical[1]).path
assert route.startswith('/SynapseML/'),route
class LocalRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  target=urllib.parse.urlsplit(newurl)
  assert target.scheme=='http' and target.hostname=='127.0.0.1' and target.port==3000, 'NONLOCAL_DEV_REDIRECT'
  redirects.append({'from':req.full_url,'status':code,'to':newurl})
  return super().redirect_request(req,fp,code,msg,headers,newurl)
opener=urllib.request.build_opener(LocalRedirect)
first_attempts=[];last_attempts=collections.deque(maxlen=64);attempt_total=0
outcomes=collections.Counter();next_probe=0.0;redirects=[]
proc=subprocess.Popen(['npm','start','--','--host','127.0.0.1','--port','3000','--no-open'],cwd=web,
 stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
start=time.monotonic();logs=bytearray();result=None;compiled=False
try:
 while time.monotonic()-start<480:
  assert proc.poll() is None,'OWNED_DEV_SERVER_EXITED'
  for key,_ in sel.select(.2):
   chunk=os.read(key.fd,8192)
   if chunk:
    assert len(logs)+len(chunk)<=1024**2,'WEBSITE_DEV_LOG_CAP';logs.extend(chunk)
  clean=re.sub(rb'\x1b\[[0-9;]*[A-Za-z]',b'',bytes(logs))
  assert not any(marker in clean for marker in [b'Failed to compile',b'Module not found',b'[ERROR]']), 'DEV_COMPILATION_ERROR'
  compiled=bool(re.search(rb'Client:\s+Compiled successfully',clean) or re.search(rb'webpack[^\n]*compiled successfully',clean,re.I))
  if not compiled:continue
  if time.monotonic()<next_probe:continue
  next_probe=time.monotonic()+2.0
  # The server announces this base URL. SSR output above proves changed content.
  urls=['http://127.0.0.1:3000/SynapseML/',
        'http://127.0.0.1:3000'+urllib.parse.quote(urllib.parse.unquote(route),safe='/')]
  successes={}
  for url in dict.fromkeys(urls):
   redirects.clear()
   item={'elapsed_seconds':time.monotonic()-start,'url':url}
   try:
    with opener.open(url,timeout=min(10,max(.1,480-(time.monotonic()-start)))) as response:
     data=response.read(2*1024**2+1)
     item.update(status=response.status,headers={k:v for k,v in response.headers.items()
                 if k.lower() in ['content-type','location','content-length']},
                 first_512_bytes=data[:512].decode('utf-8',errors='replace'),bytes=len(data))
     assert len(data)<=2*1024**2,'HTTP_RESPONSE_CAP'
     successes[url]=response.status==200 and (b'<!DOCTYPE html>' in data or b'<!doctype html>' in data)
   except urllib.error.HTTPError as error:
    item.update(exception=type(error).__name__,status=error.code,
                headers={k:v for k,v in error.headers.items() if k.lower() in ['content-type','location']},
                first_512_bytes=error.read(512).decode('utf-8',errors='replace'))
   except (urllib.error.URLError,TimeoutError,OSError,http.client.HTTPException) as error:
    item.update(exception=type(error).__name__,errno=getattr(getattr(error,'reason',error),'errno',None),
                message=str(error)[:1024])
   finally:
    item['redirect_chain']=list(redirects);attempt_total+=1
    outcomes[f"{url}|{item.get('status')}|{item.get('exception')}"]+=1
    (first_attempts if len(first_attempts)<64 else last_attempts).append(item)
   if time.monotonic()-start>=480:break
  if successes.get(urls[0]):
   result={'route':route,'base_url':urls[0],'base_http_status':200,
           'changed_route_success':successes.get(urls[1],False),
           'ssr_file':str(page),'ssr_bytes':page.stat().st_size,
           'dev_client_compilation_success':compiled,
           'http_evidence':'announced base URL liveness only; production SSR HTML is changed-content proof',
           'changed_route_result_is_diagnostic':True}
   break
 assert result,'NO_SUCCESSFUL_DEV_COMPILE_AND_LIVENESS'
 print(json.dumps(result))
finally:
 sel.close()
 if proc.poll() is None:
  os.killpg(proc.pid,signal.SIGTERM)
  try:proc.wait(timeout=8)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
 Path('/runtime/website-dev.log').write_bytes(logs)
 Path('/runtime/website-http-attempts.json').write_text(json.dumps({'attempt_total':attempt_total,'first_attempts':first_attempts,'last_attempts':list(last_attempts),'outcomes':dict(outcomes),'result':result,'elapsed_seconds':time.monotonic()-start},indent=2))
 assert proc.poll() is not None
