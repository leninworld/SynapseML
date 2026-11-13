"""Adapt the proven #874 branch-built artifact identity checks to core+cognitive."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

from validation_runtime import require

MODULES = {
    "synapse.ml.services.search.AzureSearchWriter": "synapse/ml/services/search/AzureSearchWriter.py",
    "synapse.ml.services.search.AddDocuments": "synapse/ml/services/search/AddDocuments.py",
    "synapse.ml.core.serialize.java_params_patch": "synapse/ml/core/serialize/java_params_patch.py",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_manifest(lane):
    scala_output = lane.sbt("actual-compile-scala-version", ["show ThisBuild / scalaVersion"], 120)
    require("[info] 2.12.17" in scala_output, "Unexpected actual build Scala version")
    lane.sbt("candidate-build-install", ["codegen", "core/installPipPackage",
                                      "cognitive/installPipPackage", "publishM2"], 900)
    jars, wheels, modules = {}, [], {}
    retained = lane.root / "candidate-built-artifacts"
    retained.mkdir()
    for module in ("core", "cognitive"):
        found = list(lane.source.glob(f"{module}/target/scala-*/synapseml-{module}_*-{lane.version}.jar"))
        require(len(found) == 1, "Missing/ambiguous branch JAR: " + module)
        jars[module] = found[0].resolve()
        packages = list(lane.source.glob(f"{module}/target/scala-*/generated/package/python/*.whl"))
        require(len(packages) == 1, "Missing/ambiguous branch wheel: " + module)
        wheel = packages[0]
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)), "Duplicate wheel entry")
            for name, member in MODULES.items():
                if member in names:
                    require(name not in modules, "Duplicate generated Python module")
                    modules[name] = hashlib.sha256(archive.read(member)).hexdigest()
        destination = retained / wheel.name
        shutil.copy2(wheel, destination)
        wheels.append({"path": str(wheel), "sha256": sha(wheel), "bytes": wheel.stat().st_size})
    require(set(modules) == set(MODULES), "Missing Python module provenance")
    output = lane.sbt("cognitive-runtime-classpath", ["export cognitive/Runtime/fullClasspath"], 180)
    lines = [line for line in output.splitlines() if line.startswith("/runtime/")]
    require(len(lines) == 1, "Ambiguous cognitive runtime classpath")
    classpath = []
    for entry in lines[0].split(os.pathsep):
        path = Path(entry).resolve(strict=True)
        if path.is_dir():
            parts = path.relative_to(lane.source).parts
            require(len(parts) == 4 and parts[1] == "target" and parts[3] == "classes" and
                    parts[0] in jars, "Unmapped project classes directory")
            path = jars[parts[0]]
        require(path.is_file() and path.suffix == ".jar", "Non-JAR runtime input")
        if path not in classpath:
            classpath.append(path)
    require(set(jars.values()) <= set(classpath), "Missing core/cognitive runtime JAR")
    result = {"source": lane.manifest["source_commit"], "compile_scala": "2.12.17",
              "python_modules": modules, "wheels": wheels,
              "jars": [{"path": str(path), "sha256": sha(path)} for path in classpath],
              "core_jar": str(jars["core"]), "cognitive_jar": str(jars["cognitive"])}
    file = lane.root / "candidate-artifact-manifest.json"
    file.write_text(json.dumps(result, indent=2))
    return file
