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
