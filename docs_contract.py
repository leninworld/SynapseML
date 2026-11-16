"""Private, unrun exact DML documentation contracts. No project imports on import."""
import ast
import hashlib
import json
from pathlib import Path
import re

BOOK = 'docs/Explore Algorithms/Causal Inference/Quickstart - Measure Causal Effects.ipynb'
QUICK = 'docs/Quick Examples/estimators/causal/_causalInferenceDML.md'
PUBLIC_URI = 'wasbs://publicwasb@mmlspark.blob.core.windows.net/multi_attribution_sample.csv'
MODULES = {'synapse.ml.causal.DoubleMLEstimator', 'synapse.ml.causal.DoubleMLModel',
           'synapse.ml.causal._DoubleMLModel'}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blocks(text):
    found = re.findall(r'^```(python|scala)\n(.*?)^```\s*$', text, re.M | re.S)
    require([lang for lang, _ in found] == ['python', 'scala', 'python', 'scala'],
            'EXACT_FOUR_DML_FENCES')
    for lang, code in found:
        require(code.strip(), 'EMPTY_DML_FENCE')
        if lang == 'python':
            ast.parse(code)
    return found


def book(raw):
    require(sha(raw) == '561100f9bee680a89178a0a736116d539999a52b88314bd20f5593ae03375e11',
            'FROZEN_NOTEBOOK_IDENTITY')
    value = json.loads(raw)
    require(value['nbformat'] == 4 and len(value['cells']) == 15, 'EXACT_FIFTEEN_CELLS')
    require(sum(c['cell_type'] == 'code' for c in value['cells']) == 7, 'EXACT_SEVEN_CODE_CELLS')
    ids = [c['id'] for c in value['cells'] if 'id' in c]
    require(len(ids) == len(set(ids)), 'DUPLICATE_PRESENT_CELL_ID')
    for index, cell in enumerate(value['cells']):
        require(cell['cell_type'] in ('markdown', 'code'), 'UNEXPECTED_CELL_TYPE')
        require(not cell.get('metadata', {}).get('tags'), 'SKIP_OR_EXECUTION_TAG')
        if cell['cell_type'] == 'code':
            require(cell.get('outputs') == [], 'AUTHORED_NOTEBOOK_HAS_OUTPUTS')
            if index >= 10:
                require(cell.get('execution_count') is None, 'NEW_NOTEBOOK_CELL_NOT_CLEARED')
            ast.parse(''.join(cell['source']))
    return value


def runtime_book(raw, staged_uri):
    value = book(raw)
    text = ''.join(value['cells'][5]['source'])
    require(text.count(PUBLIC_URI) == 1, 'EXACT_PUBLIC_URI_NOT_FOUND')
    require(staged_uri.startswith('file:///runtime/inputs/'), 'UNAPPROVED_STAGED_URI')
    replacement = text.replace(PUBLIC_URI, staged_uri)
    before, after = ast.parse(text), ast.parse(replacement)
    occurrences = 0
    for node in ast.walk(before):
        if isinstance(node, ast.Constant) and node.value == PUBLIC_URI:
            node.value = staged_uri
            occurrences += 1
    require(occurrences == 1 and ast.dump(before) == ast.dump(after), 'URI_ONLY_AST_CHANGE')
    value['cells'][5]['source'] = replacement.splitlines(keepends=True)
    return value


def manifest(path):
    value = json.loads(Path(path).read_text())
    require(value['candidate'] is True, 'CANDIDATE_ARTIFACT_MANIFEST_REQUIRED')
    require(MODULES <= set(value['python_modules']), 'MISSING_DML_MODULE_IDENTITIES')
    jars = [Path(row['path']).resolve(strict=True) for row in value['jars']]
    require(jars.count(Path(value['core_jar']).resolve(strict=True)) == 1, 'CORE_JAR_NOT_UNIQUE')
    for row, jar in zip(value['jars'], jars):
        require(sha(jar.read_bytes()) == row['sha256'], 'JAR_HASH_MISMATCH')
    return value


def verify_runtime(spark, value):
    import importlib
    root = Path(value['installed_python_root']).resolve(strict=True)
    modules = {}
    for name, expected in value['python_modules'].items():
        path = Path(importlib.import_module(name).__file__).resolve(strict=True)
        require(path.is_relative_to(root) and sha(path.read_bytes()) == expected,
                'INSTALLED_MODULE_MISMATCH:' + name)
        modules[name] = expected
    require(spark.version == '3.5.0', 'SPARK_VERSION')
    jvm = spark._jvm
    loader = jvm.java.lang.Thread.currentThread().getContextClassLoader()
    origins = {}
    for name in ('DoubleMLEstimator', 'DoubleMLModel'):
        cls = jvm.java.lang.Class.forName('com.microsoft.azure.synapse.ml.causal.' + name, True, loader)
        origin = Path(cls.getProtectionDomain().getCodeSource().getLocation().toURI().getPath()).resolve()
        require(origin == Path(value['core_jar']).resolve(), 'JVM_CLASS_ORIGIN:' + name)
        origins[name] = str(origin)
    return {'pythonModules': modules, 'jvmOrigins': origins, 'spark': spark.version}


def fixture(quick_text, notebook_raw):
    require(len(blocks(quick_text)) == 4, 'POSITIVE_FENCES')
    runtime_book(notebook_raw, 'file:///runtime/inputs/multi_attribution_sample.csv')
    bads = [lambda: blocks(quick_text.replace('```scala', '```python', 1)),
            lambda: runtime_book(notebook_raw, 'https://example.invalid/data')]
    for call in bads:
        try:
            call()
        except (RuntimeError, SyntaxError):
            pass
        else:
            raise RuntimeError('NEGATIVE_DOC_CONTROL_ACCEPTED')
    return {'positive': 2, 'negative': 2, 'controlsOnlyNotProjectExecution': True}
