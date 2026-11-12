"""Private G0 lane. Not execution authority or candidate documentation validation."""

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from bootstrap import download
from validation_runtime import require


def measure(lane):
    lane.initial_admission("native-interop-complete-finish", remaining_seconds=900)
    wheel = lane.manifest["lightgbm"]
    destination = lane.root / "bootstrap/lightgbm-3.3.5-py3-none-manylinux1_x86_64.whl"
    receipt = download(wheel, destination, lane.deadline)
    require(receipt["bytes"] == wheel["exact_bytes"], "Wheel size identity")
    lane.command("extra-wheel-install", ["python", "-m", "pip", "install", "--no-deps",
                                        "--no-index", str(destination)], 60)
    lane.command("extra-wheel-import", ["python", "-c",
        "import lightgbm,numpy,pandas,scipy,sklearn; "
        "assert lightgbm.__version__=='3.3.5'; "
        "print(lightgbm.__version__,numpy.__version__,pandas.__version__,"
        "scipy.__version__,sklearn.__version__)"], 30)
    lane.command("private-generator-syntax", ["python", "-c",
        "import ast;from pathlib import Path;ast.parse(Path('/packet/fixture_generator.py').read_text())"], 10)
    lane.command("real-python-train-export-reference",
                 ["python", str(lane.packet / "fixture_generator.py")], 120)
    fixture = lane.root / "interop874"
    reference = json.loads((fixture / "python-reference.json").read_text())
    require(reference["rows"] == 14 and reference["trees"] > 0, "Wrong reference selection")
    for filename, expected in [("python-model.txt", reference["model_sha256"]),
                               ("expected.tsv", reference["expected_sha256"])]:
        require(hashlib.sha256((fixture / filename).read_bytes()).hexdigest() == expected,
                "Reference fixture hash mismatch")
    tasks = ['set lightgbm / Test / unmanagedSources += file("/packet/NativeInterop874Probe.scala")',
             "lightgbm/testOnly com.microsoft.azure.synapse.ml.lightgbm.split6.NativeInterop874Probe"]
    lane.sbt("actual-native-interop-early", tasks, 1200)
    matches = []
    for path in (lane.source / "lightgbm/target/test-reports").glob("*.xml"):
        report = ET.parse(path).getroot()
        if report.get("name") == "com.microsoft.azure.synapse.ml.lightgbm.split6.NativeInterop874Probe":
            matches.append((path, report))
    require(len(matches) == 1, "Missing/ambiguous native parity report")
    path, report = matches[0]
    require(len(list(report.iter("testcase"))) == 1, "Wrong native parity selection")
    require(not any(list(report.iter(tag)) for tag in ("failure", "error", "skipped")),
            "Native parity failure or skip")
    (lane.root / "native-interop.xml").write_bytes(path.read_bytes())
    require((fixture / "scala-parity-passed.txt").is_file(), "Missing actual parity marker")
    require(not lane.command("final-source-clean", ["git", "status", "--porcelain"], 10).strip(),
            "G0 changed upstream source")
    proof = {"source_commit": lane.manifest["source_commit"], "source_overlay": False,
             "fixture": reference, "native_pair": "Python3.3.5/lightgbmlib3.3.510",
             "actual_native_parity": True, "candidate_validation": False,
             "tutorial_or_website_executed": False,
             "limits": "No general version compatibility, managed-runtime or final candidate claim"}
    (lane.root / "interop-measurement.json").write_text(json.dumps(proof, indent=2))
