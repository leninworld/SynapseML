"""Disabled candidate installed lane composition for one future whole review.

Uses the existing Lab/Runtime, Azure Black ratchet, upstream core/testPython,
and measured G0 artifact builder. No source edits, pytest monkeypatch or run here.
"""
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from candidate_generated_artifacts import artifact_manifest, wheel_modules, MODULES
from candidate_python_contract import (
    report, fixture_controls, HANDWRITTEN_CLASS, HANDWRITTEN_NAMES, EARLY_NAMES,
    GENERATED_CLASS, GENERATED_NAMES, require_collection_order,
)
from format_checks import azure_black
from validation_runtime import require
from candidate_wheel_identity import early_controls as wheel_identity_controls

TEST_PATH = "core/src/test/python/synapsemltest/causal/test_double_ml_multiple_treatments.py"
BLACK_REFERENCE_SHA = "ea886ac2620016b6c0853dbb05cfbf7bde148743dc5d7b18daf59fd21044ae85"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def pytest_environment(config_path, packet):
    changes = {"SYNAPSEML_CANDIDATE_CONSUMER_CONFIG": str(config_path),
               "PYTHONPATH": str(packet), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
               "PYTEST_ADDOPTS": "-p candidate_installed_plugin -p no:cacheprovider"}
    saved = {name: os.environ.get(name) for name in changes}
    os.environ.update(changes)
    try:
        yield
    finally:
        for name, old in saved.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


def remaining(lane, required):
    require(lane.deadline - time.time() >= required, "HOLD insufficient original candidate clock")


def source_identity(lane, label):
    files = lane.manifest["candidate_files"]
    require(len(files) == 10, "Expected exact frozen ten-file feature")
    for relative, expected in files.items():
        path = lane.source / relative
        require(path.is_file() and not path.is_symlink() and sha(path) == expected,
                "Candidate postimage differs: " + relative)
    raw = lane.command(label, ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], 20)
    records = [row for row in raw.split("\0") if row]
    require(all(len(row) >= 4 and row[:2] in (" M", "??", "A ", "AM", "M ") for row in records),
            "Unexpected source status or rename")
    require(len(records) == len(files) and {row[3:] for row in records} == set(files),
            "Changed source footprint differs from frozen ten")
    return {"candidate_files": files, "status_sha256": hashlib.sha256(raw.encode()).hexdigest()}


