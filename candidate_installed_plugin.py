"""Explicit private pytest plugin for the unchanged upstream core/testPython task.

No SDK monkeypatch or generated-file rewrite. All hooks are future Docker code,
UNRUN. Generated tests reuse a real, identity-verified Spark session.
"""
import ast
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sysconfig
import types
import unittest
import time

from candidate_generated_artifacts import wheel_modules
from candidate_python_contract import require_collection_order
from candidate_wheel_identity import verify as verify_wheel_identity
from validation_runtime import require

OLD_SHA = "e566433f3c6074ea1389d8f8fc354043757901a0e3072b6102f64669fbec28f8"
ADDED = "        if cls.owns_spark_context:\n            cls.addClassCleanup(cls.spark.stop)\n"
REMOVED = (
    "    @classmethod\n    def tearDownClass(cls):\n"
    "        if getattr(cls, \"owns_spark_context\", False) and hasattr(cls, \"spark\"):\n"
    "            cls.spark.stop()\n\n"
)


def lifecycle_controls(target, test_path, manifest, output):
    """Exercise actual setUpClass through unittest, before the full nine test methods."""
    from pyspark import SparkContext
    from pyspark.sql import SparkSession

    require(SparkContext._active_spark_context is None, "Cleanup controls need a fresh process")
    source = Path(test_path).read_text()
    require(source.count(ADDED) == 1, "Cleanup insertion differs")
    before = source.replace(ADDED, "").replace(
        "    @staticmethod\n    def estimator():", REMOVED + "    @staticmethod\n    def estimator():"
    )
    require(hashlib.sha256(before.encode()).hexdigest() == OLD_SHA,
            "Reconstructed baseline is not the exact published pre-fix test")
    module = types.ModuleType("synapseml_exact_before_cleanup_control")
    exec(compile(before, "<exact-published-before-cleanup>", "exec"), module.__dict__)
    root = Path(output)
    root.mkdir()
    bad = dict(manifest)
    bad["python_modules"] = dict(manifest["python_modules"])
    bad["python_modules"][sorted(bad["python_modules"])[0]] = "0" * 64
    bad_path = root / "controlled-python-provenance-mismatch.json"
    bad_path.write_text(json.dumps(bad, indent=2))
    original_env = os.environ.get("SYNAPSEML_DML_ARTIFACT_MANIFEST")
    require(original_env is not None, "Missing actual installed artifact manifest")
    outcomes = []

    def fail_before_first_fit():
        raise RuntimeError("CONTROLLED_CLEANUP_INITIAL_FIT_FAILURE")

    def run_case(base, label, provenance_failure, borrowed, expect_stopped):
        require(SparkContext._active_spark_context is None, "Prior control leaked a context")
        started = time.monotonic()
        shared = None
        probe = type("CleanupFailureProbe", (base,), {
            "estimator": staticmethod(fail_before_first_fit),
            "test_probe": lambda self: self.fail("Setup failure did not occur"),
        })
        try:
            if borrowed:
                shared = SparkSession.builder.master("local[2]").appName(
                    "cleanup-shared-context-control"
                ).config("spark.jars", ",".join(r["path"] for r in manifest["jars"])).getOrCreate()
                os.environ.pop("SYNAPSEML_DML_ARTIFACT_MANIFEST", None)
            else:
                os.environ["SYNAPSEML_DML_ARTIFACT_MANIFEST"] = (
                    str(bad_path) if provenance_failure else original_env
                )
            result = unittest.TestResult()
            unittest.TestSuite([probe("test_probe")]).run(result)
            require(result.testsRun == 0 and len(result.errors) == 1 and not result.failures,
                    "Control did not fail exactly once in setUpClass")
            reason = result.errors[0][1]
            expected = ("Installed branch Python module mismatch" if provenance_failure
                        else "CONTROLLED_CLEANUP_INITIAL_FIT_FAILURE")
            require(expected in reason, "Setup failed for an unrelated reason: " + reason[-1200:])
            require(probe.owns_spark_context is (not borrowed), "Wrong context ownership")
            stopped = SparkContext._active_spark_context is None
            require(stopped is expect_stopped, "Cleanup outcome differs: " + label)
            if borrowed:
                require(SparkContext._active_spark_context is shared.sparkContext
                        and shared.sparkContext._jsc is not None,
                        "Borrowed shared context was stopped or replaced")
            elif not expect_stopped:
                require(SparkContext._active_spark_context is probe.spark.sparkContext,
                        "Baseline did not leak its own context")
            outcomes.append({"case": label, "expectedSetupFailure": expected,
                             "ownsContext": probe.owns_spark_context,
                             "contextStopped": stopped, "testsRun": result.testsRun,
                             "setupErrors": len(result.errors), "realSpark": True,
                             "seconds": time.monotonic() - started})
        finally:
            # These contexts are created only by this isolated hosted control.
            if SparkSession._instantiatedSession is not None:
                SparkSession._instantiatedSession.stop()
            if SparkContext._active_spark_context is not None:
                SparkContext._active_spark_context.stop()
            require(SparkSession._instantiatedSession is None
                    and SparkContext._active_spark_context is None,
                    "Control teardown left session or context state")
            os.environ["SYNAPSEML_DML_ARTIFACT_MANIFEST"] = original_env

    run_case(module.DoubleMLMultipleTreatmentsTests, "published-before-owned-fit-failure", False, False, False)
    run_case(target, "candidate-owned-provenance-failure", True, False, True)
    run_case(target, "candidate-owned-fit-failure", False, False, True)
    run_case(target, "candidate-borrowed-fit-failure", False, True, False)
    require(SparkContext._active_spark_context is None
            and SparkSession._instantiatedSession is None, "Control teardown leaked Spark")
    record = {"baselineSHA256": OLD_SHA, "candidateSHA256": hashlib.sha256(source.encode()).hexdigest(),
              "cases": outcomes, "caseCount": len(outcomes), "realInstalledDependencies": True,
              "scientificFitsStillRequired": True}
    (root / "cleanup-controls-result.json").write_text(json.dumps(record, indent=2))
    return record


