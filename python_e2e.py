"""Real installed Python/Spark controls; Scala owns the schema-mutation oracle."""
import hashlib
import json
import sys
from pathlib import Path
from pyspark.ml import Pipeline, PipelineModel
from pyspark.ml.classification import LogisticRegression
from pyspark.ml.linalg import VectorUDT, Vectors
from pyspark.sql import SparkSession, types as T
from artifact_provenance import verify_installed_python, verify_jvm
from baseline_checks import python_failure_kind

root = Path('/runtime')
role = sys.argv[1]
assert role in ('baseline', 'candidate')
manifest = json.loads((root / (role + '-artifact-manifest.json')).read_text())
installed = verify_installed_python(manifest['python_modules'])
for item in manifest['jars']:
    assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256']
core = next(Path(item['path']) for item in manifest['jars'] if Path(item['path']).name.startswith('synapseml-core_'))
transitives = [item['path'] for item in manifest['jars']
               if not any(token in item['path'].lower() for token in ('synapseml', 'mmlspark'))
               and not any(token in item['path'] for token in
                           ('/org.apache.spark/', '/org.scala-lang/', '/org.apache.hadoop/'))]
spark = (SparkSession.builder.master('local[2]').appName('CategoricalInputSchema-' + role)
         .config('spark.jars', ','.join([str(core), *transitives]))
         .config('spark.ui.enabled', 'false').config('spark.driver.memory', '2g')
         .config('spark.sql.shuffle.partitions', '1').getOrCreate())
spark.sparkContext.setLogLevel('WARN')
proof = {'role': role, 'complete': False, 'results': [], 'known_long_signature': None,
         'Scala_mutation_oracle': 'Separate native Scala tests; no invented Python fail-before',
         'installed_modules': installed}
try:
    from synapse.ml.featurize import ValueIndexer, IndexToValue
    assert spark.version == '3.5.0'
    proof['jvm_provenance'] = verify_jvm(spark, manifest['jvm_classes'])
    proof['spark'] = spark.version
    proof['core_sha256'] = hashlib.sha256(core.read_bytes()).hexdigest()

    def frame(data_type, levels):
        schema = T.StructType([
            T.StructField('label', data_type, False, {'schema-test': 'preserve-input'}),
            T.StructField('features', VectorUDT(), False, {'schema-test': 'preserve-input'})])
        return spark.createDataFrame([
            (levels[index % 2], Vectors.dense(float(index % 2), 1.0)) for index in range(16)], schema)

    def public_controls(data_type, levels, steps):
        data = frame(data_type, levels)
        before = data._jdf.schema().json()
        model = ValueIndexer(inputCol='label', outputCol='indexed').fit(data)
        indexed = model.transform(data)
        rows = indexed.select('indexed').collect()
        assert [float(row[0]) for row in rows] == [float(index % 2) for index in range(16)]
        assert data._jdf.schema().json() == before
        steps.append('append-indexer-real-values')
        decoded = IndexToValue(inputCol='indexed', outputCol='decoded').transform(indexed)
        assert [row[0] for row in decoded.select('decoded').collect()] == [levels[index % 2] for index in range(16)]
        assert data._jdf.schema().json() == before
        steps.append('append-decoder-real-values')
        assert model.copy().transform(data).select('indexed').collect() == rows
        assert data._jdf.schema().json() == before
        steps.append('model-copy')
        fitted = Pipeline(stages=[ValueIndexer(inputCol='label', outputCol='label'),
                                  LogisticRegression(maxIter=2, regParam=0.1)]).fit(data)
        predictions = fitted.transform(data).select('prediction').collect()
        assert len(predictions) == 16 and all(row[0] in (0.0, 1.0) for row in predictions)
        assert data._jdf.schema().json() == before
        steps.append('public-python-pipeline')

    def run(name, action):
        steps = []
        try:
            action(steps)
        except Exception as error:
            cause = str(error)
            if isinstance(error, MemoryError) or 'OutOfMemoryError' in cause:
                raise
            known = name == 'indexed-long' and python_failure_kind(name, cause, True) == 'independent_producer_failure'
            proof['results'].append({'name': name, 'passed': False, 'known_independent_Long': known,
                                     'steps_completed': steps, 'cause': cause[:16384]})
            if known:
                proof['known_long_signature'] = {'name': name, 'type': 'java.lang.ClassCastException',
                    'message': 'java.lang.Integer cannot be cast to class java.lang.Long',
                    'line': 'ValueIndexer.scala:169', 'counted_as_pass': False}
        else:
            proof['results'].append({'name': name, 'passed': True, 'steps_completed': steps})

    variants = [('integer', T.IntegerType(), [24, 85]), ('long', T.LongType(), [10001, 30001]),
                ('double', T.DoubleType(), [0.25, 1.75]), ('string', T.StringType(), ['negative', 'positive']),
                ('boolean', T.BooleanType(), [False, True])]
    for name, data_type, levels in variants:
        run('indexed-' + name, lambda steps, dt=data_type, values=levels: public_controls(dt, values, steps))
    for name, data_type, levels in (variants[0], variants[3]):
        def persistence(steps, dt=data_type, values=levels, label=name):
            data = frame(dt, values)
            before = data._jdf.schema().json()
            pipeline = Pipeline(stages=[ValueIndexer(inputCol='label', outputCol='indexed'),
                                        IndexToValue(inputCol='indexed', outputCol='decoded')]).fit(data)
            destination = root / (role + '-python-persistence-' + label)
            pipeline.write().save(str(destination))
            restored = PipelineModel.load(str(destination))
            rows = restored.transform(data).select('label', 'decoded').collect()
            assert len(rows) == 16 and all(row[0] == row[1] for row in rows)
            assert data._jdf.schema().json() == before
            steps.append('actual-public-Pipeline-save-load-roundtrip')
        run('persistence-' + name, persistence)
    assert len(proof['results']) == 7
    assert all(item['passed'] or item.get('known_independent_Long') for item in proof['results'])
    proof['complete'] = True
    proof['all_controls_green'] = all(item['passed'] for item in proof['results'])
finally:
    spark.stop()
    (root / (role + '-python-proof.json')).write_text(json.dumps(proof, indent=2))
    print('SCHEMA_PYTHON=' + json.dumps(proof), flush=True)
