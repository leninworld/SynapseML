"""Generate the complete current website tree using the unchanged upstream channel."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import yaml
from controller_checks import verify_projection

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
selected.write_text(yaml.safe_dump({'channels': [{'name': original['name'], 'input_dir': str(input_root), 'output_dir': str(output_root)}]}))
files = sorted(p for p in input_root.rglob('*') if p.is_file())
assert files and not any(p.is_symlink() for p in input_root.rglob('*'))
tracked_raw = subprocess.run(['git', 'ls-files', '-z', 'website/docs'], cwd=source, check=True, capture_output=True).stdout
tracked = {str(Path(name.decode()).relative_to('website/docs')) for name in tracked_raw.split(b'\0') if name}
subprocess.run([sys.executable, '__main__.py', '--manifest', str(selected)],
               cwd=manifest_file.parent, check=True, timeout=540)
projection = verify_projection(input_root, output_root, tracked)
print(json.dumps({'generator': 'unmodified WebsiteChannel/DocumentProcessor',
                  'full_input_files': len(files), 'output_files': projection['output_files'],
                  'preexisting_tracked_website_files': sorted(tracked),
                  'source_behavior_citations': {'inventory': 'channels.py25-32,36-51: rglob/process exactly projected files; include existingtrackedoutputs', 'byte_copy': 'channels.py47-56: shutil.copy on nonnotebook files', 'frontmatter': 'channels.py41-45: literal title prefix before notebook markdown', 'pandoc': 'WebsiteChannel23-56 has no pypandoc call; later FabricChannel owns conversions', 'cli': '__main__.py20-29 --manifest argparse; official website-deploy.yml46-48 invokes same CLI defaultmanifest'},
                  'notebooks_converted_without_execution': sum(p.suffix == '.ipynb' for p in files),
                  'manifest_sha256': hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
                  'selected_manifest_sha256': hashlib.sha256(selected.read_bytes()).hexdigest()}))