def subtest_dependency_controls(lane, root):
    """Real isolated pinned-pytest accounting before expensive project builds."""
    import xml.etree.ElementTree as ET
    control = root / "real-subtest-controls"
    control.mkdir()
    ini = control / "pytest.ini"
    ini.write_text("[pytest]\n")
    conftest = control / "conftest.py"
    conftest.write_text(
        "import hashlib,json,os,pytest\nfrom pathlib import Path\n"
        "def pytest_sessionfinish(session,exitstatus):\n"
        "    config=session.config\n"
        "    record=dict(version=pytest.__version__,rootdir=str(config.rootpath.resolve()),"
        "configfile=str(config.inipath.resolve()),ini_sha256=hashlib.sha256(config.inipath.read_bytes()).hexdigest(),"
        "autoload_disabled=os.environ.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD')=='1',"
        "consumer_absent=not config.pluginmanager.hasplugin('candidate_installed_plugin'),"
        "cache_absent=not config.pluginmanager.hasplugin('cacheprovider'),exitstatus=int(exitstatus))\n"
        "    Path(os.environ['SYNAPSEML_SUBTEST_CONTROL_RECEIPT']).write_text(json.dumps(record))\n")
    test = control / "test_real_subtest_accounting.py"
    test.write_text("import unittest\nclass SubtestFixture(unittest.TestCase):\n"
                    "    def test_subtests(self):\n"
                    "        for i in range(2):\n"
                    "            with self.subTest(i=i):\n"
                    "                self.assertTrue(True)\n")

    def run_control(label, source, junit, exit_code):
        receipt = control / (label + "-configuration.json")
        updates = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "",
                   "SYNAPSEML_SUBTEST_CONTROL_RECEIPT": str(receipt)}
        saved = {name: os.environ.get(name) for name in updates}
        consumer = os.environ.pop("SYNAPSEML_CANDIDATE_CONSUMER_CONFIG", None)
        os.environ.update(updates)
        try:
            lane.command(label, ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                 "-c", str(ini), "--rootdir=" + str(control),
                                 "--junitxml=" + str(junit), str(source)],
                         20, expected=(exit_code,), cwd=control)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if consumer is not None:
                os.environ["SYNAPSEML_CANDIDATE_CONSUMER_CONFIG"] = consumer
        require(receipt.is_file(), "Missing actual subtest configuration receipt")
        actual = json.loads(receipt.read_text())
        require(actual["version"] == lane.manifest["candidate_python"]["pytest_version"],
                "Wrong subtest-control pytest version")
        require(actual["rootdir"] == str(control.resolve()) and actual["configfile"] == str(ini.resolve())
                and actual["ini_sha256"] == sha(ini), "Project or unexpected subtest-control configuration")
        require(actual["autoload_disabled"] and actual["consumer_absent"] and actual["cache_absent"]
                and actual["exitstatus"] == exit_code, "Subtest control isolation differs")
        return actual

    junit = control / "real-subtest-positive.xml"
    positive_config = run_control("real-pytest-subtest-accounting", test, junit, 0)
    accepted = report(junit, ("test_subtests",), "SubtestFixture", expected_subtests=2)
    negatives = []
    reasons = {"wrong-total-low": "Wrong pytest total", "wrong-total-high": "Wrong pytest total",
               "hidden-failure": "Unexpected pytest failures", "hidden-error": "Skipped/error pytest case",
               "hidden-skip": "Skipped/error pytest case"}
    for label, expected_reason in reasons.items():
        tree = ET.fromstring(junit.read_bytes())
        suite = tree if tree.tag == "testsuite" else tree.find("testsuite")
        if label.startswith("wrong-total"):
            suite.set("tests", "2" if label.endswith("low") else "4")
        else:
            tag = {"hidden-failure": "failure", "hidden-error": "error", "hidden-skip": "skipped"}[label]
            ET.SubElement(suite.find("testcase"), tag)
        target = control / ("real-subtest-" + label + ".xml")
        target.write_bytes(ET.tostring(tree))
        try:
            report(target, ("test_subtests",), "SubtestFixture", expected_subtests=2)
        except RuntimeError as error:
            require(str(error) == expected_reason, "Wrong subtest-control rejection: " + label)
            negatives.append({"case": label, "reason": str(error), "xmlSHA256": sha(target)})
        else:
            raise RuntimeError("Accepted invalid real-subtest report: " + label)
    failure_test = control / "test_real_subtest_failure.py"
    failure_test.write_text(test.read_text().replace("self.assertTrue(True)", "self.assertTrue(i == 0)"))
    failure_xml = control / "real-subtest-failure.xml"
    failure_config = run_control("real-pytest-failed-subtest-accounting", failure_test, failure_xml, 1)
    require(failure_xml.is_file(), "Missing actual failed-subtest XML")
    failure_tree = ET.fromstring(failure_xml.read_bytes())
    failure_suites = [failure_tree] if failure_tree.tag == "testsuite" else list(failure_tree)
    require(failure_suites and sum(int(x.get("failures", "0")) for x in failure_suites) >= 1,
            "Actual failed-subtest XML has no failure total")
    try:
        report(failure_xml, ("test_subtests",), "SubtestFixture", expected_subtests=2)
    except RuntimeError as error:
        require(str(error) in ("Unexpected pytest failures", "Exact pytest case names differ",
                              "Wrong pytest total", "Wrong pytest failure total"),
                "Wrong actual failed-subtest rejection")
        negatives.append({"case": "actual-failed-subtest", "reason": str(error),
                          "xmlSHA256": sha(failure_xml)})
    else:
        raise RuntimeError("Accepted actual failed subtest")
    return {"positive": accepted, "negativeCases": negatives, "expectedSubtests": 2,
            "positiveConfiguration": positive_config, "failureConfiguration": failure_config,
            "projectTestsRun": False, "realDependency": True}


