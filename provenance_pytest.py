"""Actual installed-artifact Spark provenance for the approved pytest processes."""

import json
import os
from pathlib import Path

from fresh_driver_model_info import verify_installed_python, verify_jvm
from validation_runtime import docker_only


spark = None


def pytest_sessionstart(session):
    global spark
    docker_only()
    manifest = json.loads(Path(os.environ["MODEL_INFO_ARTIFACT_MANIFEST"]).read_text())
    from synapse.ml.core.init_spark import init_spark

    spark = init_spark()
    result = {"python": verify_installed_python(manifest["python_modules"]),
              "jvm": verify_jvm(spark, manifest["jvm_classes"])}
    with Path(os.environ["MODEL_INFO_PROVENANCE_RESULT"]).open("x") as output:
        json.dump(result, output, indent=2)


def pytest_sessionfinish(session, exitstatus):
    if spark is not None:
        spark.stop()
