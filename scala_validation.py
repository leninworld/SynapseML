"""Unexecuted Scala lanes for the complete hosted-Docker #1850 packet.

The final entry controller must run D0 first, provide immutable /source and
/packet inputs, and supply the original workflow deadline and actual host
available memory. This module does not grant execution authority.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import xml.etree.ElementTree as ET

from validation_runtime import Runtime, admit_from_baseline, cgroup_memory, require


PACKAGE = "com.microsoft.azure.synapse.ml."
AUTO = "core/src/test/scala/com/microsoft/azure/synapse/ml/automl/"
NATIVE = ("lightgbm/src/test/scala/com/microsoft/azure/synapse/ml/"
          "lightgbm/split1/VerifyLightGBMTuningModelInfo.scala")


class ScalaValidation:
    def __init__(self, packet, runtime_root, source, manifest, deadline, host_available):
        self.packet, self.root, self.source = packet, runtime_root, source
        self.manifest, self.deadline = manifest, deadline
        self.host_available = host_available
        self.runtime = Runtime(deadline, runtime_root)
        self.candidate = False
        self.baseline_injections = []
        self.records = []

    def command(self, label, argv, seconds=1200, expected=(0,)):
        result = self.runtime.run(label, argv, self.source, seconds, expected=expected)
        self.records.append({"label": label, **result})
        return (self.root / (label + ".log")).read_text(errors="replace")

    def sbt(self, label, tasks, seconds=1200, expected=(0,)):
        version = "0.0.2-i1850-SNAPSHOT" if self.candidate else "0.0.1-i1850-SNAPSHOT"
        return self.command(label, ["sbt", f'set ThisBuild / version := "{version}"', *tasks],
                            seconds, expected)

    def initial_source(self):
        require(not self.source.exists(), "Source output already exists")
        shutil.copytree("/source", self.source, symlinks=True)
        commit = self.command("source-commit", ["git", "rev-parse", "HEAD"], 10).strip()
        require(commit == self.manifest["source_commit"], "Wrong baseline checkout")
        require(not self.command("baseline-clean", ["git", "status", "--porcelain"], 10).strip(),
                "Baseline checkout not clean")
        for name, sha in self.manifest["baseline_files"].items():
            path = self.source / name
            if sha is None:
                require(not path.exists(), "New candidate path already exists")
            else:
                require(hashlib.sha256(path.read_bytes()).hexdigest() == sha,
                        f"Baseline source hash mismatch: {name}")
        self.sbt("fork-settings", ["show core / Test / fork", "show lightgbm / Test / fork"], 180)
        output = (self.root / "fork-settings.log").read_text()
        require(len(re.findall(r"\[info\]\s+false\s*$", output, re.M)) >= 2,
                "Unconfirmed single-JVM test memory model")

    def inject_inventory(self):
        destination = self.source / "core/src/test/scala/com/microsoft/azure/synapse/ml/train/TestInventory1850.scala"
        require(not destination.exists(), "Inventory fixture collision")
        shutil.copy2(self.packet / "TestInventory1850.scala", destination)

    def inject_baseline(self, names):
        for name in names:
            destination = self.source / name
            require(not destination.exists(), "Baseline diagnostic collision")
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = self.packet / "candidate-files" / name
            require(hashlib.sha256(payload.read_bytes()).hexdigest() ==
                    self.manifest["candidate_files"][name], "Diagnostic input hash mismatch")
            shutil.copy2(payload, destination)
            self.baseline_injections.append(name)

    def apply_candidate(self):
        require(not self.candidate, "Candidate already applied")
        # Only remove the exact newly injected fixture copies in this owned
        # ephemeral hosted source tree, after preserving their byte identities.
        preserved = {}
        for name in self.baseline_injections:
            path = self.source / name
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            require(sha == self.manifest["candidate_files"][name], "Diagnostic mutated")
            preserved[name] = sha
            path.unlink()
        (self.root / "baseline-diagnostic-inputs.json").write_text(json.dumps(preserved, indent=2))
        patch = self.packet / "candidate.patch"
        require(hashlib.sha256(patch.read_bytes()).hexdigest() == self.manifest["patch_sha256"],
                "Wrong candidate patch")
        self.command("candidate-apply-check", ["git", "apply", "--check", str(patch)], 10)
        self.command("candidate-apply", ["git", "apply", str(patch)], 10)
        for name, sha in self.manifest["candidate_files"].items():
            require(hashlib.sha256((self.source / name).read_bytes()).hexdigest() == sha,
                    f"Applied candidate hash mismatch: {name}")
        self.candidate = True

    def datasets(self):
        self.sbt("public-datasets", ["getDatasets"], 240)
        archives = list(self.source.glob("**/datasets-2023-04-03.tgz"))
        require(len(archives) == 1, "Ambiguous dataset archive")
        path = archives[0]
        require(path.stat().st_size == self.manifest["dataset"]["bytes"] and
                hashlib.sha256(path.read_bytes()).hexdigest() == self.manifest["dataset"]["sha256"],
                "Public dataset identity mismatch")
        os.environ["DATASETS_HOME"] = str(path.parent / "datasets-2023-04-03")

    def inventory(self, label, module, suites):
        output = self.sbt(label + "-inventory", [module + "/Test/runMain " + PACKAGE +
                          "train.TestInventory1850 " + " ".join(suites)], 180)
        collected = re.search(r"TEST_INVENTORY_1850=(\{[^\n]+\})", output)
        ignored = re.search(r"IGNORED_INVENTORY_1850=(\{[^\n]+\})", output)
        require(collected is not None and ignored is not None, "Missing actual suite inventory")
        dataset = re.search(r"DATASET_ROOT_1850=([^\n]+)", output)
        require(dataset is not None and Path(dataset[1].strip()) == Path(os.environ["DATASETS_HOME"]),
                "Actual BuildInfo dataset directory mismatch")
        names, skips = json.loads(collected[1]), json.loads(ignored[1])
        require(set(names) == set(suites) == set(skips), "Inventory suite mismatch")
        require(all(values and len(set(values)) == len(values) for values in names.values()),
                "Empty/duplicate test registration")
        return names, skips

    def suites(self, label, module, suites, expected_failure_substrings=None, seconds=1200):
        names, skips = self.inventory(label, module, suites)
        before = self.source / module / "target/test-reports"
        destination = self.root / (label + "-junit")
        require(not destination.exists(), "Existing test evidence; no replay")
        destination.mkdir()
        for path in before.glob("*.xml"):
            shutil.copy2(path, destination / ("prior-" + path.name))
            path.unlink()
        expected_failure_substrings = expected_failure_substrings or {}
        self.sbt(label, [module + "/testOnly " + " ".join(suites)], seconds,
                 expected=(1,) if expected_failure_substrings else (0,))
        summary = {}
        for suite, registered in names.items():
            reports = []
            for path in before.glob("*.xml"):
                report = ET.parse(path).getroot()
                if report.get("name") == suite:
                    reports.append((path, report))
            require(len(reports) == 1, "Missing/duplicate fresh JUnit suite: " + suite)
            path, report = reports[0]
            cases = list(report.iter("testcase"))
            actual = [case.get("name") for case in cases]
            require(len(actual) == len(registered) and set(actual) == set(registered),
                    "Actual test selection mismatch: " + suite)
            actual_skips = [case.get("name") for case in cases if case.find("skipped") is not None]
            require(sorted(actual_skips) == sorted(skips[suite]), "Unexpected test skips")
            failures = {}
            for case in cases:
                problems = list(case.findall("error")) + list(case.findall("failure"))
                if problems:
                    failures[case.get("name")] = "\n".join(
                        item.get("message", "") + "\n" + (item.text or "") for item in problems)
            expected = expected_failure_substrings.get(suite, {})
            require(set(failures) == set(expected), "Unexpected failure IDs: " + suite)
            for name, needle in expected.items():
                require(needle in failures[name], "Wrong fail-before cause: " + name)
            shutil.copy2(path, destination / path.name)
            summary[suite] = {"names": actual, "skips": actual_skips,
                              "failure_names": sorted(failures)}
        (self.root / (label + "-selection.json")).write_text(json.dumps(summary, indent=2))
        return summary

    def memory_admission(self, calibration_started, extra_seconds, remaining_factor=2):
        measured = cgroup_memory()
        sampling = self.runtime.memory.snapshot(trace_prefix=True)
        require(sampling["error"] is None and sampling["samples"] >= 2 and
                sampling["max_gap_seconds"] <= 1.0 and
                time.monotonic() - sampling["last_monotonic"] <= 0.5,
                "Insufficient/faulty non-reclaimable memory sampling")
        measured["sampled_nonreclaimable_peak"] = sampling["sampled_nonreclaimable_peak"]
        measured["sampling"] = sampling
        capacity = json.loads(Path("/capacity/host.json").read_text())
        require(0 <= time.time() - capacity["at"] < 5, "Missing current host memory evidence")
        # MemAvailable already includes reclaimable file cache. Add back only
        # this owned container's anonymous memory, not all memory.current.
        available = min(capacity["available"] + measured["anon"], capacity["total"] - 2 * 1024**3)
        admission = admit_from_baseline(measured, available)
        elapsed = time.monotonic() - calibration_started
        projected = remaining_factor * elapsed + extra_seconds + 120
        require(time.time() + projected < self.deadline,
                "Full finish projection exceeds original workflow deadline")
        admission.update(calibration_seconds=elapsed, projected_remaining_seconds=projected,
                         actual_host_capacity=capacity, remaining_time_factor=remaining_factor)
        (self.root / "full-admission.json").write_text(json.dumps(admission, indent=2))

    def root_fuzzing(self, label):
        suite = PACKAGE + "core.test.fuzzing.FuzzingTest"
        directory = self.source / "target/test-reports"
        retained = self.root / (label + "-junit")
        retained.mkdir()
        for report in directory.glob("*.xml"):
            shutil.copy2(report, retained / ("prior-" + report.name))
            report.unlink()
        self.sbt(label, ["testOnly " + suite], 600)
        matches = []
        for report in directory.glob("*.xml"):
            parsed = ET.parse(report).getroot()
            if parsed.get("name") == suite:
                matches.append((report, parsed))
        require(len(matches) == 1, "Root FuzzingTest missing/duplicate fresh report")
        report, parsed = matches[0]
        cases = list(parsed.iter("testcase"))
        names = [case.get("name") for case in cases]
        expected = self.manifest["root_fuzz_test_names"] + self.manifest["root_fuzz_ignored_names"]
        require(len(names) == len(expected) and set(names) == set(expected),
                "Root FuzzingTest registration changed")
        require(not list(parsed.iter("failure")) and not list(parsed.iter("error")),
                "Root FuzzingTest failed")
        ignored = [case.get("name") for case in cases if case.find("skipped") is not None]
        require(sorted(ignored) == sorted(self.manifest["root_fuzz_ignored_names"]),
                "Root FuzzingTest ignores changed")
        shutil.copy2(report, retained / report.name)
        result = {"suite": suite, "names": sorted(names), "ignored": sorted(ignored)}
        (self.root / (label + "-selection.json")).write_text(json.dumps(result, indent=2))
        return result

    def run_scala_lane(self, lane):
        self.initial_source()
        if lane in ("core", "native"):
            self.inject_inventory()
        started = time.monotonic()
        if lane == "root":
            self.sbt("baseline-root-compile", ["compile", "Test / compile"], 900)
            original = self.root_fuzzing("baseline-root-fuzzing")
            self.memory_admission(started, 600, remaining_factor=1.25)
            self.apply_candidate()
            self.sbt("candidate-root-checks", ["scalastyle", "Test / scalastyle",
                                               "compile", "Test / compile"], 1200)
            require(self.root_fuzzing("candidate-root-fuzzing") == original,
                    "Root FuzzingTest baseline/candidate set changed")
            return
        self.datasets()
        if lane == "core":
            self.inject_baseline([AUTO + "ModelInfoFixtures.scala", AUTO + "VerifyModelInfoRegression.scala"])
            suite = PACKAGE + "automl.VerifyModelInfoRegression"
            expected = {suite: {
                "wrapped classifier reports the winning training-only knob": "knob: 1",
                "wrapped regressor reports the winning training-only knob": "knob: 1",
                "a direct classifier exposes its applied training-only setting":
                    "Model type not supported for evaluation",
            }}
            self.suites("baseline-fail-before-core", "core", [suite], expected, 600)
            self.memory_admission(started, 900)
            baseline = [PACKAGE + "automl." + name for name in self.manifest["baseline_automl_suites"]]
            original = self.suites("baseline-full-automl", "core", baseline, seconds=1200)
            self.apply_candidate()
            suites = baseline + [suite, PACKAGE + "automl.VerifyTuneHyperparametersModelInfo",
                                  PACKAGE + "core.test.pipeline.PipelineTestCoverageSuite"]
            candidate = self.suites("candidate-full-automl", "core", suites, seconds=1200)
        else:
            require(lane == "native", "Unknown Scala lane")
            self.inject_baseline([NATIVE])
            suite = PACKAGE + "lightgbm.split1.VerifyLightGBMTuningModelInfo"
            expected = {suite: {
                "winning LightGBM training parameters survive copy and persistence in " + mode + " mode":
                    "numLeaves: 7" for mode in ("bulk", "streaming")
            }}
            self.suites("baseline-fail-before-native", "lightgbm", [suite], expected, 600)
            self.memory_admission(started, 900)
            baseline = [PACKAGE + "lightgbm.split1.VerifyLightGBMRankingTuning"]
            original = self.suites("baseline-native-ranking", "lightgbm", baseline, seconds=1200)
            self.apply_candidate()
            candidate = self.suites("candidate-native-tuners", "lightgbm", baseline + [suite], seconds=1200)
        for suite, values in original.items():
            require(set(values["names"]) <= set(candidate[suite]["names"]),
                    "Candidate lost original registered tests")
            require(values["skips"] == candidate[suite]["skips"], "Candidate changed baseline skips")
