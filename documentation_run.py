"""Execute the exact authored Python/Scala snippets, with real installed artifacts."""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

DOC = Path('/runtime/source/docs/Explore Algorithms/LightGBM/Python model inputs.md')


def blocks(text):
    found = re.findall(r'^```(python|scala)\n(.*?)^```\s*$', text, re.M | re.S)
    assert [language for language, _ in found] == ['python', 'scala', 'python'], 'DOC_CODE_SELECTION'
    for language, code in found:
        assert code.strip(), 'EMPTY_CODE_BLOCK'
        if language == 'python':
            ast.parse(code)
    return found


def fixture():
    source = '```python\nx = 1\n```\n```scala\nval x = 1\n```\n```python\ny = x\n```\n'
    assert len(blocks(source)) == 3
    for bad in [source.replace('```scala', '```python'), source.rsplit('```', 1)[0],
                source.replace('y = x', 'this is not valid python !')]:
        try:
            blocks(bad)
        except (AssertionError, SyntaxError):
            pass
        else:
            raise AssertionError('DOC_NEGATIVE_CONTROL_MISSED')
    print('Exact code-block selection: positive and three negative controls passed')


def train():
    selected = blocks(DOC.read_text())
    namespace = {'__name__': '__main__'}
    exec(compile(selected[0][1], str(DOC) + ':python-train', 'exec'), namespace)
    directory = namespace['fixture']
    expected = {'python-model.txt': '767aef4e256ddb3535b1f690a87043d96c3f5e9ba7ddf4623b430d7c8fae192e',
                'expected.tsv': '00185629cc179d5374345678e25baea2015b026f499648033508fb427fb7464c'}
    for name, digest in expected.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, name
        committed = Path('/runtime/source/lightgbm/src/test/resources/python-interop') / name
        assert committed.read_bytes() == (directory / name).read_bytes(), name
    (Path('/runtime') / 'documentation-python-context.json').write_text(json.dumps({
        'fixture': str(directory), 'category_lists': namespace['category_lists'],
        'code_sha256': hashlib.sha256(selected[0][1].encode()).hexdigest(),
        'regenerated_fixture_hashes': expected}))
    scala = '''// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.lightgbm.split6
import com.microsoft.azure.synapse.ml.core.test.base.TestBase
class PythonInteropDocumentationExample extends TestBase {
  test("Exact Scala documentation example") {
'''+ selected[1][1] + '\n  }\n}\n'
    Path('/runtime/PythonInteropDocumentationExample.scala').write_text(scala)
    print('Exact Python training block and byte-identical committed-generator regeneration passed')


def spark_example():
    from pyspark.sql import SparkSession
    from artifact_provenance import verify_installed_python, verify_jvm
    info = json.loads(Path('/runtime/candidate-artifact-manifest.json').read_text())
    python = verify_installed_python(info['python_modules'])
    spark = SparkSession.builder.master('local[2]').appName('Python model inputs').getOrCreate()
    try:
        jvm = verify_jvm(spark, info['jvm_classes'])
        state = json.loads(Path('/runtime/documentation-python-context.json').read_text())
        namespace = {'spark': spark, 'category_lists': state['category_lists'],
                     'model_text': (Path(state['fixture']) / 'python-model.txt').read_text()}
        selected = blocks(DOC.read_text())
        exec(compile(selected[2][1], str(DOC) + ':pyspark-inputs', 'exec'), namespace)
        assert namespace['spark'] is spark
        Path('/runtime/documentation-spark-result.json').write_text(json.dumps({
            'passed': True, 'spark_version': spark.version, 'python_modules': python,
            'jvm_classes': jvm, 'code_sha256': hashlib.sha256(selected[2][1].encode()).hexdigest(),
            'predictions': {str(k): v for k, v in namespace['actual'].items()},
            'mapping_frequency_negative_control': True, 'explicit_null_unknown_NaN': True,
            'real_installed_text_loader_and_transform': True}, indent=2))
        print('Exact PySpark example passed using verified installed wheels and branch JARs')
    finally:
        spark.stop()


if __name__ == '__main__':
    {'fixture': fixture, 'train': train, 'spark': spark_example}[sys.argv[1]]()
