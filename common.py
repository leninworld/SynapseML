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
from g0_contract import clock_admission


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

    def command(self, label, argv, seconds=1200, expected=(0,), cwd=None):
        record = self.runtime.run(label, argv, cwd or self.source, seconds, expected=expected)
        self.records.append({"label": label, **record})
        return (self.root / (label + ".log")).read_text(errors="replace")

    @property
    def version(self):
        return self.manifest["artifact_version"]

    def sbt(self, label, tasks, seconds=1200, expected=(0,)):
        settings = [f'set ThisBuild / version := "{self.version}"']
        if getattr(self, "datasets_home", None) is not None:
            settings.append('set ThisBuild / datasetDir := file("' + str(self.datasets_home) + '")')
        return self.command(label, ["sbt", *settings, *tasks], seconds, expected)

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

    def initial_admission(self, label, remaining_seconds):
        self.runtime.guard()
        capacity = json.loads(Path('/capacity/host.json').read_text())
        require(0 <= time.time() - capacity['at'] < 5, 'Stale actual host capacity')
        current = cgroup_memory()
        plan = self.manifest['lanes'][self.name]
        # Proposed cold measurement bound; actual DML peak is not yet established.
        needed = plan['planning_nonreclaimable_gib'] * 1024**3
        available = min(capacity['available'] + current['anon'], capacity['total'] - 2*1024**3)
        require(needed <= min(available,current['limit']), 'Cold-work capacity deficit')
        clock_admission(time.time(), self.deadline, remaining_seconds + 120)
        (self.root/(label+'-initial-admission.json')).write_text(json.dumps({
            'planned_required_bytes':needed,'actual_capacity':capacity,'actual_cgroup':current,
            'remaining_work_seconds':remaining_seconds,'evidence_reserve_seconds':120,
            'deadline':self.deadline,'basis':plan['resource_basis'],
        },indent=2))

