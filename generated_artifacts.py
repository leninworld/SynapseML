"""Actual branch-built image-writer wheels and JAR provenance."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile
from validation_runtime import require

MODULES = {
    'synapse.ml.vw.VowpalWabbitClassifier': 'synapse/ml/vw/VowpalWabbitClassifier.py',
    'synapse.ml.vw._VowpalWabbitClassifier': 'synapse/ml/vw/_VowpalWabbitClassifier.py',
    'synapse.ml.vw.VowpalWabbitClassificationModel': 'synapse/ml/vw/VowpalWabbitClassificationModel.py',
    'synapse.ml.vw._VowpalWabbitClassificationModel': 'synapse/ml/vw/_VowpalWabbitClassificationModel.py',
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
    lane.sbt(label + "-build-install", [m+'/codegen' for m in
        ['core','deepLearning','cognitive','vw','lightgbm','opencv']] +
        ['core/installPipPackage','vw/installPipPackage','publishM2'], 1200)
    module_jars, wheels, modules = {}, [], {}
    retained = lane.root / (label + "-built-artifacts")
    retained.mkdir()
    for module in ("core", "vw"):
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
    classpath = lane.sbt(label + "-runtime-classpath", ["export vw/Runtime/fullClasspath"], 180)
    jars = resolve_runtime_classpath(classpath, lane.source, module_jars)
    native = [path for path in jars if path.name == "vw-jni-9.3.0.jar"]
    require(len(native) == 1, "Missing actual upstream native VW JNI dependency")
    manifest = {
        "source_commit": lane.manifest["source_commit"], "source_kind": label,
        "tested_patch_sha256": lane.manifest["patch_sha256"] if lane.candidate else None,
        "python_modules": modules, "wheels": wheels,
        "jars": [{"path": str(path), "sha256": sha(path)} for path in jars],
        "jvm_classes": {
            "com.microsoft.azure.synapse.ml.vw.VowpalWabbitClassifier": sha(module_jars["vw"]),
            "com.microsoft.azure.synapse.ml.vw.VowpalWabbitClassificationModel": sha(module_jars["vw"]),
        },
    }
    path = lane.root / (label + "-artifact-manifest.json")
    path.write_text(json.dumps(manifest, indent=2))
    return path
