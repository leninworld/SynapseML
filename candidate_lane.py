"""One reviewed sequence: real baseline oracle, exact repair, complete offline acceptance."""
import hashlib
import json
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

from validation_runtime import require

JSON_SELECTOR = '*JsonOutputParserSuite'
JSON_SUITE_SUFFIX = '.JsonOutputParserSuite'
JSON_REGRESSION = 'declared JSON schema matches nullable parsed fields and nested containers'

SUITE = "com.microsoft.azure.synapse.ml.services.search.split2.SchemaParitySuite"
OFFLINE = [
    "com.microsoft.azure.synapse.ml.services.search.AzureSearchAuthSuite",
    "com.microsoft.azure.synapse.ml.services.search.split2.VectorSchemaMigrationSuite",
    "com.microsoft.azure.synapse.ml.services.search.IndexSchemaParsingSuite",
]


def verified_report(path, suite, wanted, failures):
    report = ET.parse(path).getroot()
    require(report.get("name") == suite, "Wrong JUnit suite")
    cases = list(report.iter("testcase"))
    names = [case.get("name") for case in cases]
    require(len(names) == len(set(names)) and set(names) == set(wanted), "Exact JUnit IDs mismatch")
    require(not list(report.iter("skipped")), "Unexpected skipped case")
    require(int(report.get("skipped", "0")) == 0, "Unexpected skipped total")
    require(not list(report.iter("error")), "JUnit execution error")
    actual = {}
    for case in cases:
        failed = list(case.findall("failure"))
        if failed:
            actual[case.get("name")] = " ".join(
                (item.get("message", "") + " " + (item.text or "")) for item in failed
            )
    require(set(actual) == set(failures), "Unexpected fail-before IDs")
    for name in failures:
        require("AssertionError" in actual[name] and "no exception was thrown" in actual[name],
                "Wrong baseline failure cause")
    require(int(report.get("tests")) == len(cases), "JUnit total mismatch")
    require(int(report.get("failures")) == len(actual) and int(report.get("errors")) == 0,
            "JUnit failure/error totals mismatch")
    return {"suite": suite, "names": names, "failures": list(actual), "count": len(cases)}


def reader_controls(path, suite, wanted, failures, directory):
    """Exercise this exact reader on real JUnit bytes and bounded corruptions."""
    original = path.read_bytes()
    verified_report(path, suite, wanted, failures)
    controls = []
    for label in ("wrong-suite", "duplicate-case", "skip", "execution-error", "wrong-total", "wrong-failure"):
        root = ET.fromstring(original)
        if label == "wrong-suite":
            root.set("name", "wrong")
        elif label == "duplicate-case":
            root.append(ET.fromstring(ET.tostring(next(root.iter("testcase")))))
        elif label == "skip":
            ET.SubElement(next(root.iter("testcase")), "skipped")
        elif label == "execution-error":
            ET.SubElement(next(root.iter("testcase")), "error")
        elif label == "wrong-total":
            root.set("tests", "99999")
        elif failures:
            next(root.iter("failure")).set("message", "unrelated failure")
            next(root.iter("failure")).text = "unrelated failure"
        else:
            ET.SubElement(next(root.iter("testcase")), "failure", message="unexpected")
        target = directory / (label + ".xml")
        target.write_bytes(ET.tostring(root))
        try:
            verified_report(target, suite, wanted, failures)
        except RuntimeError:
            controls.append(label)
        else:
            raise RuntimeError("Negative JUnit fixture accepted: " + label)
    return controls


def complete_json_report(path):
    """Validate a fresh unfiltered full inherited suite without inventing its case count."""
    document = ET.parse(path).getroot()
    suite = document.get("name", "")
    require(suite.endswith(JSON_SUITE_SUFFIX), "Wrong JSON parser suite")
    names = [case.get("name") for case in document.iter("testcase")]
    require(names and all(isinstance(name, str) and name for name in names), "Empty JSON parser suite")
    require(JSON_REGRESSION in names, "Missing merged JSON schema regression")
    return verified_report(path, suite, names, ())


def complete_json_reader_controls(path, directory):
    actual = complete_json_report(path)
    controls = reader_controls(path, actual["suite"], actual["names"], (), directory)
    for label in ("empty-suite", "missing-merged-regression"):
        document = ET.parse(path).getroot()
        if label == "empty-suite":
            for case in list(document.iter("testcase")):
                document.remove(case)
            document.set("tests", "0")
        else:
            for case in document.iter("testcase"):
                if case.get("name") == JSON_REGRESSION:
                    case.set("name", "different regression")
        target = directory / (label + ".xml")
        target.write_bytes(ET.tostring(document))
        try:
            complete_json_report(target)
        except RuntimeError:
            controls.append(label)
        else:
            raise RuntimeError("Negative complete JSON JUnit fixture accepted: " + label)
    return controls


def clear_reports(lane, label, module="cognitive"):
    """Preserve then clear only this owned ephemeral checkout's prior reports."""
    saved = lane.root / (label + "-prior-junit")
    saved.mkdir()
    for path in (lane.source / module / "target/test-reports").glob("*.xml"):
        shutil.copy2(path, saved / path.name)
        path.unlink()


