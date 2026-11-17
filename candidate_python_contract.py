"""Data-only exact pytest acceptance. Controls are run only in the future approved Docker plan."""
import copy
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from validation_runtime import require

HANDWRITTEN_CLASS = "DoubleMLMultipleTreatmentsTests"
HANDWRITTEN_NAMES = (
    "test_real_plural_fit_and_all_getter_shapes",
    "test_python_lists_and_nested_rows_are_defensive_copies",
    "test_copy_and_reload_preserve_actual_fitted_causal_statistics",
    "test_singular_fit_and_getters_remain_available",
    "test_python_setter_does_not_change_jvm_order_until_transfer",
    "test_width_corruption_and_fit_exclusivity_are_not_silently_accepted",
    "test_param_map_and_loaded_model_same_width_rename",
    "test_ambiguous_jvm_route_rejects_statistics_after_copy_and_reload",
    "test_auxiliary_glr_offset_both_roles_preserve_caller_parameters",
)
EARLY_NAMES = ("test_ambiguous_jvm_route_rejects_statistics_after_copy_and_reload",
               "test_auxiliary_glr_offset_both_roles_preserve_caller_parameters")
GENERATED_CLASS = "VerifyDoubleMLEstimator"
GENERATED_NAMES = (
    "test_DoubleMLEstimator_constructor_0",
    "test_DoubleMLEstimator_constructor_1",
)


def require_collection_order(names, expected_first):
    require(list(names[:len(expected_first)]) == list(expected_first),
            "Focused regressions are not first in the actual full collection")


def report(path, names, class_name, expected_failures=(), expected_subtests=0):
    """Require a complete pytest JUnit document, exact cases, and no ignored failures."""
    require(type(expected_subtests) is int and 0 <= expected_subtests <= 100, "Invalid expected subtest count")
    require(expected_subtests == 0 or len(names) > 0, "Subtests require named methods")
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= 2 * 1024**2, "Missing/oversize pytest report")
    root = ET.fromstring(path.read_bytes())
    require(root.tag in ("testsuite", "testsuites"), "Wrong pytest report root")
    suites = [root] if root.tag == "testsuite" else list(root)
    require(suites and all(s.tag == "testsuite" for s in suites), "Malformed pytest suites")
    cases = [case for suite in suites for case in suite.findall("testcase")]
    require(len(list(root.iter("testcase"))) == len(cases), "Nested hidden pytest cases")
    actual_names = [case.get("name") for case in cases]
    require(len(actual_names) == len(set(actual_names)) and set(actual_names) == set(names),
            "Exact pytest case names differ")
    require(all(case.get("classname", "").split(".")[-1] == class_name for case in cases),
            "Wrong pytest class")
    require(not list(root.iter("skipped")) and not list(root.iter("error")), "Skipped/error pytest case")
    failed = {case.get("name") for case in cases if list(case.findall("failure"))}
    require(failed == set(expected_failures), "Unexpected pytest failures")
    require(expected_subtests == 0 or len(suites) == 1, "Subtest counts require one complete suite")
    for suite in suites:
        own = list(suite.findall("testcase"))
        require(int(suite.get("tests", "-1")) == len(own) + expected_subtests, "Wrong pytest total")
        require(int(suite.get("failures", "-1")) == sum(bool(c.findall("failure")) for c in own),
                "Wrong pytest failure total")
        require(int(suite.get("errors", "-1")) == 0 and int(suite.get("skipped", "-1")) == 0,
                "Wrong pytest error/skip totals")
    if expected_failures:
        require(all("AssertionError" in (f.get("message", "") + (f.text or ""))
                    for case in cases if case.get("name") in failed for f in case.findall("failure")),
                "Intentional failure has wrong cause")
    return {"class": class_name, "names": actual_names, "count": len(cases),
            "failures": sorted(failed), "expected_subtests": expected_subtests,
            "reported_suite_total": sum(int(x.get("tests", "-1")) for x in suites),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture_controls(directory):
    """Real XML bytes through the same consumer; not a project pass."""
    directory = Path(directory)
    directory.mkdir()
    source = ET.Element("testsuites")
    suite = ET.SubElement(source, "testsuite", tests=str(len(HANDWRITTEN_NAMES)), failures="0", errors="0", skipped="0")
    for name in HANDWRITTEN_NAMES:
        ET.SubElement(suite, "testcase", name=name, classname="fixture." + HANDWRITTEN_CLASS)
    positive = directory / "positive.xml"
    positive.write_bytes(ET.tostring(source))
    report(positive, HANDWRITTEN_NAMES, HANDWRITTEN_CLASS)
    controls = ["positive-exact-authored"]
    labels = ("missing", "duplicate", "skip", "failure", "error", "wrong-class",
              "wrong-total", "extra-name", "empty")
    for label in labels:
        root = copy.deepcopy(source)
        test_suite = root.find("testsuite")
        first = test_suite.find("testcase")
        if label == "missing":
            test_suite.remove(first)
            test_suite.set("tests", str(len(HANDWRITTEN_NAMES)-1))
        elif label == "duplicate":
            test_suite.append(copy.deepcopy(first))
            test_suite.set("tests", str(len(HANDWRITTEN_NAMES)+1))
        elif label in ("skip", "failure", "error"):
            ET.SubElement(first, {"skip": "skipped"}.get(label, label), message="AssertionError control")
            test_suite.set({"skip": "skipped", "failure": "failures", "error": "errors"}[label], "1")
        elif label == "wrong-class":
            first.set("classname", "fixture.Other")
        elif label == "wrong-total":
            test_suite.set("tests", str(len(HANDWRITTEN_NAMES)+1))
        elif label == "extra-name":
            first.set("name", "test_unrelated")
        else:
            for case in list(test_suite):
                test_suite.remove(case)
            test_suite.set("tests", "0")
        target = directory / (label + ".xml")
        target.write_bytes(ET.tostring(root))
        try:
            report(target, HANDWRITTEN_NAMES, HANDWRITTEN_CLASS)
        except (RuntimeError, ValueError):
            controls.append(label)
        else:
            raise RuntimeError("Accepted negative pytest XML control: " + label)
    return {"state": "DATA_ONLY_PYTEST_READER_CONTROLS", "caseCount": 10, "cases": controls,
            "projectTestsRun": False}
