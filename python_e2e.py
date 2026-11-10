"""Unmodified branch-built, installed Python/Spark baseline, never a mock transport."""
import hashlib
import importlib
import json
import sys
from pathlib import Path
from pyspark.ml import Pipeline, PipelineModel
from pyspark.ml.classification import LogisticRegression, RandomForestClassifier
from pyspark.ml.evaluation import BinaryClassificationEvaluator, MulticlassClassificationEvaluator
from pyspark.ml.linalg import VectorUDT, Vectors
from pyspark.sql import SparkSession, functions as F, types as T
from artifact_provenance import verify_installed_python
from baseline_checks import python_failure_kind

root = Path('/runtime')
# Exact names from current-core MetricConstants.scala; its OID is in the packet manifest.
METRIC_COLUMNS = {'accuracy': 'accuracy', 'AUC': 'AUC', 'areaUnderPR': 'areaUnderPR'}
packet_manifest = json.loads(Path('/packet/manifest.json').read_text())
assert METRIC_COLUMNS == packet_manifest['metric_columns']
manifest = json.loads((root / 'baseline-artifact-manifest.json').read_text())
installed = verify_installed_python(manifest['python_modules'])
for item in manifest['jars']:
    assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256']
core = next(Path(x['path']) for x in manifest['jars']
            if Path(x['path']).name.startswith('synapseml-core_'))
transitives = [x['path'] for x in manifest['jars']
               if not any(t in x['path'].lower() for t in ('synapseml', 'mmlspark'))
               and not any(t in x['path'] for t in
                           ('/org.apache.spark/', '/org.scala-lang/', '/org.apache.hadoop/'))]
spark = (SparkSession.builder.master('local[2]').appName('EncodedLabelsIssue667Baseline')
         .config('spark.jars', ','.join([str(core), *transitives]))
         .config('spark.ui.enabled', 'false').config('spark.driver.memory', '2g')
         .config('spark.sql.shuffle.partitions', '1').getOrCreate())