def report(lane, label, suite, wanted, failures=()):
    paths = list((lane.source / "cognitive/target/test-reports").glob("*.xml"))
    matches = [path for path in paths if ET.parse(path).getroot().get("name") == suite]
    require(len(matches) == 1, "Missing/duplicate suite report")
    directory = lane.root / (label + "-junit")
    directory.mkdir(exist_ok=True)
    target = directory / matches[0].name
    shutil.copy2(matches[0], target)
    result = verified_report(target, suite, wanted, failures)
    controls = directory / "reader-controls"
    controls.mkdir()
    result["negative_controls"] = reader_controls(target, suite, wanted, failures, controls)
    (lane.root / (label + "-selection.json")).write_text(json.dumps(result, indent=2))
    return result


def validate(lane):
    from format_checks import azure_black
    from generated_artifacts import artifact_manifest

    lane.initial_admission("complete-schema-finish", remaining_seconds=2100)
    azure_black(lane, "baseline", exclude_docs=True)
    test_path = next(name for name in lane.manifest["candidate_files"] if name.endswith("SchemaParitySuite.scala"))
    lane.inject([test_path])
    lane.sbt("early-lint-import-compile", ["cognitive/Test/scalastyle", "cognitive/Test/compile"], 900)
    names = lane.manifest["scala_cases"][SUITE]
    bad = names[1:4]
    # All ten cases run: if the private method cannot resolve, positive controls fail too.
    clear_reports(lane, "baseline")
    lane.sbt("baseline-exact-regressions", ["cognitive/testOnly " + SUITE], 300, expected=(1,))
    report(lane, "baseline", SUITE, names, bad)
    lane.apply()
    azure_black(lane, "candidate", exclude_docs=True)
    clear_reports(lane, "candidate")
    lane.sbt("candidate-exact-regressions", ["cognitive/testOnly " + SUITE], 300)
    report(lane, "candidate", SUITE, names)
    lane.sbt("full-root-compile-testcompile-styles", ["compile", "Test / compile", "scalastyle",
                                                    "Test / scalastyle"], 900)
    clear_reports(lane, "shared-parser", module="core")
    lane.sbt("complete-shared-JSON-output-parser-suite", ["core/testOnly " + JSON_SELECTOR], 600)
    parser_reports = [path for path in (lane.source / "core/target/test-reports").glob("*.xml")
                      if ET.parse(path).getroot().get("name", "").endswith(JSON_SUITE_SUFFIX)]
    require(len(parser_reports) == 1, "Missing/duplicate full JSON parser suite report")
    parser_result = lane.root / "complete-shared-JSON-output-parser.xml"
    shutil.copy2(parser_reports[0], parser_result)
    parser_selection = complete_json_report(parser_result)
    parser_controls = lane.root / "shared-parser-reader-controls"
    parser_controls.mkdir()
    parser_selection["negative_controls"] = complete_json_reader_controls(parser_result, parser_controls)
    (lane.root / "complete-shared-JSON-output-parser-selection.json").write_text(
        json.dumps(parser_selection, indent=2))
    clear_reports(lane, "offline")
    lane.sbt("complete-affected-offline-suites", ["cognitive/testOnly " + " ".join(OFFLINE)], 600)
    for number, suite in enumerate(OFFLINE):
        report(lane, "offline-" + str(number), suite, lane.manifest["scala_cases"][suite])
    clear_reports(lane, "registration", module="core")
    lane.sbt("CI-registration", ["core/testOnly " +
             "com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite"], 180)
    reports = list((lane.source / "core/target/test-reports").glob("*.xml"))
    selected = [path for path in reports if ET.parse(path).getroot().get("name") ==
                "com.microsoft.azure.synapse.ml.core.test.pipeline.PipelineTestCoverageSuite"]
    require(len(selected) == 1, "Missing exact registration report")
    document = ET.parse(selected[0]).getroot()
    require(list(document.iter("testcase")) and not any(list(document.iter(tag))
            for tag in ("failure", "error", "skipped")), "Registration did not pass")
    shutil.copy2(selected[0], lane.root / "CI-registration.xml")
    artifacts = artifact_manifest(lane)
    lane.command("fresh-installed-spark-artifact-guard", ["python", str(lane.packet / "installed_guard.py"),
                 str(artifacts)], 180, cwd=lane.packet)
    lane.verify_candidate("final")
    patch = lane.command("tested-tracked-patch", ["git", "diff", "--binary"], 10).encode()
    patch += lane.command("tested-new-suite", ["git", "diff", "--no-index", "--binary", "--",
                           "/dev/null", test_path], 10, expected=(1,)).encode()
    (lane.root / "tested-repair.patch").write_bytes(patch)
    (lane.root / "frozen-repair.json").write_text(json.dumps({
        "tested_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "source_sha256": lane.manifest["candidate_files"],
        "liveAzure": "NOT_RUN", "publicWriterServiceAcceptance": "NOT_RUN",
        "liveNotebook": "NOT_RUN", "scope": "Related477 nested-schema preflight only",
    }, indent=2))
