"""Unexecuted Docker-only validation payload for SynapseML #1850.

The controller must invoke write and read in separate Python processes, retain
both exit statuses, and verify the installed wheels/JARs against the source
build manifest before invoking this file. No Maven package resolution is used.
"""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse
import uuid


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_installed_python(expected):
    proofs = {}
    for name, sha in expected.items():
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve(strict=True)
        require(path.is_relative_to(Path(sys.prefix).resolve()),
                f"Module outside installed environment: {name}: {path}")
        actual = digest(path)
        require(actual == sha, f"Installed module hash mismatch: {name}")
        proofs[name] = {"path": str(path), "sha256": actual}
    return proofs


def verify_jvm(spark, expected):
    loader = spark._jvm.java.lang.Thread.currentThread().getContextClassLoader()
    proofs = {}
    for name, sha in expected.items():
        cls = loader.loadClass(name)
        location = cls.getProtectionDomain().getCodeSource().getLocation().toString()
        parsed = urlparse(location)
        require(parsed.scheme == "file" and not parsed.netloc,
                f"Nonlocal code source: {name}: {location}")
        path = Path(unquote(parsed.path)).resolve(strict=True)
        require(path.is_file() and path.suffix == ".jar",
                f"Expected built JAR, not class directory: {name}: {path}")
        actual = digest(path)
        require(actual == sha, f"Loaded JAR hash mismatch: {name}")
        proofs[name] = {"path": str(path), "sha256": actual}
    return proofs