STATE = {}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def java_origins(spark, manifest):
    jvm = spark._jvm
    loader = jvm.java.lang.Thread.currentThread().getContextClassLoader()
    origins = {}
    for name in ("DoubleMLEstimator", "DoubleMLModel"):
        cls = jvm.java.lang.Class.forName(
            "com.microsoft.azure.synapse.ml.causal." + name, True, loader)
        path = Path(cls.getProtectionDomain().getCodeSource().getLocation().toURI().getPath()).resolve()
        require(path == Path(manifest["core_jar"]).resolve(), "Wrong loaded branch Java origin")
        origins[name] = str(path)
    return origins


def pytest_sessionstart(session):
    config = json.loads(Path(os.environ["SYNAPSEML_CANDIDATE_CONSUMER_CONFIG"]).read_text())
    STATE["config"] = config
    source = Path(config["source_root"]).resolve()
    for relative, expected in config["candidate_files"].items():
        path = source / relative
        require(path.is_file() and not path.is_symlink() and sha(path) == expected,
                "Candidate source changed before upstream pytest collection")
    if config["role"] == "dependency":
        STATE["dependencyOnly"] = True
        return
    prior = json.loads(Path(config["build_manifest"]).read_text())
    current = dict(prior)
    current["jars"] = [{"path": r["path"], "sha256": sha(r["path"])} for r in prior["jars"]]
    require(sum(Path(r["path"]).resolve() == Path(current["core_jar"]).resolve()
                for r in current["jars"]) == 1, "Unbound core JAR")
    # core/testPython legitimately repackages/reinstalls. Bind that actual wheel,
    # preserving the original producer manifest instead of silently overwriting it.
    wheels = prior["wheels"]
    require(len(wheels) == 1, "Wrong branch wheel set")
    wheel = Path(wheels[0]["path"])
    current["wheels"] = [{"path": str(wheel), "sha256": sha(wheel), "bytes": wheel.stat().st_size}]
    current["python_modules"] = wheel_modules(wheel)
    require(current["python_modules"] == prior["python_modules"],
            "Upstream task changed generated module bytes after the reviewed build")
    current["installed_python_root"] = str(Path(sysconfig.get_paths()["purelib"]).resolve())
    direct = json.loads(importlib.metadata.distribution("synapseml-core").read_text("direct_url.json"))
    wheel_identity = verify_wheel_identity(direct, wheel, current["wheels"][0]["sha256"])
    paths = {}
    for name, expected in current["python_modules"].items():
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        require(path.is_relative_to(Path(current["installed_python_root"])),
                "Imported checkout/generated source instead of installed wheel")
        require(sha(path) == expected, "Installed Python bytes differ from current branch wheel")
        paths[name] = str(path)
    manifest_path = Path(config["observed_manifest"])
    require(not manifest_path.exists(), "Consumer manifest replay")
    manifest_path.write_text(json.dumps(current, indent=2))
    os.environ["SYNAPSEML_DML_ARTIFACT_MANIFEST"] = str(manifest_path)
    STATE.update(manifest=current, paths=paths, installed_direct_url=direct,
                 wheel_identity=wheel_identity)
    if config["role"] == "generated":
        from pyspark import SparkContext
        from pyspark.sql import SparkSession
        require(SparkContext._active_spark_context is None, "Generated consumer is not a fresh process")
        spark = SparkSession.builder.master("local[3]").appName("candidate-generated-DML").config(
            "spark.jars", ",".join(row["path"] for row in current["jars"])).getOrCreate()
        STATE["spark"] = spark
        STATE["application_id"] = spark.sparkContext.applicationId
        STATE["origins_before"] = java_origins(spark, current)
    # Handwritten tests must create their OWN fresh context in the actual setUpClass.


