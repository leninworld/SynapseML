"""Fresh real Spark process configured with the exact branch-built classpath."""
import json
import os
from pathlib import Path
from artifact_provenance import digest, verify_installed_python, verify_jvm
from validation_runtime import docker_only, require

spark = None

def pytest_sessionstart(session):
    global spark
    docker_only()
    manifest = json.loads(Path(os.environ['IMAGE_ARTIFACT_MANIFEST']).read_text())
    jars = []
    for item in manifest['jars']:
        path = Path(item['path'])
        require(digest(path) == item['sha256'], 'Input JAR changed')
        jars.append(str(path))
    from pyspark import SparkContext
    from pyspark.sql import SparkSession
    require(SparkContext._active_spark_context is None and SparkContext._gateway is None,
            'Pytest did not start in a fresh Spark process')
    # init_spark and the actual upstream Markdown helper subsequently reuse this
    # existing context; no published SynapseML Maven package starts this JVM.
    spark = (SparkSession.builder.master('local[2]').appName('image-writer-provenance')
             .config('spark.jars', ','.join(jars)).config('spark.jars.packages', '')
             .config('spark.ui.enabled', 'false').config('spark.sql.shuffle.partitions', '2')
             .getOrCreate())
    proof = {'python': verify_installed_python(manifest['python_modules']),
             'jvm': verify_jvm(spark, manifest['jvm_classes'])}
    with Path(os.environ['IMAGE_PROVENANCE_RESULT']).open('x') as target:
        json.dump(proof, target, indent=2)

def pytest_sessionfinish(session, exitstatus):
    if spark is not None:
        spark.stop()
