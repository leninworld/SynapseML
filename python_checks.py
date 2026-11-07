"""Actual installed PySpark/OpenCV tests and complete sequential Markdown examples."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import xml.etree.ElementTree as ET
from generated_artifacts import sha
from validation_runtime import require


def run_pytest(lane, label, path, artifacts, count, failure_names=(), tokens=(), extra=()):
    os.environ.update(IMAGE_ARTIFACT_MANIFEST=str(artifacts), PYTHONPATH=str(lane.packet))
    options = ['--import-mode=importlib', '-p', 'provenance_pytest', '-p', 'no:cacheprovider', *extra]
    os.environ['IMAGE_PROVENANCE_RESULT'] = str(lane.root/(label+'-collect-provenance.json'))
    output = lane.command(label+'-collect', ['python','-m','pytest','--collect-only','-q',
                                           *options,str(path)], 180)
    nodes = [x.strip() for x in output.splitlines() if '::' in x and path.name in x.split('::')[0]]
    require(len(nodes) == count and len(set(nodes)) == count, 'Unexpected real pytest collection: '+label)
    require(Path(os.environ['IMAGE_PROVENANCE_RESULT']).is_file(), 'Missing collection provenance')
    os.environ['IMAGE_PROVENANCE_RESULT'] = str(lane.root/(label+'-run-provenance.json'))
    report = lane.root/(label+'.xml')
    output = lane.command(label, ['python','-m','pytest','-q','-s','--junitxml='+str(report),
                                 *options,str(path)], 600, expected=(1,) if failure_names else (0,))
    cases = list(ET.parse(report).getroot().iter('testcase'))
    require(len(cases) == count and not list(ET.parse(report).getroot().iter('skipped')),
            'Missing/skipped executed pytest cases')
    require(sorted(x.get('name') for x in cases) == sorted(x.rsplit('::',1)[1] for x in nodes),
            'Executed pytest IDs changed')
    failures = {x.get('name'): '\n'.join((e.get('message','')+'\n'+(e.text or ''))
                    for e in [*x.findall('failure'), *x.findall('error')]) for x in cases
                    if x.find('failure') is not None or x.find('error') is not None}
    require(set(failures) == set(failure_names), 'Unexpected baseline/candidate pytest failures')
    if failures:
        for name, alternatives in tokens.items():
            require(any(value.lower() in failures[name].lower() for value in alternatives),
                    'Wrong fail-before cause: '+name)
    require(Path(os.environ['IMAGE_PROVENANCE_RESULT']).is_file(), 'Missing execution provenance')
    (lane.root/(label+'-nodes.json')).write_text(json.dumps(nodes, indent=2))
    return output


def whole_page(lane, label, artifacts):
    doc = lane.source/'docs/Quick Examples/transformers/core/_Image.md'
    markdown = doc.read_text()
    count = 4 if lane.candidate else 3
    require(len(re.findall(r'```python\n',markdown)) == count, 'Full page selection changed')
    folder = lane.root/(label+'-examples')
    folder.mkdir()
    sample = folder/doc.name
    if lane.candidate:
        needle = '    assert spark.read.image().load(destination).count() == 1'
        require(markdown.count(needle) == 1, 'Missing final example witness location')
        markdown = markdown.replace(needle,needle+'\nprint("IMAGE_WRITER_FULL_EXAMPLE_EXECUTED")',1)
    sample.write_text(markdown)
    spec = importlib.util.spec_from_file_location('actual_upstream_doctest',lane.source/'website/doctest.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.add_python_helper_to_markdown(str(folder),sample.name,lane.version)
    d0 = json.loads((lane.root/'d0-validate/D0-result.json').read_text())
    expected = d0['actual_page_items']['candidate' if lane.candidate else 'baseline']['count']
    output = run_pytest(lane,label+'-whole-page',sample,artifacts,expected,extra=('--codeblocks',))
    if lane.candidate:
        require('IMAGE_WRITER_FULL_EXAMPLE_EXECUTED' in output,'Changed example was not executed')
    (lane.root/(label+'-page-identity.json')).write_text(json.dumps({
        'source_sha256':sha(doc),'transformed_sha256':sha(sample),'python_blocks':count,
        'helper_sha256':sha(lane.source/'website/doctest.py'),
        'scope':'Complete sequential page via actual upstream helper and pytest-codeblocks',
    },indent=2))


def shipping(lane):
    records = []
    for module in ('core','opencv'):
        roots = list(lane.source.glob(module+'/target/scala-*/generated/src/python'))
        require(len(roots) == 1,'Ambiguous shipping package source')
        destination = lane.root/('shipping-'+module)
        destination.mkdir()
        lane.command(module+'-shipping-sdist',['python','setup.py','sdist','--dist-dir',str(destination)],
                     180,cwd=roots[0])
        archives = list(destination.glob('*.tar.gz'))
        require(len(archives) == 1,'Missing shipping sdist')
        records.append({'module':module,'path':str(archives[0]),'sha256':sha(archives[0])})
        with tarfile.open(archives[0]) as archive:
            required = 'synapse/ml/io/IOImplicits.py' if module == 'core' else 'synapse/ml/opencv/_ImageTransformer.py'
            entries = [m for m in archive.getmembers() if m.name.endswith('/'+required)]
            require(len(entries) == 1 and entries[0].isfile(),'Missing shipped source module')
    (lane.root/'shipping-artifacts.json').write_text(json.dumps(records,indent=2))
