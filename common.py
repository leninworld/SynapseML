"""Docker-only adapters around upstream commands; execution requires exact approval."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import xml.etree.ElementTree as ET

from validation_runtime import Runtime, cgroup_memory, require


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


class Lab:
    def __init__(self, packet, root, manifest, deadline):
        self.packet, self.root, self.manifest = packet, root, manifest
        self.name = os.environ.get("VALIDATION_LANE", "fixture")
        self.source = root / "source"
        self.deadline = deadline
        self.runtime = Runtime(deadline, root)
        self.records = []
        self.candidate = False
        self.injected = []
        self.generated_tracked = set()
        self.before_tracked = set()
        self.before_untracked = set()
        self.known_failures = []

    def command(self, label, argv, seconds=1200, expected=(0,), cwd=None):
        try:
            self.runtime.run(label, argv, cwd or self.source, seconds, expected=expected)
        finally:
            receipt = self.root / (label + ".json")
            if receipt.is_file():
                self.records.append({"label": label, **json.loads(receipt.read_text())})
            else:
                self.records.append({"label": label, "receipt_missing": True})
        return (self.root / (label + ".log")).read_text(errors="replace")

    @property
    def version(self):
        return "0.0.2-i643g0-SNAPSHOT" if self.candidate else "0.0.1-i643g0-SNAPSHOT"

    def sbt(self, label, tasks, seconds=1200, expected=(0,)):
        return self.command(label, ["sbt", f'set ThisBuild / version := "{self.version}"', *tasks],
                            seconds, expected)

    def initial_source(self):
        require(not self.source.exists(), "Existing source; no replay")
        shutil.copytree("/source", self.source, symlinks=True)
        require(self.command("source-commit", ["git", "rev-parse", "HEAD"], 10).strip() ==
                self.manifest["source_commit"], "Wrong baseline")
        require(not self.command("baseline-clean", ["git", "status", "--porcelain"], 10).strip(),
                "Dirty baseline")
        for name, expected in self.manifest["baseline_files"].items():
            path = self.source / name
            require((not path.exists()) if expected is None else sha(path) == expected,
                    "Baseline file mismatch: " + name)

    def check_forks(self):
        output = self.sbt("fork-settings", ["show core / Test / fork", "show vw / Test / fork"], 180)
        (self.root / "actual-fork-settings.txt").write_text(output)

    def inject(self, names):
        for name in names:
            target = self.source / name
            expected = self.manifest['baseline_files'][name]
            require((not target.exists()) if expected is None else sha(target) == expected,
                    "Baseline test preimage mismatch")
            payload = self.packet / "candidate-files" / name
            require(sha(payload) == self.manifest["candidate_files"][name], "Test hash mismatch")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload, target)
            self.injected.append(name)

    def apply(self):
        require(not self.candidate, "Candidate already applied")
        patch = self.packet / "candidate.patch"
        require(sha(patch) == self.manifest["patch_sha256"], "Patch hash mismatch")
        excludes = []
        for name in self.injected:
            require(sha(self.source / name) == self.manifest["candidate_files"][name],
                    "Baseline regression source changed")
            excludes.append("--exclude=" + name)
        self.before_tracked = set(self.command("before-apply-tracked", ["git", "diff", "--name-only", "HEAD"], 10).splitlines())
        self.before_untracked = set(self.command("before-apply-untracked", ["git", "ls-files", "--others", "--exclude-standard"], 10).splitlines())
        require(self.before_tracked <= self.generated_tracked | set(self.injected), "Baseline tracked source was changed")
        self.command("candidate-apply-check", ["git", "apply", "--check", *excludes, str(patch)], 10)
        self.command("candidate-apply", ["git", "apply", *excludes, str(patch)], 10)
        self.candidate = True
        self.verify_candidate("applied")

    def verify_candidate(self, label):
        for name, expected in self.manifest["candidate_files"].items():
            require(sha(self.source / name) == expected, "Candidate file changed: " + name)
        changed = set(self.command(label + "-tracked-scope", ["git", "diff", "--name-only", "HEAD"], 10)
                      .splitlines())
        new = set(self.command(label + "-new-scope", ["git", "ls-files", "--others", "--exclude-standard"], 10)
                  .splitlines())
        wanted_tracked = {name for name, value in self.manifest["baseline_files"].items() if name in self.manifest["candidate_files"] and value is not None}
        wanted_new = set(self.manifest["candidate_files"]) - wanted_tracked - set(self.injected)
        require(changed - self.generated_tracked == wanted_tracked and
                new - self.before_untracked == wanted_new,
                "Unexpected source delta after candidate application")

    def datasets(self):
        self.sbt("public-datasets", ["getDatasets"], 300)
        paths = list(self.source.glob("**/datasets-2023-04-03.tgz"))
        require(len(paths) == 1, "Dataset archive missing/ambiguous")
        item = self.manifest["dataset"]
        require(paths[0].stat().st_size == item["bytes"] and sha(paths[0]) == item["sha256"],
                "Dataset identity mismatch")
        os.environ["DATASETS_HOME"] = str(paths[0].parent / "datasets-2023-04-03")

    def initial_admission(self, label, remaining_seconds):
        self.runtime.guard()
        capacity = json.loads(Path('/capacity/host.json').read_text())
        require(0 <= time.time() - capacity['at'] < 5, 'Stale actual host capacity')
        require(capacity['available'] >= 13.5 * 1024**3, 'Full priced venue unavailable')
        current = cgroup_memory()
        plan = self.manifest['lanes'][self.name]
        # Prior real VW and full-environment measurements support a planning
        # bound, not current input identity or a new peak; admission is dynamic.
        needed = plan['planning_nonreclaimable_gib'] * 1024**3
        available = min(capacity['available'] + current['anon'], capacity['total'] - 2*1024**3)
        require(needed <= min(available,current['limit']), 'Cold-work capacity deficit')
        require(time.time() + remaining_seconds + 120 < self.deadline,
                'Full finish cannot fit original clock at this stage')
        (self.root/(label+'-initial-admission.json')).write_text(json.dumps({
            'planned_required_bytes':needed,'actual_capacity':capacity,'actual_cgroup':current,
            'remaining_work_seconds':remaining_seconds,'evidence_reserve_seconds':120,
            'deadline':self.deadline,'basis':plan['resource_basis'],
        },indent=2))

    def admission(self, start, extra_seconds, factor=1.25):
        memory = cgroup_memory()
        sampled = self.runtime.memory.snapshot(trace_prefix=True)
        require(sampled["error"] is None and sampled["samples"] >= 2 and
                sampled["max_gap_seconds"] <= 1.0 and
                time.monotonic() - sampled["last_monotonic"] <= 0.5,
                "Missing actual baseline memory evidence")
        capacity = json.loads(Path("/capacity/host.json").read_text())
        require(0 <= time.time() - capacity["at"] < 5, "Stale host capacity")
        require(capacity['available'] >= 13.5 * 1024**3, 'Full priced venue unavailable')
        available = min(capacity["available"] + memory["anon"], capacity["total"] - 2 * 1024**3)
        # Same sequential workload, one small writer delta; no doubled parallel JVMs.
        needed = 2 * sampled["sampled_nonreclaimable_peak"] + 1024**3
        require(needed <= min(available, memory["limit"]), "Measured full-finish memory deficit")
        elapsed = time.monotonic() - start
        projected = factor * elapsed + extra_seconds + 120
        require(time.time() + projected < self.deadline, "Measured full-finish time deficit")
        (self.root / "full-admission.json").write_text(json.dumps({
            "baseline_memory": memory, "sampled_baseline": sampled,
            "required_bytes": needed, "available_bytes": available,
            "calibration_seconds": elapsed, "projected_remaining_seconds": projected,
            "factor": factor, "deadline_epoch": self.deadline,
        }, indent=2))

    def suites(self, label, suites, expected_failures=None, inherited=None, capture_inherited=False):
        expected_failures = expected_failures or {}
        reports = self.source / "core/target/test-reports"
        destination = self.root / (label + "-junit")
        destination.mkdir()
        for path in reports.glob("*.xml"):
            shutil.copy2(path, destination / ("prior-" + path.name))
            path.unlink()  # Owned hosted report path only.
        output = {}
        command_error = None
        try:
            self.sbt(label, ["core/testOnly " + " ".join(suites)], 900,
                     expected=(0, 1) if capture_inherited or inherited is not None else
                     ((1,) if expected_failures else (0,)))
        except BaseException as error:
            command_error = error
        finally:
            # Save actual XML before ANY classifier can reject it.
            for path in reports.glob("*.xml"):
                shutil.copy2(path, destination / path.name)
        try:
            for suite in suites:
                matches = [(path, ET.parse(path).getroot()) for path in destination.glob("*.xml")
                           if not path.name.startswith("prior-") and ET.parse(path).getroot().get("name") == suite]
                require(len(matches) == 1, "Missing/duplicate JUnit suite: " + suite)
                path, report = matches[0]
                cases = list(report.iter("testcase"))
                names = [v.get("name") for v in cases]
                failures = {}
                for case in cases:
                    errors = list(case.findall("failure")) + list(case.findall("error"))
                    if errors:
                        failures[case.get("name")] = "\n".join(
                            v.get("message", "") + "\n" + (v.text or "") for v in errors)
                output[suite] = {"names": names, "failures": failures}
                require(set(names) == set(self.manifest["scala_cases"][suite]) and
                        len(names) == len(set(names)), "Actual test selection mismatch: " + suite)
                require(not list(report.iter("skipped")), "Unexpected skipped tests")
                if capture_inherited or inherited is not None:
                    allowed = {"read images with subsample": 51,
                               "write images with subsample function 2": 148}
                    reader = 'com.microsoft.azure.synapse.ml.io.split1.ImageReaderSuite'
                    require(set(failures) <= (set(allowed) if suite == reader else set()),
                            'Unexpected original-suite failure: ' + suite)
                    signatures = {}
                    assertion = 'scala.Predef.Set.apply[Int](2, 3, 4).apply(imageDF.count().toInt) was false'
                    for name, message in failures.items():
                        require(re.sub(r'\s+', '', assertion) in re.sub(r'\s+', '', message),
                                'Wrong inherited assertion: ' + name)
                        require(re.search(r'ImageReaderSuite.scala:' + str(allowed[name]) + r'\b', message),
                                'Wrong inherited source line: ' + name)
                        signatures[name] = {'assertion': assertion, 'line': allowed[name]}
                    output[suite]['inherited_signatures'] = signatures
                    if inherited is not None:
                        require(signatures == inherited.get(suite, {}).get('inherited_signatures', {}),
                                'Changed inherited failure set/assertion/line: ' + suite)
                else:
                    wanted = expected_failures.get(suite, {})
                    require(set(failures) == set(wanted), "Unexpected fail-before test IDs: " + suite)
                    for name, tokens in wanted.items():
                        normalized = re.sub(r"\s+", "", failures[name]).lower()
                        require(any(re.sub(r"\s+", "", token).lower() in normalized for token in tokens),
                                "Wrong fail-before cause: " + name)
            if command_error is not None:
                raise command_error
            return output
        finally:
            (self.root / (label + "-selection.json")).write_text(json.dumps({
                'suites': output, 'command_error': repr(command_error) if command_error else None,
                'classification_complete': len(output) == len(suites),
            }, indent=2))
