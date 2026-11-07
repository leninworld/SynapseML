"""Check actual SSR output and the owned npm-start compilation and HTTP liveness; never launch a browser."""
from pathlib import Path
import json
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
  return super().redirect_request(req,fp,code,msg,headers,newurl)
opener=urllib.request.build_opener(LocalRedirect)
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
  try:
   with opener.open('http://127.0.0.1:3000'+urllib.parse.quote(urllib.parse.unquote(route),safe='/'),timeout=1) as response:
    data=response.read(2*1024**2+1)
    assert len(data)<=2*1024**2 and response.status==200
    assert b'<!DOCTYPE html>' in data or b'<!doctype html>' in data
    result={'route':route,'http_status':response.status,'bytes':len(data),'ssr_file':str(page),'ssr_bytes':page.stat().st_size,'dev_client_compilation_success':compiled,'http_evidence':'loopback server liveness only; production SSR HTML is rendered-page evidence'}
    break
  except (urllib.error.URLError,TimeoutError):
   continue
 assert result,'NO_SUCCESSFUL_DEV_COMPILE_AND_LIVENESS'
 print(json.dumps(result))
finally:
 sel.close()
 if proc.poll() is None:
  os.killpg(proc.pid,signal.SIGTERM)
  try:proc.wait(timeout=8)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
 Path('/runtime/website-dev.log').write_bytes(logs)
 assert proc.poll() is not None
