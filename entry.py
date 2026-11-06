"""Unexecuted container entry point for the complete #1850 review packet."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

from validation_runtime import Runtime, docker_only, require


def download(item, destination, deadline):
    allowed = {"repo.maven.apache.org", "github.com", "release-assets.githubusercontent.com",
               "objects.githubusercontent.com"}

    def check(url):
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname in allowed and not parsed.username,
                "Unapproved dependency download host")

    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            check(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    check(item["url"])
    started = time.monotonic()
    sha = hashlib.new(item["hash_algorithm"])
    count = 0
    with urllib.request.build_opener(Redirect()).open(item["url"], timeout=20) as response:
        require(response.status == 200, "Dependency download status")
        with destination.open("xb") as output:
            while True:
                require(time.time() < deadline and time.monotonic() - started < 120,
                        "Dependency download deadline")
                data = response.read(65536)
                if not data:
                    break
                count += len(data)
                require(count <= item["maximum_bytes"], "Dependency download cap")
                output.write(data)
                sha.update(data)
    require(sha.hexdigest() == item["hash"], "Dependency download hash")
    return {"url": item["url"], "bytes": count,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def full_environment(lane):
    original = (Path("/source") / "environment.yml").read_text()
    block = "channels:\n  - conda-forge\n  - default\n"
    require(original.count(block) == 1, "Unexpected upstream environment channel layout")
    destination = lane.root / "environment-validation.yml"
    destination.write_text(original.replace(block, "channels:\n  - conda-forge\n", 1))
    executable = str(lane.root / "bootstrap/micromamba")
    lane.command("full-environment", [executable, "--no-rc", "create", "--root-prefix",
                                     "/runtime/mamba", "--name", "synapseml", "--file",
                                     str(destination), "--override-channels", "--channel",
                                     "conda-forge", "--yes"], 1200)
    prefix = lane.root / "mamba/envs/synapseml"
    require((prefix / "bin/python").is_file(), "Full upstream environment missing")
    os.environ.update(CONDA_DEFAULT_ENV="synapseml", CONDA_PREFIX=str(prefix),
                      PATH="/opt/node/bin:/opt/java/openjdk/bin:" + str(prefix / "bin") + ":" + os.environ["PATH"])
    lane.command("environment-versions", ["python", "-c",
        'import sys,pyspark; assert sys.version_info[:3]==(3,11,8); '
        'assert pyspark.__version__=="3.5.0"; print(sys.version,pyspark.__version__)'], 20)
    explicit = lane.command("resolved-conda-explicit", [executable, "--no-rc", "list",
                            "--root-prefix", "/runtime/mamba", "--name", "synapseml", "--explicit"], 60)
    require("@EXPLICIT" in explicit and "repo.anaconda.com" not in explicit,
            "Missing explicit Conda closure or disallowed default channel")
    lane.command("resolved-pip-versions", ["python", "-m", "pip", "list", "--format=json"], 60)
    def diagnostic(label, argv, seconds):
        # Only an explicitly bounded diagnostic exit/own step timeout is
        # optional. The unchanged global deadline, disk, OOM, memory, output
        # and process-ownership guards remain mandatory.
        try:
            lane.command(label, argv, seconds, expected=tuple(range(256)))
            record = lane.records[-1]
        except RuntimeError as error:
            path = lane.root / (label + ".json")
            if not path.is_file():
                raise
            record = json.loads(path.read_text())
            if record.get("error") != "RuntimeError: Step deadline":
                raise
            lane.runtime.guard()
        lane.runtime.guard()
        return {"label": label, "exit": record["exit"], "error": record["error"],
                "seconds": record["seconds"],
                "completed_successfully": record["exit"] == 0 and record["error"] is None}

    pip_check = diagnostic("environment-pip-check", ["python", "-m", "pip", "check"], 60)
    script = (
        "import hashlib,importlib.metadata as m,json; from pathlib import Path; "
        "records=[]; "
        "\nfor d in m.distributions():\n"
        " raw=d.read_text('RECORD'); direct=d.read_text('direct_url.json'); "
        "records.append({'name':d.metadata['Name'],'version':d.version,"
        "'record_sha256':hashlib.sha256(raw.encode()).hexdigest() if raw else None,"
        "'direct_url':json.loads(direct) if direct else None})\n"
        "print(json.dumps(records))"
    )
    lane.command("installed-python-distribution-records", ["python", "-c", script], 60)
    specs_script = (
        "import importlib.metadata as m,json; records=[]\n"
        "for d in m.distributions():\n"
        " if (d.read_text('INSTALLER') or '').strip()!='pip': continue\n"
        " direct=json.loads(d.read_text('direct_url.json') or '{}'); "
        "records.append({'name':d.metadata['Name'],'version':d.version,'url':direct.get('url')})\n"
        "print(json.dumps(records))"
    )
    raw = lane.command("resolved-pip-artifact-specs", ["python", "-c", specs_script], 60)
    specs = json.loads(raw)
    require(specs, "No resolved pip-managed distributions")
    requirements = []
    direct_wheel = "https://mmlspark.blob.core.windows.net/installers/horovod-0.28.1-cp311-cp311-linux_x86_64.whl"
    for item in specs:
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item["name"]) and
                re.fullmatch(r"[A-Za-z0-9.+!_-]+", item["version"]), "Unexpected package spec")
        if item["url"]:
            require(item["url"] == direct_wheel, "Unexpected direct package source")
            requirements.append(item["name"] + " @ " + item["url"])
        else:
            requirements.append(item["name"] + "==" + item["version"])
    requirement_file = lane.root / "resolved-pip-artifacts.txt"
    requirement_file.write_text("\n".join(requirements) + "\n")
    artifacts = lane.root / "resolved-pip-artifacts"
    artifacts.mkdir()
    capture = diagnostic("hashable-pip-artifacts", ["python", "-m", "pip", "download", "--no-deps",
                                                   "--dest", str(artifacts), "-r",
                                                   str(requirement_file)], 300)
    files = sorted(artifacts.iterdir())
    require(all(path.is_file() and not path.is_symlink() for path in files),
            "Unexpected pip artifact path type")
    from generated_artifacts import sha

    records = [{"name": path.name, "bytes": path.stat().st_size, "sha256": sha(path)} for path in files]
    (lane.root / "resolved-pip-artifact-hashes.json").write_text(json.dumps(records, indent=2))
    (lane.root / "environment-optional-diagnostics.json").write_text(json.dumps({
        "pip_check": pip_check, "archive_capture": capture,
        "expected_artifacts": len(specs), "captured_files": len(files),
        "archive_capture_complete": capture["completed_successfully"] and len(files) == len(specs),
        "status": "Evidence only; installed RECORD hashes and all required tests remain mandatory",
    }, indent=2))


def main():
    docker_only()
    require(os.getuid() == 1001, "Unexpected validation UID")
    packet, root = Path("/packet"), Path("/runtime")
    manifest = json.loads((packet / "manifest.json").read_text())
    require(manifest["execution_enabled"] is True, "Execution not approved/enabled")
    deadline = float(os.environ["VALIDATION_DEADLINE_EPOCH"])
    lane_name = os.environ["VALIDATION_LANE"]
    phase = os.environ["VALIDATION_PHASE"]
    require(lane_name in manifest["lanes"], "Unexpected lane")
    require(phase == "validate" or (phase == "setup" and lane_name == "python"), "Unexpected phase")
    reused_setup = lane_name == "python" and phase == "validate"
    for folder in ("home", "tmp", "bootstrap"):
        if reused_setup:
            require((root / folder).is_dir(), "Missing preserved setup folder")
        else:
            (root / folder).mkdir()
    os.environ.update(HOME="/runtime/home", TMPDIR="/runtime/tmp",
                      JAVA_HOME="/opt/java/openjdk", OMP_NUM_THREADS="1",
                      OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                      MAMBA_ROOT_PREFIX="/runtime/mamba", PIP_CACHE_DIR="/runtime/pip-cache",
                      PYSPARK_SUBMIT_ARGS="--driver-memory 1g pyspark-shell",
                      SPARK_SUBMIT_OPTS="-Duser.home=/runtime/home -Djava.io.tmpdir=/runtime/tmp",
                      NODE_OPTIONS="--max-old-space-size=6144", CI="true",
                      DOCUSAURUS_TELEMETRY_DISABLED="true", SYNAPSEML_ENABLE_PUBLISH="false")
    runtime = Runtime(deadline, root)
    result = {"lane": lane_name, "phase": phase, "passed": False,
              "setup_passed": False, "full_validation_passed": False}
    try:
        if reused_setup:
            setup = json.loads((root / "setup-result.json").read_text())
            require(setup["setup_passed"] and not setup["full_validation_passed"], "Setup not complete")
            prefix = root / "mamba/envs/synapseml"
            os.environ.update(CONDA_DEFAULT_ENV="synapseml", CONDA_PREFIX=str(prefix),
                              PATH="/opt/node/bin:/opt/java/openjdk/bin:" + str(prefix / "bin") + ":" + os.environ["PATH"])
        d0_root = root / ("d0-" + phase)
        d0_args = ["python", str(packet / "validation_d0.py"),
                    "--root", str(d0_root), "--original-deadline-epoch", str(deadline),
                    "--with-node"]
        if reused_setup:
            d0_args.append("--with-pytest")
        runtime.run("d0-" + phase, d0_args, packet, 90)
        fixtures = json.loads((d0_root / "D0-result.json").read_text())
        require(fixtures["status"] == "TINY_FIXTURES_PASS" and
                fixtures["node_image_path_exercised"], "D0 incomplete")
        inputs = []
        if not reused_setup:
            inputs.append(download(manifest["sbt"], root / "bootstrap/sbt-launch.jar", deadline))
        else:
            for name, filename in (("sbt", "sbt-launch.jar"), ("micromamba", "micromamba")):
                item = manifest[name]
                actual = hashlib.new(item["hash_algorithm"], (root / "bootstrap" / filename).read_bytes())
                require(actual.hexdigest() == item["hash"], "Reused bootstrap input changed")
        if lane_name == "python" and phase == "setup":
            executable = root / "bootstrap/micromamba"
            inputs.append(download(manifest["micromamba"], executable, deadline))
            executable.chmod(0o755)
        if inputs:
            (root / ("bootstrap-inputs-" + phase + ".json")).write_text(json.dumps(inputs, indent=2))
        from scala_validation import ScalaValidation

        lane = ScalaValidation(packet, root, root / "source", manifest, deadline, None)
        if lane_name in ("root", "core", "native"):
            lane.run_scala_lane(lane_name)
        elif lane_name == "python":
            if phase == "setup":
                lane.initial_source()
                full_environment(lane)
                from validation_runtime import cgroup_memory

                result.update(setup_passed=True, full_validation_passed=False,
                              setup_memory=cgroup_memory(), steps=lane.records)
                return
            require(lane.source.is_dir(), "Missing preserved baseline source")
            require(lane.command("reused-source-commit", ["git", "rev-parse", "HEAD"], 10).strip() ==
                    manifest["source_commit"], "Reused source base changed")
            require(not lane.command("reused-source-diff", ["git", "diff", "--name-only"], 10).strip(),
                    "Setup changed tracked baseline source")
            forks = lane.sbt("validation-fork-settings", ["show core / Test / fork",
                                                         "show lightgbm / Test / fork"], 180)
            require(len(re.findall(r"\[info\]\s+false\s*$", forks, re.M)) >= 2,
                    "Unconfirmed validation process model")
            from generated_artifacts import full_generated_lane
            from python_checks import affected_python_checks

            full_generated_lane(lane)
            affected_python_checks(lane, root / "candidate-artifact-manifest.json")
        else:
            require(lane_name == "website", "Unknown full lane")
            from website_validation import full_website_lane

            full_website_lane(lane)
        # Every lane verifies the exact candidate overlay after all tasks.
        require(lane.candidate, "No actual candidate validation")
        for name, sha in manifest["candidate_files"].items():
            require(hashlib.sha256((lane.source / name).read_bytes()).hexdigest() == sha,
                    "Candidate source changed during validation: " + name)
        tracked = lane.command("final-tracked-scope", ["git", "diff", "--name-only"], 10)
        expected_tracked = {name for name, sha in manifest["baseline_files"].items()
                            if sha is not None and sha != manifest["candidate_files"][name]}
        require(set(tracked.splitlines()) == expected_tracked, "Unrelated tracked source changed")
        inventory = lane.source / "core/src/test/scala/com/microsoft/azure/synapse/ml/train/TestInventory1850.scala"
        if lane_name in ("core", "native"):
            require(inventory.read_bytes() == (packet / "TestInventory1850.scala").read_bytes(),
                    "Test registration helper changed")
        else:
            require(not inventory.exists(), "Unexpected harness fixture in source/style scope")
        result.update(passed=True, full_validation_passed=True, steps=lane.records)
    except BaseException as error:
        result.update(error=f"{type(error).__name__}: {error}")
        raise
    finally:
        output_name = "setup-result.json" if phase == "setup" else "lane-result.json"
        (root / output_name).write_text(json.dumps(result, indent=2))
        print("LANE1850=" + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
