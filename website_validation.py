"""Unexecuted full docgen/website lane; no browser or hosted publication."""

import hashlib
import json
from pathlib import Path
import time

from validation_runtime import require


def in_website(lane, label, argv, seconds):
    record = lane.runtime.run(label, argv, lane.source / "website", seconds)
    lane.records.append({"label": label, **record})


def full_website_lane(lane):
    lane.initial_source()
    for name, expected in lane.manifest["website_input_sha256"].items():
        require(hashlib.sha256((lane.source / "website" / name).read_bytes()).hexdigest() == expected,
                "Website input changed")
    in_website(lane, "npm-ci", ["npm", "ci", "--no-audit", "--no-fund"], 900)
    lane.command("docgen-install", ["python3", "-m", "pip", "install", "--user", "--no-input",
                                    "-e", "tools/docgen", "nbconvert==7.16.6",
                                    "beautifulsoup4", "requests", "pyyaml"], 300)
    lane.command("docgen-environment", ["python3", "-m", "pip", "freeze"], 60)
    lane.command("baseline-full-docgen", ["python3", str(lane.packet / "website_generate.py")], 600)
    in_website(lane, "baseline-website-tests", ["npm", "test"], 300)
    start = time.monotonic()
    in_website(lane, "baseline-full-website-build", ["npm", "run", "build"], 900)
    prior = sum(step["seconds"] for step in lane.records if step["label"] in
                ("baseline-full-docgen", "baseline-website-tests"))
    lane.memory_admission(start, 1.25 * prior + 480, remaining_factor=1.25)
    lane.apply_candidate()
    lane.command("candidate-full-docgen", ["python3", str(lane.packet / "website_generate.py")], 600)
    in_website(lane, "candidate-website-tests", ["npm", "test"], 300)
    in_website(lane, "candidate-full-website-build", ["npm", "run", "build"], 900)
    directory = lane.source / "website/build"
    matches = [path for path in directory.rglob("*.html")
               if "Inspect the selected training parameters" in path.read_text(errors="replace")]
    require(len(matches) == 1, "Missing/ambiguous rendered changed page")
    page = matches[0]
    text = page.read_text()
    for expected in ("getBestModelInfo", "getBestModelParamsInfo", "numLeaves", "Python API"):
        require(expected in text, "Rendered page lacks required content: " + expected)
    (lane.root / "rendered-page.json").write_text(json.dumps({
        "path": str(page), "bytes": page.stat().st_size,
        "sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
        "evidence": "Actual complete generated production website; no browser screenshot yet",
    }, indent=2))
    lane.command("actual-npm-start-and-rendered-page", ["python3",
                 str(lane.packet / "website_check.py")], 600)
