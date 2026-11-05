"""Owned Docker-only UID bootstrap; no root network/downloads."""
from pathlib import Path
import json, os, pwd, time
assert Path('/.dockerenv').exists() and os.getuid() == 0
assert time.time() < float(os.environ['VALIDATION_DEADLINE_EPOCH'])
assert os.environ['VALIDATION_LANE'] == 'Q'
root = Path('/runtime')
for name in ('home', 'tmp'):
    path = root / name
    path.mkdir(exist_ok=False)
    os.chown(path, 1001, 1001)
os.chown(root, 1001, 1001)
assert pwd.getpwuid(1001).pw_name == 'validation'
env = {k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('TOKEN','SECRET','PASSWORD','CREDENTIAL','API_KEY'))}
env.update(HOME='/runtime/home', TMPDIR='/runtime/tmp', PATH='/opt/node/bin:/usr/local/bin:/usr/bin:/bin',
           PIP_CACHE_DIR='/runtime/pip-cache', PIP_NO_INPUT='1', PIP_RETRIES='0', PIP_DEFAULT_TIMEOUT='20',
           NODE_OPTIONS='--max-old-space-size=6144', DOCUSAURUS_TELEMETRY_DISABLED='true', CI='true',
           npm_config_fetch_retries='0', npm_config_fetch_timeout='20000',
           OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
os.setgroups([]); os.setgid(1001); os.setuid(1001)
os.execve('/usr/local/bin/python3', ['python3','/packet/inside.py'], env)
