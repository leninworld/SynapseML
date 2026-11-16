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

