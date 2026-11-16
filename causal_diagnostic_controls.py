"""Future D0 real-file/reader controls. No product fits; authoring only."""
import base64
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import time

from causal_diagnostics import snapshot_reports, run_with_causal_snapshot, partial_log_diagnostic
from evidence_export import export_evidence, line_bytes
from native_candidate_lane import causal_discovery, validate_causal_groups, reconcile_causal_groups
from validation_runtime import Runtime


def controls(root, config):
    done = []
    with tempfile.TemporaryDirectory(prefix="causal-diagnostic-controls-") as temporary:
        work = Path(temporary)
        for mode in ["success", "cap", "timeout"]:
            place = work / mode
            reports = place / "source-reports"
            reports.mkdir(parents=True)
            raw = b'<testsuite name="fixture" tests="1"><testcase name="finished"/></testsuite>'

            child_code = """
import json,os,subprocess,sys,time
from pathlib import Path
xml,mode,pidfile=sys.argv[1:]
Path(xml).write_bytes(b'<testsuite name="fixture" tests="1"><testcase name="finished"/></testsuite>')
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def started(pid):
    return Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]
Path(pidfile).write_text(json.dumps([{'pid':pid,'startTimeTicks':started(pid)}for pid in [os.getpid(),child.pid]]))
if mode=='cap':
    os.write(1,b'x'*8192)
elif mode=='timeout':
    time.sleep(60)
else:
    print('owned-success',flush=True)
"""
            deadline = float(os.environ["VALIDATION_DEADLINE_EPOCH"])
            runtime = Runtime(deadline_epoch=deadline, evidence_dir=place)

            class Lane:
                root = place

                def sbt(self, label, tasks, seconds, expected):
                    assert tasks == ["core/testOnly fixture.CompleteSuite"]
                    # Real observer/guard/process-group path; synthetic producer,
                    # never real sbt or a scientific test result.
                    record = runtime.run(label,
                        [sys.executable, "-c", child_code, str(reports / "actual.xml"),
                         mode, str(place / "owned-processes.json")], place,
                        1.0 if mode == "timeout" else 3, expected=expected, output_cap=1024)
                    return (place / (label + ".log")).read_text()

            try:
                run_with_causal_snapshot(Lane(), reports, place / "retained", 10,
                                         "core/testOnly fixture.CompleteSuite")
            except RuntimeError as error:
                assert mode != "success"
                assert str(error) == ("Step output cap" if mode == "cap" else "Step deadline")
            else:
                assert mode == "success"
            record_path = place / "candidate-entire-causal-package.json"
            record = json.loads(record_path.read_text())
            # Preserve the actual observer artifacts before any fixture assertion.
            for source, suffix in [(record_path, "process.json"),
                    (place / "candidate-entire-causal-package.log", "process.log")]:
                (root / ("partial-report-" + mode + "-" + suffix)).write_bytes(source.read_bytes())
            assert record["leader_terminal"] and record["bytes"] <= 1024
            assert record["error"] == ({"success": None, "cap": "RuntimeError: Step output cap",
                                        "timeout": "RuntimeError: Step deadline"}[mode])
            if mode == "success":
                assert record["exit"] == 0
            observed = json.loads((place / "owned-processes.json").read_text())
            stop_end = min(deadline, time.time() + 1)
            states = []
            for process in observed:
                proc = Path("/proc", str(process["pid"]), "stat")
                terminal = False
                state = "UNOBSERVED"
                while time.time() < stop_end:
                    try:
                        fields = proc.read_text().rsplit(")", 1)[1].split()
                    except FileNotFoundError:
                        state, terminal = "ABSENT", True
                    else:
                        state = fields[0]
                        terminal = fields[19] != process["startTimeTicks"] or state in ("Z", "X")
                    if terminal:
                        break
                    time.sleep(min(0.01, max(0, stop_end - time.time())))
                states.append({**process, "observedState": state,
                               "terminalOrDifferentProcess": terminal})
            (root / ("partial-report-" + mode + "-stop-proof.json")).write_text(json.dumps({
                "trackedProcesses": states, "originalDeadlineEpoch": deadline,
                "noTrackedLiveChildren": len(states) == 2 and
                    all(row["terminalOrDifferentProcess"] for row in states)}, indent=2))
            assert len(states) == 2 and all(row["terminalOrDifferentProcess"] for row in states)
            ledger = json.loads((place / "candidate-causal-partial-reports.json").read_text())
            assert (place / "retained/actual.xml").read_bytes() == raw
            assert ledger["snapshotComplete"] and not ledger["fullValidationPassed"]
            assert ledger["files"][0]["caseIDs"] == ["finished"]
            assert ledger["remainingCaseIDs"] == "UNKNOWN"
            assert ledger["operationReturned"] == (mode == "success")
            # Retain actual observer receipt, bounded raw output and XML even
            # after the owned temporary fixture directory is closed.
            for source, suffix in [(record_path, "process.json"),
                    (place / "candidate-entire-causal-package.log", "process.log"),
                    (place / "retained/actual.xml", "retained.xml")]:
                (root / ("partial-report-" + mode + "-" + suffix)).write_bytes(source.read_bytes())
            (root / ("partial-report-" + mode + "-proof.json")).write_text(json.dumps({
                "runtimeObserverPath": True, "originalDeadlineEpoch": deadline,
                "controlOnlyOutputCap": 1024, "productionOutputCapUnchanged": 67108864,
                "processReceipt": record, "trackedProcesses": states,
                "noTrackedLiveChildren": True, "reportSnapshot": ledger,
                "syntheticJUnitProducerNotRealSbt": True}, indent=2))
            done.append("partial-report-" + mode)

        malformed = work / "malformed"
        malformed.mkdir()
        (malformed / "broken.xml").write_bytes(b"<testsuite>")
        result = snapshot_reports(malformed, work / "malformed-retained", work / "malformed.json",
                                  False, "original cap failure")
        assert result["snapshotComplete"] and result["files"][0]["parseError"]
        assert not result["fullValidationPassed"]
        done.append("partial-malformed-preserved-not-complete-test-proof")
        unsafe = work / "unsafe"
        unsafe.mkdir()
        (unsafe / "linked.xml").symlink_to(malformed / "broken.xml")
        result = snapshot_reports(unsafe, work / "unsafe-retained", work / "unsafe.json", False, "cap")
        assert not result["snapshotComplete"] and result["files"][0]["copyError"]
        done.append("partial-nonregular-rejected")

        raw = b"[info] hello\n26/11/16 03:00:00 WARN Test: warning\n" + b"x" * 700 + b"\nlast"
        path = work / "classification.log"
        path.write_bytes(raw)
        diagnostic = partial_log_diagnostic(path, path.name, 16)
        assert diagnostic["rawBytes"] == len(raw)
        assert diagnostic["rawSHA256"] == hashlib.sha256(raw).hexdigest()
        assert diagnostic["lineCount"] == 4 and sum(diagnostic["lineClassHistogram"].values()) == 4
        assert diagnostic["head"]["sha256"] == hashlib.sha256(raw[:16]).hexdigest()
        assert diagnostic["tail"]["sha256"] == hashlib.sha256(raw[-16:]).hexdigest()
        assert diagnostic["linesLongerThanClassificationPrefix"] == 1
        done.append("bounded-stream-histogram-and-excerpts")

        # Both actual selection paths: raw-envelope overflow and encoded overflow.
        path = work / "candidate-entire-causal-package.log"
        path.write_bytes(os.urandom(1024**2))
        for mode, raw_cap, encoded_cap in [("raw", 512*1024, 2*1024**2),
                                            ("encoded", 2*1024**2, 1024**2)]:
            lines = []
            result = export_evidence(work, [path], time.time() + 60,
                                     emit=lambda value, flush: lines.append(value),
                                     cap=raw_cap, encoded_cap=encoded_cap)
            assert result["complete"] and not result["critical_complete"]
            assert any(row["path"] == path.name and row["critical"] for row in result["omissions"])
            assert line_bytes(lines) <= encoded_cap
            records = [json.loads(line.split("=", 1)[1]) for line in lines]
            assert not any(row["path"] == path.name for row in records)
            pieces = [row for row in records if row["path"] == path.name + ".partial-diagnostic.json"]
            assert pieces
            recovered = b"".join(gzip.decompress(base64.b64decode(row["base64"])) for row in pieces)
            diagnostic = json.loads(recovered)
            assert diagnostic["fullCriticalLogOmitted"] and not diagnostic["complete"]
            assert diagnostic["rawSHA256"] == hashlib.sha256(path.read_bytes()).hexdigest()
            done.append("critical-" + mode + "-overflow-keeps-failing-diagnostic")

        validate_causal_groups(config)
        done.append("five-disjoint-source-groups-positive")
        for mode in ["duplicate", "missing", "budget"]:
            changed = copy.deepcopy(config)
            first = next(iter(changed["causal_groups"]))
            if mode == "duplicate":
                changed["causal_groups"][first].append(changed["causal_groups"][first][0])
            elif mode == "missing":
                changed["causal_groups"][first].pop()
            else:
                changed["lanes"][first]["suite_seconds"] = 1501
            try:
                validate_causal_groups(changed)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Invalid causal group accepted: " + mode)
            done.append("five-source-groups-" + mode + "-rejected")
        expected = config["causal_suite_inventory"]
        synthetic = "\n".join("[info] * Test " + name + " : subclass(false, org.scalatest.Suite)"
                              for name in expected)
        assert causal_discovery(synthetic, expected)["count"] == 15
        done.append("discovery-synthetic-positive-not-actual-sbt-proof")
        mutations = {"missing": synthetic.replace(expected[0], "other.Fixture"),
                     "duplicate": synthetic + "\n[info] * Test " + expected[0] + " : fingerprint",
                     "new": synthetic + "\n[info] * Test com.microsoft.azure.synapse.ml.causal.Extra : fingerprint",
                     "malformed": synthetic.replace("Test " + expected[0], "Unknown " + expected[0])}
        for mode, changed in mutations.items():
            try:
                causal_discovery(changed, expected)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Invalid discovery accepted: " + mode)
            done.append("discovery-synthetic-" + mode + "-rejected")
    bundles = {}
    for name, suites in config["causal_groups"].items():
        rows = {suite: [{"name": value} for value in config["suite_cases"].get(suite, ["owned fixture"])]
                for suite in suites}
        bundles[name] = {
            "laneResult": {"lane": name, "passed": True, "full_validation_passed": True},
            "postSource": {"passed": True, "candidate": config["candidate_files"],
                           "source_pin": config["source_commit"]},
            "discovery": {"complete": True, "count": 15,
                          "suiteIDs": sorted(config["causal_suite_inventory"])},
            "result": {"group": name, "suites": rows, "candidateFiles": config["candidate_files"],
                       "sourcePin": config["source_commit"], "exit": 0, "failures": {}, "skips": [],
                       "abortedSuites": 0,
                       "caseIDs": [[suite, row["name"]] for suite, values in rows.items() for row in values]}}
    assert reconcile_causal_groups(bundles, config)["suiteCount"] == 15
    done.append("full-five-group-union-positive")
    for mode in ["missing-group", "source-mismatch", "missing-dml-case"]:
        changed = copy.deepcopy(bundles)
        first = next(iter(changed))
        if mode == "missing-group":
            changed.pop(first)
        elif mode == "source-mismatch":
            changed[first]["postSource"]["source_pin"] = "0" * 40
        else:
            dml = config["lanes"]["candidate-legacy"]["suite"]
            selected = next(value for value in changed.values() if dml in value["result"]["suites"])
            removed = selected["result"]["suites"][dml].pop()["name"]
            selected["result"]["caseIDs"].remove([dml, removed])
        try:
            reconcile_causal_groups(changed, config)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Invalid grouped evidence accepted: " + mode)
        done.append("full-five-group-union-" + mode + "-rejected")
    result = {"state": "CAUSAL_DIAGNOSTIC_AND_GROUP_CONTROLS_COMPLETE", "controls": done,
              "count": len(done), "actualNetworkCalls": 0, "projectFitsRun": False,
              "qualification": "Real Runtime child success/output-cap/timeout and file/export/readers; synthetic JUnit producer, not real sbt or real-definedTests proof."}
    (root / "causal-diagnostic-controls.json").write_text(json.dumps(result, indent=2))
    return result
