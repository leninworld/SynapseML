"""Actual branch-built LightGBM documentation wheels and JAR provenance."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile
from validation_runtime import require

MODULES = {
    "synapse.ml.lightgbm.LightGBMClassifier": "synapse/ml/lightgbm/LightGBMClassifier.py",
    "synapse.ml.lightgbm._LightGBMClassificationModel": "synapse/ml/lightgbm/_LightGBMClassificationModel.py",
    "synapse.ml.lightgbm.LightGBMClassificationModel": "synapse/ml/lightgbm/LightGBMClassificationModel.py",
    "synapse.ml.lightgbm.mixin": "synapse/ml/lightgbm/mixin.py",
    "synapse.ml.core.serialize.java_params_patch": "synapse/ml/core/serialize/java_params_patch.py",
}

def inspect_wheel(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate wheel entries")
        return {name: hashlib.sha256(archive.read(member)).hexdigest()
                for name, member in MODULES.items() if member in names}

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
    lane.sbt(label + "-build-install", ["codegen", "core/installPipPackage",
                                      "lightgbm/installPipPackage", "publishM2"], 1200)
    module_jars, wheels, modules = {}, [], {}
    retained = lane.root / (label + "-built-artifacts")
    retained.mkdir()
    for module in ("core", "lightgbm"):
        jars = list(lane.source.glob(f"{module}/target/scala-*/synapseml-{module}_*-{lane.version}.jar"))
        require(len(jars) == 1, "Missing/ambiguous branch-built JAR: " + module)
        module_jars[module] = jars[0].resolve()
        packages = list(lane.source.glob(f"{module}/target/scala-*/generated/package/python/*.whl"))
        require(len(packages) == 1, "Missing/ambiguous branch-built wheel: " + module)
        wheel = packages[0]
        entries = inspect_wheel(wheel)
        require(not modules.keys() & entries.keys(), "Ambiguous installed module")
        modules.update(entries)
        shutil.copy2(wheel, retained / wheel.name)
        wheels.append({"path": str(wheel), "bytes": wheel.stat().st_size,
                       "sha256": sha(wheel), "preserved": str(retained / wheel.name)})
    require(modules.keys() == MODULES.keys(), "Incomplete generated Python provenance")
    classpath = lane.sbt(label + "-runtime-classpath", ["export lightgbm/Runtime/fullClasspath"], 180)
    jars = resolve_runtime_classpath(classpath, lane.source, module_jars)
    native = [path for path in jars if path.name == "lightgbmlib-3.3.510.jar"]
    require(len(native) == 1, "Missing actual upstream native LightGBM dependency")
    manifest = {
        "source_commit": lane.manifest["source_commit"], "source_kind": label,
        "input_patch_sha256": lane.manifest["patch_sha256"],
        "tested_patch_sha256": json.loads((lane.root/"frozen-documentation.json").read_text())["tested_patch_sha256"],
        "python_modules": modules, "wheels": wheels,
        "jars": [{"path": str(path), "sha256": sha(path)} for path in jars],
        "jvm_classes": {
            "com.microsoft.azure.synapse.ml.codegen.Wrappable": sha(module_jars["core"]),
            "com.microsoft.azure.synapse.ml.lightgbm.LightGBMClassifier": sha(module_jars["lightgbm"]),
            "com.microsoft.azure.synapse.ml.lightgbm.LightGBMClassificationModel": sha(module_jars["lightgbm"]),
            "com.microsoft.ml.lightgbm.lightgbmlib": sha(native[0]),
        },
    }
    path = lane.root / (label + "-artifact-manifest.json")
    path.write_text(json.dumps(manifest, indent=2))
    return path