def main():
    require(sys.platform == "linux" and Path("/.dockerenv").is_file(),
            "This project validation payload may run only in approved Docker")
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["write", "read", "legacy-write", "legacy-read"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    require(manifest["source_commit"] ==
            "d3f3af6bd3e4b7d191f51c9eb29f9aeffe788193", "Wrong base")
    legacy = args.role.startswith("legacy-")
    writing = args.role in ("write", "legacy-write")
    expected_kind = "baseline" if args.role == "legacy-write" else "candidate"
    require(manifest["source_kind"] == expected_kind, "Wrong production variant")
    if expected_kind == "candidate":
        require(len(manifest["tested_patch_sha256"]) == 64, "Missing tested patch hash")
    else:
        require(manifest["tested_patch_sha256"] is None, "Baseline includes a candidate patch")
    jars = [Path(item["path"]).resolve(strict=True) for item in manifest["jars"]]
    require(jars and len(set(jars)) == len(jars), "Missing/duplicate JARs")
    for path, item in zip(jars, manifest["jars"]):
        require(digest(path) == item["sha256"], f"Input JAR mismatch: {path}")
    required_python = {
        "synapse.ml.automl.TuneHyperparametersModel",
        "synapse.ml.automl._TuneHyperparametersModel",
        "synapse.ml.automl.TuneHyperparameters",
        "synapse.ml.lightgbm.LightGBMClassifier",
        "synapse.ml.train.TrainClassifier",
    }
    required_jvm = {
        "com.microsoft.azure.synapse.ml.automl.TuneHyperparametersModel",
        "com.microsoft.azure.synapse.ml.lightgbm.LightGBMClassifier",
    }
    require(required_python <= manifest["python_modules"].keys(),
            "Incomplete installed Python provenance")
    require(required_jvm <= manifest["jvm_classes"].keys(),
            "Incomplete loaded JVM provenance")
    # Create Spark before any generated SynapseML import. Installed JARs are
    # explicit; init_spark's public Maven fallback is intentionally not used.
    from pyspark import SparkContext
    from pyspark.sql import SparkSession

    require(SparkContext._active_spark_context is None, "Existing Spark driver")
    require(SparkContext._gateway is None, "Existing JVM gateway")

    spark = (SparkSession.builder.master("local[1]")
             .appName(f"model-info-fresh-{args.role}")
             .config("spark.ui.enabled", "false")
             .config("spark.jars", ",".join(map(str, jars)))
             .config("spark.jars.packages", "")
             .config("spark.sql.shuffle.partitions", "1")
             .getOrCreate())
    best = None
    try:
        require(not spark.sparkContext.getConf().get("spark.jars.packages", ""),
                "Unexpected Maven package resolution")
        python_proof = verify_installed_python(manifest["python_modules"])
        jvm_proof = verify_jvm(spark, manifest["jvm_classes"])
        from synapse.ml.automl import TuneHyperparameters
        from synapse.ml.automl.HyperparamBuilder import (
            DiscreteHyperParam, HyperparamBuilder, RandomSpace,
        )
        from synapse.ml.automl.TuneHyperparametersModel import TuneHyperparametersModel
        from synapse.ml.lightgbm import LightGBMClassifier
        from synapse.ml.train import TrainClassifier

        data = spark.range(64).selectExpr(
            "id", "cast(id % 8 - 4 as double) as x",
            "cast(id % 8 >= 4 as double) as label",
        ).coalesce(1)
        if writing:
            require(not args.state.exists(), "Writer state already exists")
            args.state.mkdir(parents=True)
            learner = LightGBMClassifier(
                numIterations=2, numTasks=1, numThreads=1,
                minDataInLeaf=1, verbosity=-1,
                passThroughArgs="feature_pre_filter=false",
            )
            trainer = TrainClassifier(model=learner, labelCol="label", inputCols=["x"])
            search = (HyperparamBuilder()
                      .addHyperparam(learner, learner.numLeaves, DiscreteHyperParam([7]))
                      .build())
            best = TuneHyperparameters(
                models=[trainer], paramSpace=RandomSpace(search).space(),
                evaluationMetric="accuracy", numFolds=2, numRuns=1,
                parallelism=1, seed=7,
            ).fit(data)
        else:
            expected = json.loads((args.state / "expected.json").read_text())
            if legacy:
                require(expected["source_kind"] == "baseline" and
                        expected["source_commit"] == manifest["source_commit"],
                        "Legacy artifact was not produced by the original baseline")
            else:
                require(expected["manifest_sha256"] == digest(args.manifest),
                        "Writer/reader manifest mismatch")
            best = TuneHyperparametersModel.load(str(args.state / "model"))
        info = best.getBestModelInfo()
        if not legacy:
            require("numLeaves: 7" in info, "Training-only parameter missing")
            require(best.getBestModelParamsInfo() == info, "Generated getter mismatch")
        elif not writing:
            snapshot = best._java_obj.getParam("bestModelParamsInfo")
            require(not best._java_obj.isDefined(snapshot), "Legacy snapshot unexpectedly set")
            formatter = spark._jvm.com.microsoft.azure.synapse.ml.automl.EvaluationUtils
            require(info == formatter.modelParamsToString(best._java_obj.getBestModel()),
                    "Legacy getter changed the original formatter's exact behavior")
        # The legacy formatter uses raw object toString values, including the
        # native booster's process-local identity. Preserve that formatter on
        # the same loaded object; do not demand cross-process identity strings.
        if not legacy:
            require(best.copy({}).getBestModelInfo() == info, "Copy changed report")
        native = best.getBestModel()._java_obj.getLastStage()
        require(not native.hasParam("numLeaves"), "Oracle relies on fitted parameter")
        predictions = [row.asDict() for row in best.transform(data)
                       .select("id", "label", "prediction").orderBy("id").collect()]
        require(len(predictions) == 64 and all(
            row["label"] == row["prediction"] for row in predictions
        ), "Synthetic analytical prediction oracle failed")
        result = {
            "role": args.role, "pid": os.getpid(), "process_nonce": str(uuid.uuid4()),
            "manifest_sha256": digest(args.manifest), "info": info,
            "source_kind": manifest["source_kind"], "source_commit": manifest["source_commit"],
            "predictions": predictions, "python": python_proof, "jvm": jvm_proof,
        }
        if writing:
            best.write().save(str(args.state / "model"))
            (args.state / "expected.json").write_text(json.dumps(result, indent=2) + "\n")
        else:
            if not legacy:
                require(info == expected["info"], "Fresh driver changed report")
            require(predictions == expected["predictions"], "Fresh driver changed predictions")
        # Separate result files plus controller-owned subprocess and cgroup
        # records are required evidence; writing this file alone is not a pass.
        with args.result.open("x") as output:
            json.dump(result, output, indent=2)
            output.write("\n")
    finally:
        try:
            if best is not None:
                best.getBestModel()._java_obj.getLastStage().getLightGBMBooster().freeNativeMemory()
        finally:
            spark.stop()


if __name__ == "__main__":
    main()
