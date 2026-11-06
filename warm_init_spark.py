"""Unexecuted baseline Ivy-resolution calibration using real upstream init_spark."""

import argparse
import hashlib
import json
from pathlib import Path

from fresh_driver_model_info import verify_installed_python, verify_jvm
from validation_runtime import docker_only, require


def main():
    docker_only()
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    require(manifest["source_kind"] == "baseline", "Warm-up requires original baseline artifacts")
    from synapse.ml.core.init_spark import init_spark

    spark = init_spark()
    try:
        require(spark.version == "3.5.0", "Unexpected Spark version")
        require(spark.range(1).count() == 1, "Real baseline Spark warm-up failed")
        result = {"manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                  "spark_version": spark.version, "range_count": 1,
                  "python": verify_installed_python(manifest["python_modules"]),
                  "jvm": verify_jvm(spark, manifest["jvm_classes"]),
                  "purpose": "Measured baseline upstream init_spark/Ivy closure; not a candidate test"}
        with args.result.open("x") as output:
            json.dump(result, output, indent=2)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
