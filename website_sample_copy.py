"""Run the unchanged upstream helper on a copy of the entire credential-free changed page."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

source=Path('/runtime/source')
packet=Path('/packet')
m=json.loads((packet/'manifest.json').read_text())
original=source/m['website_sample']['source']
raw=original.read_text()
assert 'getSecret(' not in raw and 'getAccessToken(' not in raw, 'UNAPPROVED_PROVIDER_SAMPLE'
folder=Path('/runtime/website-sample');folder.mkdir()
copy=folder/'_Train.md';shutil.copy2(original,copy)
spec=importlib.util.spec_from_file_location('synapse_website_doctest',source/'website/doctest.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.add_python_helper_to_markdown(str(folder),'_Train.md','0.0.2-i555-SNAPSHOT')
assert hashlib.sha256(copy.read_bytes()).hexdigest()==m['website_sample']['transformed_sha256'], 'UPSTREAM_HELPER_TEXT_IDENTITY'
print(json.dumps({'source':str(original),'copy':str(copy),'sha256':m['website_sample']['transformed_sha256']}))