def pytest_collection_modifyitems(session, config, items):
    expected = STATE["config"]
    require(len(items) == len(expected["names"]) and
            {item.name for item in items} == set(expected["names"]), "Wrong actual upstream collection")
    require(all(item.cls is not None and item.cls.__name__ == expected["class"] for item in items),
            "Wrong collected Python class")
    if "expected_first_names" in expected:
        first = list(expected["expected_first_names"])
        require_collection_order([item.name for item in items], first)
    STATE["nodeids"] = [item.nodeid for item in items]
    STATE["classes"] = {item.cls for item in items}
    STATE["test_sources"] = {str(Path(str(item.path)).resolve()) for item in items}
    require(len(STATE["test_sources"]) == 1, "Ambiguous collected test source")
    path = Path(next(iter(STATE["test_sources"])))
    actual_sha = sha(path)
    if expected["role"] == "generated":
        tree = ast.parse(path.read_text())
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == expected["class"]]
        require(len(classes) == 1, "Generated class source missing")
        methods = [node.name for node in classes[0].body if isinstance(node, ast.FunctionDef)
                   and node.name.startswith("test_")]
        require(set(methods) == set(expected["names"]) and len(methods) == len(expected["names"]),
                "Generated source does not contain both real constructor controls")
        Path(expected["generated_source_copy"]).write_bytes(path.read_bytes())
    else:
        require(actual_sha == expected["test_sha256"], "Collected authored/control source changed")
    STATE["actual_test_sha256"] = actual_sha
    if expected["role"] in ("handwritten", "generated"):
        init = path.parent / "__init__.py"
        require(init.is_file() and init.read_bytes() == b"", "Upstream did not generate empty causal init")


def pytest_sessionfinish(session, exitstatus):
    config = STATE.get("config")
    if config is None:
        return
    evidence = {"role": config["role"], "pytest_exit": int(exitstatus),
                "actual_nodeids": STATE.get("nodeids", []),
                "expected_first_names": config.get("expected_first_names", []),
                "actual_test_sources": sorted(STATE.get("test_sources", [])),
                "complete": False}
    try:
        if config["role"] == "dependency":
            require(int(exitstatus) == config["expected_exit"], "Wrong real pytest dependency outcome")
            require("nodeids" in STATE, "Dependency hook collection missing")
            evidence.update(complete=True, controlOnly=True, installedArtifactClaim=False)
            return
        require(int(exitstatus) == 0, "Upstream pytest failed")
        require("manifest" in STATE and "nodeids" in STATE, "Missing actual consumer/collection proof")
        if config["role"] == "handwritten":
            require(len(STATE["classes"]) == 1 and all(
                getattr(cls, "installed_artifact_provenance_verified", False) is True
                for cls in STATE["classes"]), "Authored setUpClass did not verify installed artifacts")
            from pyspark import SparkContext
            require(SparkContext._active_spark_context is None,
                    "Successful authored suite leaked its owned Spark context")
            STATE["cleanup_controls"] = lifecycle_controls(
                next(iter(STATE["classes"])), next(iter(STATE["test_sources"])),
                STATE["manifest"],
                Path(config["session_result"]).parent / "spark-cleanup-controls")
            require(STATE["cleanup_controls"]["caseCount"] == 4, "Missing actual cleanup controls")
            evidence["cleanup_controls"] = STATE["cleanup_controls"]
            evidence["normal_owned_cleanup_verified"] = True
        else:
            spark = STATE["spark"]
            require(spark.sparkContext.applicationId == STATE["application_id"], "Generated SDK replaced context")
            evidence["class_origins"] = java_origins(spark, STATE["manifest"])
        for row in STATE["manifest"]["jars"]:
            require(sha(row["path"]) == row["sha256"], "JAR changed during consumer")
        evidence.update(complete=True, installed_python_paths=STATE["paths"],
                        observed_manifest_sha256=sha(config["observed_manifest"]),
                        source_sha256=STATE["actual_test_sha256"],
                        installed_wheel_sha256=STATE["manifest"]["wheels"][0]["sha256"],
                        installed_wheel_identity=STATE["wheel_identity"])
    finally:
        if "spark" in STATE:
            STATE["spark"].stop()
        Path(config["session_result"]).write_text(json.dumps(evidence, indent=2))
