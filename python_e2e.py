"""Real installed generated Python API + branch-built JVM artifact Spark E2E."""
import hashlib
import json
import os
from pathlib import Path
from pyspark.sql import SparkSession, functions as F, types as T

P = Path('/runtime/source')
manifest_path = Path('/runtime/candidate-artifact-manifest.json')
manifest = json.loads(manifest_path.read_text())
from artifact_provenance import verify_installed_python
installed_modules = verify_installed_python(manifest['python_modules'])
cp = [row['path'] for row in manifest['jars']]
for row in manifest['jars']:
    assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() == row['sha256']
ARTIFACTS = [next(Path(row['path']) for row in manifest['jars']
    if Path(row['path']).name.startswith('synapseml-' + module + '_')) for module in ['core','vw']]
# The only SynapseML JARs in the runtime are freshly built from this candidate.
transitives = [x for x in cp if x.endswith('.jar') and not any(y in x.lower() for y in ['synapseml', 'mmlspark'])
               and '/org.apache.spark/' not in x and '/org.scala-lang/' not in x and '/org.apache.hadoop/' not in x]
assert all(x.is_file() for x in ARTIFACTS) and all(Path(x).is_file() for x in transitives)
jars = [str(x) for x in ARTIFACTS] + transitives
spark = (SparkSession.builder.master('local[2]').appName('GenericSchemaIssue2817')
    .config('spark.jars', ','.join(jars)).config('spark.ui.enabled', 'false')
    .config('spark.driver.memory', '2g').config('spark.sql.shuffle.partitions', '1').getOrCreate())
spark.sparkContext.setLogLevel('WARN')
try:
    from synapse.ml.vw import VowpalWabbitGeneric, VowpalWabbitGenericModel
    import importlib
    modules = [importlib.import_module(x) for x in ['synapse.ml.vw.VowpalWabbitGeneric', 'synapse.ml.vw._VowpalWabbitGeneric']]
    import sys, site
    installed_roots = [Path(sys.prefix).resolve(), Path(site.USER_BASE).resolve()]
    assert all(any(Path(x.__file__).resolve().is_relative_to(root) for root in installed_roots)
               and not Path(x.__file__).resolve().is_relative_to(P) for x in modules)
    assert spark.version == '3.5.0'
    data = spark.createDataFrame([('1 |a b c',), ('2 |d e f',)], ['value']).coalesce(1)
    results = []
    for probabilities in [False, True]:
        learner = VowpalWabbitGeneric().setUseBarrierExecutionMode(False)
        if probabilities:learner.setPassThroughArgs('--oaa 2')
        model = learner.fit(data)
        if probabilities:model.setTestArgs('--probabilities')
        actual = model._java_obj.getClass().getProtectionDomain().getCodeSource().getLocation().toString()
        assert actual == 'file:' + str(ARTIFACTS[1]), actual
        control = model.transform(data)
        predicted = control.columns[1:]
        schema = T.StructType([T.StructField('id', T.IntegerType(), False, {'purpose': 'retained-id'}),
                               T.StructField('value', T.StringType(), True, {'purpose': 'vw-input'}),
                               T.StructField('label', T.StringType(), False)])
        full = spark.createDataFrame([(7, '1 |a b c', 'first'), (8, '2 |d e f', 'second')], schema).coalesce(1)
        expected_predictions = control.orderBy('value').select(*predicted).collect()
        for order in [('id', 'value', 'label'), ('value', 'id', 'label'), ('id', 'label', 'value')]:
            arranged = full.select(*order)
            output = model.transform(arranged)
            assert output.schema.fields[:3] == arranged.schema.fields
            assert output.columns[3:] == predicted
            declared = spark._jvm.org.apache.spark.sql.types.DataType.fromJson(arranged.schema.json())
            expected_schema = T.StructType.fromJson(json.loads(model._java_obj.transformSchema(declared).json()))
            assert output.schema == expected_schema
            assert output.select(*order).collect() == arranged.collect()
            assert output.orderBy('value').select(*predicted).collect() == expected_predictions
        renamed = full.withColumnRenamed('value', 'example')
        copied = model.copy({model.inputCol: 'example'})
        assert copied.transform(renamed).count() == 2
        path = '/runtime/model-' + str(probabilities)
        model.write().save(path)
        restored = VowpalWabbitGenericModel.load(path)
        assert restored.transform(full).orderBy('id').collect() == model.transform(full).orderBy('id').collect()
        for sensitive in [False, True]:
            spark.conf.set('spark.sql.caseSensitive', str(sensitive).lower())
            name = 'predictions' if probabilities else 'prediction'
            collisions = [name, name.capitalize()]
            for column in collisions:
                conflicting = full.withColumn(column, F.lit(3))
                try:
                    transformed = model.transform(conflicting)
                    transformed.collect()
                except Exception as error:
                    assert not sensitive or column == name
                    assert 'Rename them before transforming' in str(error)
                else:
                    assert sensitive and column != name
            spark.conf.set('spark.sql.caseSensitive', 'false')
        results.append({'probabilities': probabilities, 'schema_values_metadata': 'PASS',
                        'custom_copy_save_load': 'PASS', 'collision_modes': 'PASS', 'jvm_code_source': actual})
    proof = {'status': 'PASS', 'spark': spark.version, 'java': spark._jvm.java.lang.System.getProperty('java.version'),
             'python_modules': [str(x.__file__) for x in modules], 'results': results,
             'branch_jars': {str(x): hashlib.sha256(x.read_bytes()).hexdigest() for x in ARTIFACTS}}
    Path('/runtime/python-e2e-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    print('GENERATED_INSTALLED_PYTHON_REAL_SPARK_PASS ' + json.dumps(proof), flush=True)
finally:
    spark.stop()