def dependency_controls(lane):
    root = lane.root / "candidate-python-controls"
    root.mkdir()
    wheel_identity = wheel_identity_controls(lane, root / "real-wheel-identity")
    require(wheel_identity["caseCount"] == 8, "Incomplete real wheel identity controls")
    xml = fixture_controls(root / "xml")
    require(xml["caseCount"] == 10 and len(xml["cases"]) == 10, "Missing XML controls")
    import zipfile
    positive = root / "positive.whl"
    with zipfile.ZipFile(positive, "w") as wheel:
        for member in MODULES.values():
            wheel.writestr(member, b"# controlled helper fixture\n")
    require(len(wheel_modules(positive)) == len(MODULES), "Wheel positive failed")
    negative = root / "missing-init.whl"
    with zipfile.ZipFile(negative, "w") as wheel:
        for name, member in MODULES.items():
            if name != "synapse.ml.core.init_spark":
                wheel.writestr(member, b"# controlled helper fixture\n")
    try:
        wheel_modules(negative)
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Wheel missing actual init_spark module accepted")
    versions = json.loads(lane.command(
        "candidate-pytest-dependency",
        ["python", "-c", "import json,pytest,pyspark;print(json.dumps(dict(pytest=pytest.__version__,spark=pyspark.__version__)))"],
        20, cwd=root))
    require(versions == {"pytest": lane.manifest["candidate_python"]["pytest_version"], "spark": "3.5.0"},
            "Unpriced pytest/Spark dependency")
    outcomes = []
    for label, value, exit_code in (("positive", "True", 0), ("negative", "False", 1)):
        test = root / ("test_dependency_" + label + ".py")
        control_names = HANDWRITTEN_NAMES if exit_code == 0 else ("test_early",)
        test.write_text("import unittest\nclass DependencyFixture(unittest.TestCase):\n" +
                        "".join("    def "+name+"(self):\n        self.assertTrue("+value+")\n"
                                for name in control_names))
        config = {"role": "dependency", "class": "DependencyFixture", "names": control_names,
                  "source_root": str(lane.source), "candidate_files": lane.manifest["candidate_files"],
                  "test_sha256": sha(test), "expected_exit": exit_code,
                  "session_result": str(root / (label + "-hooks.json"))}
        if exit_code == 0:
            config["expected_first_names"] = EARLY_NAMES
        path = root / (label + "-config.json")
        path.write_text(json.dumps(config, indent=2))
        junit = root / (label + ".xml")
        with pytest_environment(path, lane.packet):
            lane.command("real-pytest-" + label,
                         ["python", "-m", "pytest", "-q", "--junitxml=" + str(junit), str(test)],
                         45, expected=(exit_code,), cwd=root)
        accepted = report(junit, control_names, "DependencyFixture",
                          ("test_early",) if exit_code else ())
        hooks = json.loads(Path(config["session_result"]).read_text())
        require(hooks["complete"] and hooks["controlOnly"] and not hooks["installedArtifactClaim"],
                "Actual pytest hooks did not complete as control-only")
        order_controls = None
        if exit_code == 0:
            actual_names = [node.rsplit("::", 1)[-1] for node in hooks["actual_nodeids"]]
            require(set(actual_names) == set(HANDWRITTEN_NAMES) and len(actual_names) == 9,
                    "Real nine-method order fixture collection differs")
            require_collection_order(actual_names, EARLY_NAMES)
            swapped = list(actual_names)
            swapped[0], swapped[1] = swapped[1], swapped[0]
            require(set(swapped) == set(actual_names) and len(swapped) == len(actual_names),
                    "Order negative changed membership")
            try:
                require_collection_order(swapped, EARLY_NAMES)
            except RuntimeError as error:
                require(str(error) == "Focused regressions are not first in the actual full collection",
                        "Wrong order-control rejection")
            else:
                raise RuntimeError("Swapped real collection order was accepted")
            order_controls = {"positiveActualNames": actual_names, "negativeSameSetSwapped": swapped,
                              "positiveAccepted": True, "negativeRejected": True}
        outcomes.append({"label": label, "expected_exit": exit_code, "report": accepted,
                         "collectionOrderControls": order_controls,
                         "hook_receipt_sha256": sha(config["session_result"])})
    subtests = subtest_dependency_controls(lane, root)
    result = {"state": "EARLY_REAL_DEPENDENCY_CONTROLS", "projectTestsRun": False, "subtests": subtests,
              "XML": xml, "wheelIdentity": wheel_identity, "wheelControls": 2, "actualPytestControls": outcomes, "versions": versions}
    (lane.root / "candidate-python-early-controls.json").write_text(json.dumps(result, indent=2))


