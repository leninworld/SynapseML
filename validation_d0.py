"""Bounded real helper controls before dependencies/project work; no project tests."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import xml.etree.ElementTree as ET

from validation_runtime import Runtime, docker_only, extract_archive, require


def rejected(function, token):
    try:
        function()
    except (RuntimeError, ValueError, AssertionError) as error:
        require(token in str(error), "Wrong failure-control cause: " + str(error))
        return
    raise RuntimeError("Failure control unexpectedly accepted")


def main():
    docker_only()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--original-deadline-epoch", type=float, required=True)
    args = parser.parse_args()
    require(not args.root.exists(), "D0 no replay")
    args.root.mkdir()
    rt = Runtime(args.original_deadline_epoch, args.root)
    probe = args.root / "write-read-probe"
    probe.write_bytes(b"fixture")
    require(probe.read_bytes() == b"fixture", "Writable path")
    archive = args.root / "tiny.tar"
    with tarfile.open(archive, "w") as stream:
        item = tarfile.TarInfo("parent/a.txt")
        item.size = 2
        stream.addfile(item, io.BytesIO(b"ok"))
    records = extract_archive(archive, args.root / "extracted", 100)
    require(records[0]["sha256"] == hashlib.sha256(b"ok").hexdigest(), "Archive identity")
    bad = args.root / "bad.tar"
    with tarfile.open(bad, "w") as stream:
        item = tarfile.TarInfo("../escape")
        item.size = 1
        stream.addfile(item, io.BytesIO(b"x"))
    rejected(lambda: extract_archive(bad, args.root / "bad-extract", 100), "Unsafe archive component")
    rt.run("positive-process", [sys.executable, "-c", "print('fixture')"], args.root, 10)
    rejected(lambda: rt.run("wrong-exit", [sys.executable, "-c", "raise SystemExit(3)"], args.root, 10),
             "Unexpected exit 3")
    rejected(lambda: rt.run("owned-timeout", [sys.executable, "-c", "import time;time.sleep(30)"],
                           args.root, 0.3), "Step deadline")
    from generated_artifacts import read_compile_scala_versions, SCALA_VERSION_SCOPES
    version_fixture = (Path(__file__).parent / "compile-scala-version-fixture.log").read_text()
    actual_versions = read_compile_scala_versions(version_fixture)
    require(set(actual_versions) == set(SCALA_VERSION_SCOPES), "Real Scala log scopes")
    require(read_compile_scala_versions(version_fixture.replace("[info] \t", "[info]    ")) ==
            actual_versions, "Whitespace Scala log scopes")
    version_failures = [
        ("wrong", version_fixture.replace("2.12.17", "2.13.17"), "Unexpected actual build Scala version"),
        ("mixed", version_fixture.replace("2.12.17", "2.12.18", 1), "Unexpected actual build Scala version"),
        ("missing-value", version_fixture.replace("[info] \t2.12.17\n", "", 1), "Missing Scala version value"),
        ("missing-scope", version_fixture.replace("[info] cognitive / scalaVersion\n[info] \t2.12.17\n", ""), "Missing Scala version scope"),
        ("duplicate-scope", version_fixture + "[info] core / scalaVersion\n[info] \t2.12.17\n", "Duplicate Scala version scope"),
        ("duplicate-value", version_fixture + "[info] \t2.12.17\n", "Unlabelled or duplicate Scala version value"),
        ("empty", "", "Missing Scala version scope"),
    ]
    for label, output, cause in version_failures:
        rejected(lambda output=output: read_compile_scala_versions(output), cause)
    (args.root / "compile-scala-reader-controls.json").write_text(json.dumps({
        "actual_fixture_sha256": hashlib.sha256(version_fixture.encode()).hexdigest(),
        "actual_scopes": actual_versions, "positive_controls": 2,
        "rejected": [label for label, _, _ in version_failures],
        "runtime_scala_not_asserted_equal_to_compile_scala": True,
    }, indent=2))
    from candidate_lane import reader_controls
    junit = ET.Element("testsuite", name="fixture", tests="1", failures="0", errors="0")
    ET.SubElement(junit, "testcase", name="one", classname="fixture")
    xml = args.root / "fixture.xml"
    xml.write_bytes(ET.tostring(junit))
    controls = args.root / "reader-controls"
    controls.mkdir()
    reader_controls(xml, "fixture", ["one"], (), controls)
    junit.set("failures", "1")
    ET.SubElement(next(junit.iter("testcase")), "failure",
                  message="Expected exception java.lang.AssertionError, but no exception was thrown")
    expected_failure = args.root / "expected-failure.xml"
    expected_failure.write_bytes(ET.tostring(junit))
    failure_controls = args.root / "failure-reader-controls"
    failure_controls.mkdir()
    reader_controls(expected_failure, "fixture", ["one"], ["one"], failure_controls)
    from candidate_lane import (complete_json_report, complete_json_reader_controls,
                                JSON_SUITE_SUFFIX, JSON_REGRESSION)
    parser_shape = ET.parse(Path(__file__).parent / "scala-junit-shape-fixture.xml").getroot()
    parser_shape.set("name", "fixture" + JSON_SUITE_SUFFIX)
    next(parser_shape.iter("testcase")).set("name", JSON_REGRESSION)
    parser_fixture = args.root / "real-shape-json-parser-fixture.xml"
    parser_fixture.write_bytes(ET.tostring(parser_shape))
    parser_positive = complete_json_report(parser_fixture)
    parser_control_dir = args.root / "complete-json-reader-controls"
    parser_control_dir.mkdir()
    parser_negative = complete_json_reader_controls(parser_fixture, parser_control_dir)
    (args.root / "complete-json-reader-controls.json").write_text(json.dumps({
        "actual_shape_fixture_sha256": hashlib.sha256(
            (Path(__file__).parent / "scala-junit-shape-fixture.xml").read_bytes()).hexdigest(),
        "synthetic_names_using_real_ScalaTest_XML_shape": True,
        "no_parser_project_test_executed_by_fixture": True,
        "positive": parser_positive, "negative_controls": parser_negative,
    }, indent=2))
    from format_checks import azure_black

    class BlackFixture:
        source = args.root / "format-source"
        root = args.root
        manifest = {"candidate_files": {}}

        def __init__(self, output):
            self.output = output
            self.records = [{"exit": 1}]

        def command(self, *unused, **options):
            return self.output

    BlackFixture.source.mkdir()
    require(azure_black(BlackFixture("would reformat example.py\n"), "baseline")["failures"] ==
            ["example.py"], "Source-relative Black observer")
    rejected(lambda: azure_black(BlackFixture("would reformat ../outside.py\n"), "baseline"),
             "Black path outside source")
    from evidence_export import export_evidence
    from collect import reassemble
    emitted = []
    receipt = export_evidence(args.root, [probe], args.original_deadline_epoch,
                              emit=lambda text, **options: emitted.append(text))
    require(receipt["complete"], "Tiny export failed")
    decoded = reassemble([json.loads(text.split("EVIDENCE1669=", 1)[1]) for text in emitted])
    require(decoded["write-read-probe"] == b"fixture", "Actual exporter/collector bytes")
    rt.guard()
    (args.root / "D0-result.json").write_text(json.dumps({"status": "TINY_FIXTURES_PASS",
        "project_tests_run": False, "real_process_timeout_archive_export_and_JUnit_controls": True}))


if __name__ == "__main__":
    main()