spark.sparkContext.setLogLevel('WARN')
results = []
proof = {'baseline_complete': False, 'source_kind': 'UNMODIFIED_BASELINE', 'results': results}
try:
    from synapse.ml.train import ComputeModelStatistics, TrainClassifier, TrainedClassifierModel
    from synapse.ml.featurize import ValueIndexer, ValueIndexerModel, IndexToValue
    from synapse.ml.automl import TuneHyperparameters, FindBestModel
    from synapse.ml.automl.HyperparamBuilder import GridSpace
    assert spark.version == '3.5.0'
    for obj in (ComputeModelStatistics(), ValueIndexer(), TuneHyperparameters()):
        location = obj._java_obj.getClass().getProtectionDomain().getCodeSource().getLocation().toString()
        assert location == 'file:' + str(core), location
    proof.update(spark=spark.version, branch_core_jar_sha256=hashlib.sha256(core.read_bytes()).hexdigest(),
                 installed_modules=installed)

    def frame(data_type, levels):
        schema = T.StructType([T.StructField('label', data_type, False),
                               T.StructField('features', VectorUDT(), False)])
        return spark.createDataFrame([
            (levels[i % len(levels)], Vectors.dense([
                1.0 if j == i % len(levels) else 0.0 for j in range(len(levels))]))
            for i in range(24 * len(levels))], schema)

    def indexed(data):
        return ValueIndexer(inputCol='label', outputCol='label').fit(data).transform(data)

    def learner():
        return LogisticRegression(maxIter=12, regParam=0.01, tol=1e-6)

    def metric(scored, name='accuracy'):
        value = ComputeModelStatistics(labelCol='label', scoredLabelsCol='prediction',
                                       scoresCol='rawPrediction', evaluationMetric=name)
        return value.transform(scored).first()[METRIC_COLUMNS[name]]

    def accuracy(scored):
        reference = MulticlassClassificationEvaluator(metricName='accuracy').evaluate(scored)
        actual = metric(scored)
        assert abs(actual - reference) < 1e-10, 'CMS_LABEL_ENCODING_REGRESSION'

    def run(name, action, regression=False):
        try:
            action()
        except Exception as error:
            message = str(error)
            kind = python_failure_kind(name, message, regression)
            results.append({'name': name, 'expected_product_failure': kind == 'expected_product_failure',
                            'independent_producer_failure': kind == 'independent_producer_failure',
                            'unexpected': kind == 'unexpected', 'passed': False, 'cause': message[:16384]})
            if isinstance(error, MemoryError) or kind == 'fatal_resource_failure':
                raise
        else:
            results.append({'name': name, 'expected_product_failure': False, 'passed': True})

    variants = [('int', T.IntegerType(), [24, 85]), ('long', T.LongType(), [10001, 30001]),
                ('double', T.DoubleType(), [0.25, 1.75]),
                ('string', T.StringType(), ['negative', 'positive']),
                ('boolean', T.BooleanType(), [False, True]),
                ('multiclass', T.IntegerType(), [10, 20, 30])]
    for name, data_type, levels in variants:
        def direct(dt=data_type, ls=levels):
            data = indexed(frame(dt, ls))
            accuracy(learner().fit(data).transform(data))
        run('indexed-' + name, direct, True)

    def subset():
        data = indexed(frame(T.IntegerType(), [1, 2]))
        scored = learner().fit(data).transform(data).filter(F.col('label') == 1)
        assert scored.count() > 0
        accuracy(scored)
    run('silent-subset', subset, True)

    for name in ('AUC', 'areaUnderPR'):
        for sparse in (False, True):
            def binary(n=name, sp=sparse):
                data = indexed(frame(T.IntegerType(), [24, 85]))
                scored = learner().fit(data).transform(data)
                if sp:
                    to_sparse = F.udf(lambda v: Vectors.sparse(len(v), [(i, float(x)) for i, x in enumerate(v) if x != 0]), VectorUDT())
                    scored = scored.withColumn('rawPrediction', to_sparse(F.col('rawPrediction')))
                target = 'areaUnderROC' if n == 'AUC' else n
                reference = BinaryClassificationEvaluator(metricName=target).evaluate(scored)
                assert abs(metric(scored, n) - reference) < 1e-10, 'CMS_LABEL_ENCODING_REGRESSION'
            run(name + '-sparse-' + str(sparse), binary, True)

    def train_indexed():
        data = indexed(frame(T.IntegerType(), [24, 85]))
        model = TrainClassifier(model=learner(), labelCol='label', featuresCol='mlfeatures',
                                numFeatures=32).fit(data)
        accuracy(model.transform(data))
    run('train-indexed', train_indexed, True)

    def explicit_labels():
        data = frame(T.IntegerType(), [0, 1])
        model = TrainClassifier(model=learner(), labelCol='label', featuresCol='mlfeatures',
                                numFeatures=32, reindexLabel=False, labels=['negative', 'positive']).fit(data)
        accuracy(model.transform(data))
    run('explicit-labels', explicit_labels, True)

    for name in ('LR', 'RF'):
        def tune(n=name):
            data = indexed(frame(T.IntegerType(), [24, 85]))
            model = learner() if n == 'LR' else RandomForestClassifier(numTrees=3, maxDepth=2, seed=11)
            result = TuneHyperparameters(models=[model], paramSpace=GridSpace([]).space(),
                                         numFolds=2, numRuns=1, parallelism=1, seed=11,
                                         evaluationMetric='accuracy').fit(data)
            assert 0 <= result.getBestMetric() <= 1
        run('tuning-' + name, tune, True)

    def best_model():
        data = indexed(frame(T.IntegerType(), [24, 85]))
        model = TrainClassifier(model=learner(), labelCol='label', featuresCol='mlfeatures',
                                numFeatures=32).fit(data)
        result = FindBestModel(models=[model], evaluationMetric='accuracy').fit(data)
        assert result.getBestModelMetrics().count() == 1
    run('find-best', best_model, True)

    def persisted():
        data = frame(T.IntegerType(), [24, 85])
        model = Pipeline(stages=[ValueIndexer(inputCol='label', outputCol='label'),
            learner(),
            ComputeModelStatistics(labelCol='label', scoredLabelsCol='prediction',
                                   scoresCol='rawPrediction', evaluationMetric='accuracy')]).fit(data)
        path = '/runtime/python-pipeline'
        model.write().save(path)
        result = PipelineModel.load(path).transform(data)
        assert abs(result.first()['accuracy'] - 1.0) < 1e-10, 'CMS_LABEL_ENCODING_REGRESSION'
    run('pipeline-persistence', persisted, True)

    def trained_persisted():
        data = indexed(frame(T.IntegerType(), [24, 85]))
        model = TrainClassifier(model=learner(), labelCol='label', featuresCol='mlfeatures',
                                numFeatures=32).fit(data)
        model.write().save('/runtime/python-trained-model')
        evaluator = ComputeModelStatistics(labelCol='label', scoredLabelsCol='prediction',
                                           scoresCol='rawPrediction', evaluationMetric='accuracy')
        evaluator.write().save('/runtime/python-trained-evaluator')
        scored = TrainedClassifierModel.load('/runtime/python-trained-model').transform(data)
        actual = ComputeModelStatistics.load('/runtime/python-trained-evaluator').transform(scored)
        reference = MulticlassClassificationEvaluator(metricName='accuracy').evaluate(scored)
        assert abs(actual.first()['accuracy'] - reference) < 1e-10, 'CMS_LABEL_ENCODING_REGRESSION'
    run('trained-model-evaluator-persistence', trained_persisted, True)

    for name, data_type, levels in variants[:-1]:
        def raw(dt=data_type, ls=levels):
            data = frame(dt, ls)
            model = TrainClassifier(model=learner(), labelCol='label', featuresCol='mlfeatures',
                                    numFeatures=32).fit(data)
            scored = model.transform(data)
            mapping = {value: index for index, value in enumerate(ls)}
            pairs = scored.select('label', 'prediction').collect()
            reference = sum(mapping[row[0]] == row[1] for row in pairs) / len(pairs)
            assert abs(metric(scored) - reference) < 1e-10
        run('raw-' + name, raw)

    def identity():
        data = indexed(frame(T.IntegerType(), [0, 1]))
        accuracy(learner().fit(data).transform(data))
    run('identity-control', identity)

    def decoder():
        data = frame(T.IntegerType(), [24, 85])
        model = ValueIndexer(inputCol='label', outputCol='label').fit(data)
        path = '/runtime/python-indexer'
        model.write().save(path)
        decoded = IndexToValue(inputCol='label', outputCol='decoded').transform(
            ValueIndexerModel.load(path).transform(data))
        assert decoded.schema['decoded'].dataType == T.IntegerType()
        assert sorted(r[0] for r in decoded.select('decoded').collect()) == sorted(
            r[0] for r in data.select('label').collect())
    run('indexer-decoder-persistence-control', decoder)

    def copied():
        data = indexed(frame(T.IntegerType(), [24, 85]))
        scored = learner().fit(data).transform(data)
        accuracy(scored.withColumn('label', F.col('label').alias('label',
                 metadata=scored.schema['label'].metadata)))
    run('metadata-copy', copied, True)

    def stripped():
        data = indexed(frame(T.IntegerType(), [24, 85]))
        scored = learner().fit(data).transform(data)
        schema = T.StructType([T.StructField(f.name, f.dataType, f.nullable,
                               {} if f.name == 'label' else f.metadata) for f in scored.schema.fields])
        stripped = spark.createDataFrame(scored.rdd, schema)
        assert stripped.schema['label'].metadata == {}
        accuracy(stripped)
    run('metadata-stripped-control', stripped)
    required = {'indexed-int', 'silent-subset', 'tuning-LR', 'tuning-RF'}
    assert required <= {x['name'] for x in results if x['expected_product_failure']}
    expected_names = packet_manifest['expected_python_case_names']
    actual_names = [x['name'] for x in results]
    assert len(actual_names) == len(set(actual_names)) and set(actual_names) == set(expected_names)
    proof['independent_producer_failures'] = [x for x in results if x.get('independent_producer_failure')]
    proof['independent_producer_qualification'] = (
        'Recorded pre-existing producer failure, never a pass; future candidate must match exact signature. '
        'This case has not validated the CMS Long path through Python.')
    unexpected = [x['name'] for x in results if x.get('unexpected')]
    assert not unexpected, 'Unexpected baseline failures: ' + ', '.join(unexpected)
    proof['baseline_complete'] = True
finally:
    (root / 'python-e2e-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    spark.stop()
