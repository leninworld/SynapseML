"""Installed branch-built Python API baseline; this never validates a fix."""
import hashlib
import json
import sys
from pathlib import Path

from pyspark.ml.linalg import Vectors
from pyspark.sql import SparkSession, functions as F, types as T

from artifact_provenance import verify_installed_python, verify_jvm


root = Path('/runtime')
role = sys.argv[1]
assert role in ('baseline', 'candidate')
manifest = json.loads((root / (role + '-artifact-manifest.json')).read_text())
installed = verify_installed_python(manifest['python_modules'])
for row in manifest['jars']:
    assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest() == row['sha256']
artifacts = [next(Path(row['path']) for row in manifest['jars']
                 if Path(row['path']).name.startswith('synapseml-' + module + '_'))
             for module in ('core', 'vw')]
transitives = [row['path'] for row in manifest['jars']
               if not any(term in row['path'].lower() for term in ('synapseml', 'mmlspark'))
               and '/org.apache.spark/' not in row['path']
               and '/org.scala-lang/' not in row['path']
               and '/org.apache.hadoop/' not in row['path']]
spark = (SparkSession.builder.master('local[2]').appName('RawPredictionBaseline643')
         .config('spark.jars', ','.join([str(p) for p in artifacts] + transitives))
         .config('spark.ui.enabled', 'false').config('spark.driver.memory', '2g')
         .config('spark.sql.shuffle.partitions', '1').getOrCreate())
try:
    assert spark.version == '3.5.0'
    jvm = verify_jvm(spark, manifest['jvm_classes'])
    driver_heap = spark.sparkContext._jvm.java.lang.Runtime.getRuntime().maxMemory()
    assert driver_heap == 2147483648, driver_heap
    from synapse.ml.vw import VowpalWabbitClassifier, VowpalWabbitClassificationModel
    from pyspark.ml.param.shared import HasRawPredictionCol
    assert isinstance(VowpalWabbitClassifier(), HasRawPredictionCol)
    binary = spark.createDataFrame([(0, 0.0, Vectors.dense([1.0, 0.0])),
                                    (1, 1.0, Vectors.dense([0.0, 1.0]))],
                                   ['id', 'label', 'features']).coalesce(1)
    multi = spark.createDataFrame([(0, 0.0, Vectors.dense([1.0, 0.0])),
                                   (1, 1.0, Vectors.dense([0.0, 1.0])),
                                   (2, 2.0, Vectors.dense([1.0, 1.0]))],
                                  ['id', 'label', 'features']).coalesce(1)
    observations = []

    def observe(name, model, data):
        before = data.schema.json()
        try:
            declared = T.StructType.fromJson(json.loads(model._java_obj.transformSchema(data._jdf.schema()).json()))
            actual = model.transform(data)
            rows = actual.orderBy('id').toJSON().collect()
            record = {'name': name, 'status': 'observed', 'declared': declared.jsonValue(),
                      'actual': actual.schema.jsonValue(), 'rows': rows,
                      'rawColumn': model.getRawPredictionCol(), 'probabilityColumn': model.getProbabilityCol(),
                      'predictionColumn': model.getPredictionCol()}
        except Exception as error:
            record = {'name': name, 'status': 'error', 'error': str(error)[:4096]}
        assert data.schema.json() == before, 'Input schema mutated'
        observations.append(record)
        return record

    def inspect(name, model, data):
        original = observe(name, model, data)
        assert original['status'] == 'observed', original
        copied = model.copy({model.rawPredictionCol: 'rawCustom',
                             model.probabilityCol: 'probCustom', model.predictionCol: 'predCustom'})
        observe(name + '-custom', copied, data)
        for parameter in (model.rawPredictionCol, model.probabilityCol, model.predictionCol):
            observe(name + '-disabled-' + parameter.name, model.copy({parameter: ''}), data)
        target = str(root / ('python-saved-' + role + '-' + name))
        model.write().save(target)
        restored = VowpalWabbitClassificationModel.load(target)
        loaded = observe(name + '-reloaded', restored, data)
        assert loaded['status'] == 'observed' and loaded['rows'] == original['rows']

    for name, link in (('unlinked', ''), ('linked-space', ' --link logistic'),
                       ('linked-equals', ' --link=logistic')):
        model = (VowpalWabbitClassifier().setLabelConversion(True).setNumPasses(2)
                 .setUseBarrierExecutionMode(False)
                 .setPassThroughArgs('--quiet --holdout_off --loss_function logistic' + link).fit(binary))
        inspect(name, model, binary)
    for name, indexing in (('zero', '--indexing 0'), ('one', '--indexing 1'), ('unspecified', '')):
        data = multi if name == 'zero' else multi.withColumn('label', F.col('label') + 1)
        model = (VowpalWabbitClassifier().setNumClasses(3).setNumPasses(2)
                 .setUseBarrierExecutionMode(False)
                 .setPassThroughArgs('--oaa 3 --quiet --holdout_off --loss_function logistic ' + indexing)
                 .fit(data))
        inspect('oaa-' + name, model, data)
        inspect('scores-' + name, model.copy({model.testArgs: '--scores'}), data)
        inspect('probabilities-' + name, model.copy({model.testArgs: '--probabilities'}), data)
    first = next(row for row in observations if row['name'] == 'unlinked')
    actual = {field['name']: field for field in first['actual']['fields']}
    declared = {field['name']: field for field in first['declared']['fields']}
    if role == 'baseline':
        assert actual['rawPrediction']['type'] == 'double'
    else:
        assert 'VectorUDT' in actual['rawPrediction']['type']['class']
    assert 'VectorUDT' in declared['rawPrediction']['type']['class']
    proof = {'kind': 'candidate' if role == 'candidate' else 'unmodified-master-baseline-only',
             'baseline_schema_gap_reproduced': role == 'baseline', 'candidate_validated': False,
             'source': manifest['source_commit'], 'installed_modules': installed,
             'jvm': jvm, 'actual_driver_heap_bytes': driver_heap, 'observations': observations,
             'disabled_column_errors': [r for r in observations if r['status'] == 'error']}
    (root / ('python-' + role + '-e2e-proof.json')).write_text(json.dumps(proof, indent=2) + '\n')
    print('RAW643_INSTALLED_BASELINE_RECORDED', len(observations), flush=True)
finally:
    spark.stop()
