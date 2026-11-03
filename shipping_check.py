"""Inspect the actual built shipping artifacts, no project imports."""
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile

source = Path('/runtime/source')
wheels = list(source.glob('core/target/scala-*/generated/package/python/synapseml_core-*.whl'))
assert len(wheels) == 1
with zipfile.ZipFile(wheels[0]) as archive:
    matches = [name for name in archive.namelist() if name.endswith('/TrainClassifier.py')]
    assert len(matches) == 1
    data = archive.read(matches[0])
    assert b'def setIgnoredCols' in data and b'ignoredCols' in data
sdists = list(Path('/runtime/shipping').glob('*.tar.gz'))
assert len(sdists) == 1
with tarfile.open(sdists[0], 'r:gz') as archive:
    matches = [name for name in archive.getnames() if name.endswith('/TrainClassifier.py')]
    assert len(matches) == 1
    data = archive.extractfile(matches[0]).read()
    assert b'def setIgnoredCols' in data and b'ignoredCols' in data
print(json.dumps([{'file': str(path), 'bytes': path.stat().st_size,
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                  for path in wheels + sdists]))
