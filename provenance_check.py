"""Public synthetic installed-wheel demo and exact generated/JVM artifact provenance."""
import hashlib
import inspect
import json
import os
from pathlib import Path

from pyspark.ml.classification import LogisticRegression
from synapse.ml.core.init_spark import init_spark
from synapse.ml.lightgbm import LightGBMClassifier
from synapse.ml.stages import ClassBalancer
from synapse.ml.train import TrainClassifier

spark = init_spark()
try:
    assert spark.version == '3.5.0'
    expected = Path(os.environ['SYNAPSEML_EXPECTED_CORE_JAR']).resolve(strict=True)
    root = Path(os.environ['SYNAPSEML_EXPECTED_GENERATED_ROOT']).resolve(strict=True)
    setter = TrainClassifier.setIgnoredCols
    actual_wrapper = Path(inspect.getfile(setter)).resolve(strict=True)
    module = inspect.getmodule(setter).__name__
    generated = root / (module.replace('.', '/') + '.py')
    assert hashlib.sha256(actual_wrapper.read_bytes()).digest() == hashlib.sha256(generated.read_bytes()).digest()
    stage = TrainClassifier()
    actual = Path(stage._java_obj.getClass().getProtectionDomain().getCodeSource().getLocation().toURI().getPath()).resolve(strict=True)
    assert hashlib.sha256(actual.read_bytes()).digest() == hashlib.sha256(expected.read_bytes()).digest()
    loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
    native_class = spark._jvm.java.lang.Class.forName('com.microsoft.ml.lightgbm.lightgbmlib', False, loader)
    native_jar = Path(native_class.getProtectionDomain().getCodeSource().getLocation().toURI().getPath()).resolve(strict=True)
    native_sha = hashlib.sha256(native_jar.read_bytes()).hexdigest()
    assert native_sha == os.environ['SYNAPSEML_EXPECTED_NATIVE_JAR_SHA256'], 'RUNTIME_NATIVE_JAR_HASH'
    data = spark.createDataFrame(
        [(0.0, -2.0, 'a'), (0.0, 0.0, 'b'), (1.0, 1.0, 'c')],
        ['outcome', 'x', 'tracking'],
    )
    weighted = ClassBalancer().setInputCol('outcome').fit(data).transform(data)
    learner = LogisticRegression(labelCol='outcome', weightCol='weight', maxIter=8, regParam=0.2)
    model = TrainClassifier().setModel(learner).setIgnoredCols(['tracking']).fit(weighted)
    predictions = model.transform(weighted.drop('outcome', 'weight', 'tracking'))
    assert predictions.count() == 3
    native = (LightGBMClassifier().setLabelCol('outcome').setWeightCol('weight')
              .setNumIterations(3).setNumLeaves(3).setMinDataInLeaf(1).setNumThreads(1)
              .setNumTasks(1).setDataTransferMode('streaming'))
    native_model = TrainClassifier().setModel(native).setIgnoredCols(['tracking']).fit(weighted.coalesce(1))
    assert native_model.transform(weighted.drop('outcome', 'weight', 'tracking')).count() == 3
    print(json.dumps({'spark': spark.version, 'installed_wrapper': str(actual_wrapper),
                      'generated_source': str(generated), 'core_jvm_jar': str(actual),
                      'core_jvm_sha256': hashlib.sha256(actual.read_bytes()).hexdigest(),
                      'native_runtime_jar': str(native_jar), 'native_runtime_sha256': native_sha,
                      'weighted_lr_demo_rows': 3, 'native_wrapper_demo_rows': 3,
                      'spark_packages': spark.sparkContext.getConf().get('spark.jars.packages')}))
finally:
    spark.stop()
