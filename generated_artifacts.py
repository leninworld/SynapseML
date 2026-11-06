"""Unexecuted branch-built artifact/provenance portion of the #1850 packet."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import zipfile

from validation_runtime import require


MODULES = {
    "synapse.ml.automl.TuneHyperparametersModel": "synapse/ml/automl/TuneHyperparametersModel.py",
    "synapse.ml.automl._TuneHyperparametersModel": "synapse/ml/automl/_TuneHyperparametersModel.py",
    "synapse.ml.automl.TuneHyperparameters": "synapse/ml/automl/TuneHyperparameters.py",
    "synapse.ml.lightgbm.LightGBMClassifier": "synapse/ml/lightgbm/LightGBMClassifier.py",
    "synapse.ml.train.TrainClassifier": "synapse/ml/train/TrainClassifier.py",
}


def inspect_wheel(wheel, candidate):
    hashes = {}
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate wheel entries")
        for module, member in MODULES.items():
            if member not in names:
                continue
            data = archive.read(member)
            hashes[module] = hashlib.sha256(data).hexdigest()
            if candidate and module == "synapse.ml.automl._TuneHyperparametersModel":
                require(b"def getBestModelParamsInfo" in data,
                        "Generated internal wrapper lacks new public getter")
                stub = archive.read(member[:-3] + ".pyi")
                require(b"def getBestModelParamsInfo(self) -> str:" in stub,
                        "Generated internal wrapper lacks typed getter")
    return hashes


def sha(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def resolve_runtime_classpath(output, source, module_jars):
    lines = [line.strip() for line in output.splitlines() if line.startswith("/runtime/")]
    require(len(lines) == 1, "Ambiguous actual runtime classpath")
    jars = []
    for entry in lines[0].split(os.pathsep):
        path = Path(entry).resolve(strict=True)
        if path.is_dir():
            relative = path.relative_to(source)
            require(len(relative.parts) == 4 and relative.parts[1] == "target" and
                    relative.parts[3] == "classes" and relative.parts[0] in module_jars,
                    "Unmapped project classes directory")
            path = module_jars[relative.parts[0]]
        require(path.is_file() and path.suffix == ".jar", "Non-JAR runtime input")
        if path not in jars:
            jars.append(path)
    require(set(module_jars.values()) <= set(jars), "Missing branch module on runtime classpath")
    return jars


def artifact_manifest(lane, label):
    """Build/install through upstream tasks, then hash actual generated artifacts."""
    lane.sbt(label + "-build-install", ["codegen", "core/installPipPackage",
                                       "lightgbm/installPipPackage", "publishM2"], 1200)
    version = "0.0.2-i1850-SNAPSHOT" if lane.candidate else "0.0.1-i1850-SNAPSHOT"
    module_jars, wheels = {}, []
    preserved = lane.root / (label + "-built-artifacts")
    preserved.mkdir()
    for module in ("core", "lightgbm"):
        jars = list(lane.source.glob(f"{module}/target/scala-*/synapseml-{module}_*-{version}.jar"))
        require(len(jars) == 1, "Missing/ambiguous branch-built module JAR")
        module_jars[module] = jars[0].resolve()
        packages = list(lane.source.glob(f"{module}/target/scala-*/generated/package/python/*.whl"))
        require(len(packages) == 1, "Missing/ambiguous generated wheel")
        wheels.extend(packages)
    python_hashes = {}
    wheel_records = []
    for wheel in wheels:
        # Upstream packagePython removes the previous wheel when the version
        # changes. Retain the original bytes before building the candidate.
        shutil.copy2(wheel, preserved / wheel.name)
        entries = inspect_wheel(wheel, lane.candidate)
        require(not (python_hashes.keys() & entries.keys()), "Ambiguous module across wheels")
        python_hashes.update(entries)
        wheel_records.append({"path": str(wheel), "bytes": wheel.stat().st_size,
                              "sha256": sha(wheel), "preserved": str(preserved / wheel.name)})
    require(python_hashes.keys() == MODULES.keys(), "Incomplete generated module hashes")
    output = lane.sbt(label + "-runtime-classpath", ["export lightgbm/Runtime/fullClasspath"], 180)
    jars = resolve_runtime_classpath(output, lane.source, module_jars)
    native = [path for path in jars if "lightgbmlib" in path.name]
    require(len(native) == 1 and sha(native[0]) == lane.manifest["native_jar_sha256"],
            "Native dependency identity mismatch")
    manifest = {
        "source_commit": lane.manifest["source_commit"],
        "source_kind": "candidate" if lane.candidate else "baseline",
        "tested_patch_sha256": lane.manifest["patch_sha256"] if lane.candidate else None,
        "jars": [{"path": str(path), "sha256": sha(path)} for path in jars],
        "python_modules": python_hashes,
        "jvm_classes": {
            "com.microsoft.azure.synapse.ml.automl.TuneHyperparametersModel": sha(module_jars["core"]),
            "com.microsoft.azure.synapse.ml.lightgbm.LightGBMClassifier": sha(module_jars["lightgbm"]),
        },
        "wheels": wheel_records,
    }
    destination = lane.root / (label + "-artifact-manifest.json")
    with destination.open("x") as result:
        json.dump(manifest, result, indent=2)
    return destination


def run_fresh_driver(lane, label, role, manifest, state):
    result = lane.root / (label + "-result.json")
    lane.command(label, ["python", str(lane.packet / "fresh_driver_model_info.py"), role,
                         "--manifest", str(manifest), "--state", str(state),
                         "--result", str(result)], 300)
    data = json.loads(result.read_text())
    require(data["role"] == role and data["manifest_sha256"] == sha(manifest),
            "Fresh-driver result identity mismatch")
    return data


def full_generated_lane(lane):
    """Called only after full environment setup and the actual D0 tiny fixtures."""
    import time

    require(lane.source.is_dir() and not lane.candidate, "Missing original baseline source")
    lane.datasets()
    started = time.monotonic()
    original = artifact_manifest(lane, "baseline")
    from python_checks import azure_black

    azure_black(lane, "baseline")
    lane.command("baseline-init-spark-ivy-warmup", ["python",
                 str(lane.packet / "warm_init_spark.py"), str(original),
                 str(lane.root / "baseline-ivy-provenance.json")], 600)
    legacy_state = lane.root / "baseline-saved-model"
    baseline_proof = run_fresh_driver(lane, "baseline-original-writer", "legacy-write",
                                     original, legacy_state)
    writer_seconds = next(step["seconds"] for step in lane.records
                          if step["label"] == "baseline-original-writer")
    # Three remaining fresh drivers and six real pytest collection/run JVMs.
    # Each gets 1.5x the measured original writer (which includes training,
    # prediction and save), plus 360s for Black, packaging and collection work.
    lane.memory_admission(started, 9 * 1.5 * writer_seconds + 360, remaining_factor=1.25)
    lane.apply_candidate()
    candidate = artifact_manifest(lane, "candidate")
    legacy_proof = run_fresh_driver(lane, "candidate-legacy-reader", "legacy-read",
                                   candidate, legacy_state)
    current_state = lane.root / "candidate-saved-model"
    writer = run_fresh_driver(lane, "candidate-new-writer", "write", candidate, current_state)
    reader = run_fresh_driver(lane, "candidate-new-reader", "read", candidate, current_state)
    proofs = [baseline_proof, legacy_proof, writer, reader]
    require(len({record["process_nonce"] for record in proofs}) == 4,
            "Fresh drivers did not produce distinct process records")
    # Subprocess launch/exit records and separate JVM code-source proofs, not
    # the nonce alone, establish actual independent driver executions.
    (lane.root / "fresh-drivers.json").write_text(json.dumps(proofs, indent=2))