def upstream_python(lane, role, artifact, subpath, source_sha):
    names = HANDWRITTEN_NAMES if role == "handwritten" else GENERATED_NAMES
    class_name = HANDWRITTEN_CLASS if role == "handwritten" else GENERATED_CLASS
    root = lane.root / ("upstream-" + role)
    root.mkdir()
    config = {"role": role, "names": names, "class": class_name, "source_root": str(lane.source),
              "candidate_files": lane.manifest["candidate_files"], "build_manifest": str(artifact),
              "test_sha256": source_sha, "observed_manifest": str(root / "actual-artifacts.json"),
              "session_result": str(root / "actual-session.json"),
              "generated_source_copy": str(root / "actual-generated-test.py")}
    if role == "handwritten":
        config["expected_first_names"] = EARLY_NAMES
    path = root / "config.json"
    path.write_text(json.dumps(config, indent=2))
    upstream_report = lane.source / "target/python-test-results-synapseml-core.xml"
    if upstream_report.exists():
        require(upstream_report.is_file(), "Unexpected upstream report path")
        # Preserve the prior report; never accept it as this new invocation.
        prior = root / "prior-upstream-report.xml"
        shutil.copy2(upstream_report, prior)
        previous_sha = sha(upstream_report)
    else:
        previous_sha = None
    with pytest_environment(path, lane.packet):
        lane.sbt("upstream-core-testPython-" + role,
                 ['set core / pythonSubTestPath := Some("' + subpath + '")',
                  "set core / pythonIgnoreTestPath := None", "core/testPython"],
                 600 if role == "handwritten" else 360)
    require(upstream_report.is_file(), "Upstream testPython did not write its report")
    shutil.copy2(upstream_report, root / "actual-upstream.xml")
    if role == "handwritten":
        require(lane.manifest["candidate_files"][TEST_PATH] ==
                "e566433f3c6074ea1389d8f8fc354043757901a0e3072b6102f64669fbec28f8",
                "Handwritten subtest count source changed")
    # Exact frozen handwritten source: 3 models x 7 getters + 2 offset roles.
    # Pytest 9.2 includes successful unittest subtests in the suite total.
    accepted = report(root / "actual-upstream.xml", names, class_name,
                      expected_subtests=23 if role == "handwritten" else 0)
    session = json.loads(Path(config["session_result"]).read_text())
    require(session["complete"] and session["role"] == role and not session.get("controlOnly", False),
            "Installed consumer session incomplete")
    require(session["observed_manifest_sha256"] == sha(config["observed_manifest"]),
            "Consumer manifest binding mismatch")
    result = {"role": role, "actual_upstream_task": "core/testPython",
              "pythonSubTestPath": subpath, "report": accepted, "actual_session": session,
              "prior_report_sha256": previous_sha}
    (root / "result.json").write_text(json.dumps(result, indent=2))
    return result


def generated_subpath(lane, handwritten_result):
    sources = handwritten_result["actual_session"]["actual_test_sources"]
    require(len(sources) == 1, "Missing actual copied test location")
    directory = Path(sources[0]).parent
    require(directory.is_relative_to(lane.source) and directory.name == "causal",
            "Unexpected upstream generated causal directory")
    candidates = list(directory.glob("*.py"))
    require(len(candidates) <= 64 and sum(p.stat().st_size for p in candidates) <= 2 * 1024**2,
            "Generated causal test source cap")
    found = []
    for path in candidates:
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == GENERATED_CLASS:
                methods = [m.name for m in node.body if isinstance(m, ast.FunctionDef)
                           and m.name.startswith("test_")]
                require(len(methods) == 2 and set(methods) == set(GENERATED_NAMES),
                        "Generated DML test objects missing")
                found.append(path)
    require(len(found) == 1, "Missing/ambiguous generated VerifyDML test")
    return "causal/" + found[0].name


def validate(lane):
    require(lane.manifest["execution_enabled"] is True and
            lane.manifest["candidate_python"]["execution_authorized"] is True,
            "Candidate installed route is disabled")
    fixture = json.loads((lane.root / "standard-test-fixtures.json").read_text())
    require(fixture["complete"] is True and fixture["archive"]["bytes"] == 123200074 and
            fixture["archive"]["sha256"] ==
            "f8f8592c30451628ef3c0f63aab3b1f85d18acf6565de0b48086b273e94622fb",
            "Shared standard fixture acquisition is incomplete")
    require(Path(fixture["dataset_dir"]).resolve() == Path(lane.datasets_home).resolve() and
            Path(lane.datasets_home).is_dir(), "SBT datasetDir binding differs from fixture receipt")
    before = source_identity(lane, "candidate-installed-source-before")
    remaining(lane, 2545)
    dependency_controls(lane)
    reference = lane.packet / "baseline-shipping-black.failures.json"
    require(sha(reference) == BLACK_REFERENCE_SHA, "Retained G0 Black evidence changed")
    shutil.copy2(reference, lane.root / reference.name)
    azure_black(lane, "candidate", exclude_docs=True)
    remaining(lane, 2390)
    lane.sbt("candidate-root-compile-testcompile-styles",
             ["compile", "Test / compile", "scalastyle", "Test / scalastyle"], 600)
    remaining(lane, 1790)
    artifact = artifact_manifest(lane)
    remaining(lane, 1200)
    handwritten = upstream_python(
        lane, "handwritten", artifact, "causal/test_double_ml_multiple_treatments.py",
        lane.manifest["candidate_files"][TEST_PATH])
    remaining(lane, 600)
    generated = upstream_python(lane, "generated", artifact, generated_subpath(lane, handwritten), None)
    after = source_identity(lane, "candidate-installed-source-after")
    require(before == after, "Source changed during installed validation")
    result = {"state": "CANDIDATE_INSTALLED_ROUTE_COMPLETE", "candidate_patch_sha256":
              lane.manifest["candidate_patch_sha256"], "source_before": before, "source_after": after,
              "handwritten": handwritten, "generated": generated, "scope": "Build and installed Python only"}
    (lane.root / "candidate-installed-result.json").write_text(json.dumps(result, indent=2))
