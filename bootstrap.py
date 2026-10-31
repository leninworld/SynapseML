"""Container root bootstrap; never run this on a host."""
from pathlib import Path
import hashlib,json,os,pwd,sys,time,urllib.request,urllib.parse
assert os.getuid()==0 and Path('/.dockerenv').exists()
P=Path('/packet');R=Path('/runtime');M=json.loads((P/'manifest.json').read_text())
END=float(os.environ['VALIDATION_DEADLINE_EPOCH'])
assert time.time()<END
lane=os.environ['VALIDATION_LANE'];assert lane in M['jobs']
mounts=[line.split() for line in Path('/proc/mounts').read_text().splitlines()]
entry=[fields for fields in mounts if fields[1]=='/opt/bootstrap'];assert len(entry)==1
options=set(entry[0][3].split(','))
assert {'nosuid','nodev'} <= options and ('noexec' not in options if lane=='C' else 'noexec' in options),'BOOTSTRAP_MOUNT_CONTRACT'
assert Path('/opt/bootstrap').stat().st_uid==0 and Path('/opt/bootstrap').stat().st_mode & 0o022==0
print(json.dumps({'bootstrap_mount_options':sorted(options),'lane':lane}),flush=True)
allowed={'repo.maven.apache.org','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}
class Redirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  assert urllib.parse.urlparse(newurl).hostname in allowed and newurl.startswith('https://')
  return super().redirect_request(req,fp,code,msg,headers,newurl)
def download(url,dest,limit,hashname,expected):
 assert urllib.parse.urlparse(url).hostname in allowed and url.startswith('https://')
 start=time.monotonic();h=hashlib.new(hashname);size=0
 with urllib.request.build_opener(Redirect()).open(url,timeout=20) as response, dest.open('xb') as output:
  assert response.status==200
  while True:
   assert time.time()<END and time.monotonic()-start<120
   chunk=response.read(65536)
   if not chunk:break
   size+=len(chunk);assert size<=limit;h.update(chunk);output.write(chunk)
 assert h.hexdigest()==expected,'INPUT_HASH_MISMATCH'
 print(json.dumps({'public_input':url,'bytes':size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}),flush=True)
jar=Path('/opt/bootstrap/sbt-launch.jar');download(M['sbt']['url'],jar,8*1024**2,'sha1',M['sbt']['sha1'])
if lane=='C':
 item=M['micromamba'];dest=Path('/opt/bootstrap/micromamba');download(item['url'],dest,item['bytes'],'sha256',item['sha256']);dest.chmod(0o755)
for name in ('home','tmp'):
 (R/name).mkdir(exist_ok=False);os.chown(R/name,1001,1001)
os.chown(R,1001,1001);assert pwd.getpwuid(1001).pw_name=='validation'
env={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ['TOKEN','SECRET','PASSWORD','CREDENTIAL','API_KEY'])}
env.update(HOME='/runtime/home',TMPDIR='/runtime/tmp',JAVA_HOME='/opt/java/openjdk',PATH='/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',MAMBA_ROOT_PREFIX='/runtime/mamba',PIP_CACHE_DIR='/runtime/pip-cache',PIP_LOG='/runtime/pip-install.log',PYSPARK_SUBMIT_ARGS='--driver-memory 1g pyspark-shell',SPARK_SUBMIT_OPTS='-Duser.home=/runtime/home -Djava.io.tmpdir=/runtime/tmp')
os.setgroups([]);os.setgid(1001);os.setuid(1001)
os.execve('/usr/local/bin/python3',['python3','/packet/inside.py'],env)
