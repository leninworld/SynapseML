"""Generate the complete current website tree using the unchanged upstream channel."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import yaml

source = Path('/runtime/source')
input_root = source / 'docs'
output_root = source / 'website/docs'
manifest_file = source / 'tools/docgen/docgen/manifest.yaml'
manifest = yaml.safe_load(manifest_file.read_text())
channels = [c for c in manifest['channels'] if c['name'] == 'channels.WebsiteChannel']
assert len(channels) == 1 and set(channels[0]) == {'name', 'input_dir', 'output_dir'}
original = channels[0]
assert (manifest_file.parent / original['input_dir']).resolve() == input_root
assert (manifest_file.parent / original['output_dir']).resolve() == output_root
# Preserve this channel's full input set; Fabric/Azure publication channels are unrelated.
selected = Path('/runtime/website-only-manifest.yaml')
selected.write_text(yaml.safe_dump({'channels': channels}))
files = sorted(p for p in input_root.rglob('*') if p.is_file())
assert files and not any(p.is_symlink() for p in input_root.rglob('*'))
expected = {str(p.relative_to(input_root).with_suffix('.md') if p.suffix == '.ipynb' else p.relative_to(input_root)) for p in files}
assert len(expected) == len(files), 'DOCGEN_OUTPUT_COLLISION'
subprocess.run([sys.executable, '__main__.py', '--manifest', str(selected)],
               cwd=manifest_file.parent, check=True, timeout=540)
actual = {str(p.relative_to(output_root)) for p in output_root.rglob('*') if p.is_file()}
assert actual == expected, 'INCOMPLETE_OR_EXTRA_WEBSITE_DOCS'
for path in files:
    output = output_root / path.relative_to(input_root)
    if path.suffix == '.ipynb':
        output = output.with_suffix('.md')
        assert output.stat().st_size and output.read_text().startswith('---\ntitle: ')
    else:
        assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(output.read_bytes()).digest()
print(json.dumps({'generator': 'unmodified WebsiteChannel/DocumentProcessor',
                  'full_input_files': len(files), 'output_files': len(actual),
                  'notebooks_converted_without_execution': sum(p.suffix == '.ipynb' for p in files),
                  'manifest_sha256': hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
                  'selected_manifest_sha256': hashlib.sha256(selected.read_bytes()).hexdigest()}))
