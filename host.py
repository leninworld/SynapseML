"""Unexecuted ephemeral GitHub-hosted Docker controller. Never run on the Mac."""

import datetime
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
import urllib.request


def require(value, message):
    if not value:
        raise RuntimeError(message)


def main():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true",
            "Hosted GitHub Linux only; no Mac Docker access")
    require(os.getuid() == 1001, "Unexpected hosted runner UID")
    require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "No workflow or job rerun authority")
    require(len(os.sched_getaffinity(0)) >= 2, "Two actual allowed CPUs required")
    packet = Path(__file__).resolve().parent
    manifest = json.loads((packet / "manifest.json").read_text())
    require(manifest["execution_enabled"] is True, "Execution not approved/enabled")
    lane = os.environ["VALIDATION_LANE"]
    require(lane in manifest["lanes"], "Unknown lane")
    run_id = os.environ["GITHUB_RUN_ID"]
    require(re.fullmatch("[0-9]+", run_id), "Invalid run ID")
    require(os.environ["GITHUB_REPOSITORY"] == "leninworld/SynapseML", "Wrong diagnostic repository")
    request = urllib.request.Request(
        f"https://api.github.com/repos/leninworld/SynapseML/actions/runs/{run_id}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "validation-controller",
                 "Authorization": "Bearer " + os.environ["GH_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(256 * 1024 + 1)
        require(len(data) <= 256 * 1024, "Run metadata cap")
        run = json.loads(data)
    packet_head = subprocess.run(["git", "-C", str(packet), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    require(run["head_sha"] == os.environ["GITHUB_SHA"] == packet_head,
            "Run, workflow and packet commit mismatch")
    require(run["id"] == int(run_id) and run["head_branch"] == manifest["validation_branch"],
            "Run identity or branch mismatch")
    created = datetime.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).timestamp()
    deadline = created + manifest["target_seconds"]
    absolute = created + manifest["absolute_seconds"]
    require(time.time() < deadline, "Original workflow target expired including queue")
    root = Path(os.environ["RUNNER_TEMP"]) / ("synapse1669-" + run_id + "-" + lane)
    require(not root.exists(), "Existing run directory; no automatic replay")
    root.mkdir()
    runtime, capacity = root / "runtime", root / "capacity"
    runtime.mkdir()
    capacity.mkdir()
    source = Path(os.environ["GITHUB_WORKSPACE"]) / "source"
    require(source.is_dir(), "Missing source checkout")
    owned = []
    result = {"lane": lane, "workflow_created_epoch": created, "deadline_epoch": deadline,
              "absolute_epoch": absolute, "passed": False, "steps": []}
    result.update(head_sha=run["head_sha"], github_sha=os.environ["GITHUB_SHA"],
                  github_ref=os.environ["GITHUB_REF"], packet_head=packet_head)
    interfaces = list(Path("/sys/class/net").glob("*/statistics/rx_bytes"))
    rx_start = {str(path): int(path.read_text()) for path in interfaces}

    def update_capacity():
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        record = {"at": time.time(), "available": int(info["MemAvailable"].split()[0]) * 1024,
                  "total": int(info["MemTotal"].split()[0]) * 1024}
        temp = capacity / "host.new"
        temp.write_text(json.dumps(record))
        temp.replace(capacity / "host.json")

    def command(label, argv, seconds, cap=64 * 1024**2):
        require(time.time() < deadline, "Original workflow deadline")
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        start = time.monotonic()
        out = bytearray()
        try:
            while selector.get_map() or process.poll() is None:
                update_capacity()
                require(shutil.disk_usage(root).free > 4 * 1024**3, "Hosted disk pressure")
                received = sum(max(0, int(Path(path).read_text()) - start)
                               for path, start in rx_start.items())
                require(received <= manifest["lanes"][lane]["network_gib"] * 1024**3,
                        "Hosted network allowance exceeded")
                require(time.time() < deadline and time.monotonic() - start < seconds,
                        "Host step deadline: " + label)
                for key, _ in selector.select(0.25):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        require(len(out) + len(chunk) <= cap, "Host output cap")
                        out.extend(chunk)
            require(process.wait(timeout=3) == 0, "Host step failed: " + label)
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            selector.close()
            process.stdout.close()
            (root / (label + ".log")).write_bytes(out)
            result["steps"].append({"label": label, "exit": process.returncode,
                                    "seconds": time.monotonic() - start, "bytes": len(out)})
        return bytes(out)

    try:
        disk = shutil.disk_usage(root)
        result["initial_disk_receipt"] = {"path": str(root), "total": disk.total, "used": disk.used, "free": disk.free}
        require(shutil.disk_usage(root).free >= manifest["lanes"][lane]["minimum_start_disk_gib"] * 1024**3,
                "Insufficient starting disk for the complete priced lane")
        update_capacity()
        available = json.loads((capacity / "host.json").read_text())
        require(available["total"] >= (manifest["lanes"][lane]["memory_gib"] + 2) * 1024**3,
                "Host cannot hold priced container plus OS reserve")
        require(available["available"] >= manifest["lanes"][lane]["initial_available_gib"] * 1024**3,
                "Insufficient actual host available memory before bootstrap")
        for name, sha in manifest["packet_sha256"].items():
            require(hashlib.sha256((packet / name).read_bytes()).hexdigest() == sha,
                    "Packet input mismatch: " + name)
        actual_paths = set()
        for path in packet.rglob('*'):
            relative = path.relative_to(packet)
            if '.git' in relative.parts:
                continue
            require(not path.is_symlink(), "Symlink in diagnostic packet")
            if path.is_file():
                actual_paths.add(str(relative))
        require(actual_paths == set(manifest['packet_sha256']) | {'manifest.json'},
                "Extra/missing diagnostic packet file")
        real_workflow = packet / manifest['workflow_path']
        require(real_workflow.read_bytes() == (packet/'workflow.yml').read_bytes(),
                "Actual running workflow differs from reviewed workflow")
        workflows = set(str(x.relative_to(packet)) for x in (packet/'.github/workflows').rglob('*') if x.is_file())
        require(workflows == {manifest['workflow_path']}, "Unreviewed extra workflow")
        auth = subprocess.run(["git", "-C", str(source), "config", "--local", "--get-regexp",
                               "extraheader|credential"], capture_output=True, timeout=5)
        require(auth.returncode == 1 and not auth.stdout, "Source credential config")
        context = json.loads(command("docker-context", ["docker", "context", "inspect"], 10))
        require(len(context) == 1 and
                context[0]["Endpoints"]["docker"]["Host"] == "unix:///var/run/docker.sock",
                "Not the owned hosted Docker engine")
        for index, image in enumerate(manifest["input_images"]):
            command("pull-" + str(index), ["docker", "pull", "--platform", "linux/amd64", image], 300)
            metadata = json.loads(command("image-" + str(index),
                                         ["docker", "image", "inspect", image], 10))[0]
            require(metadata["Architecture"] == "amd64" and
                    any(value.endswith("@" + image.split("@", 1)[1])
                        for value in metadata["RepoDigests"]),
                    "Image identity mismatch")
        tag = "synapse1669-validation:" + run_id
        command("image-build", ["docker", "build", "--network=none", "--tag", tag,
                                "--file", str(packet / "Dockerfile"), str(packet)], 180)
        memory = str(manifest["lanes"][lane]["memory_gib"]) + "g"
        phases = ["setup", "validate"] if lane.startswith("python-") else ["validate"]
        for phase in phases:
            own = "synapse1669-" + run_id + "-" + lane + "-" + phase
            owned.append(own)
            command(phase + "-container-create", [
            "docker", "create", "--name", own, "--user", "1001:1001", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=512",
            "--memory=" + memory, "--memory-swap=" + memory, "--cpus=2", "--cpuset-cpus=" + ",".join(map(str, sorted(os.sched_getaffinity(0))[:2])), "--init",
            "--tmpfs", "/tmp:rw,nosuid,nodev,exec,size=1g",
            "--mount", f"type=bind,src={source},dst=/source,readonly",
            "--mount", f"type=bind,src={packet},dst=/packet,readonly",
            "--mount", f"type=bind,src={runtime},dst=/runtime",
            "--mount", f"type=bind,src={capacity},dst=/capacity,readonly",
            "--env", "VALIDATION_LANE=" + lane,
            "--env", "FORMATTED_NOTEBOOK_B64=" + os.environ.get("FORMATTED_NOTEBOOK_B64", ""),
            "--env", "FORMATTED_NOTEBOOK_SHA256=" + os.environ.get("FORMATTED_NOTEBOOK_SHA256", ""),
            "--env", "FORMATTED_PATCH_SHA256=" + os.environ.get("FORMATTED_PATCH_SHA256", ""),
            "--env", "VALIDATION_PHASE=" + phase,
            "--env", "VALIDATION_DEADLINE_EPOCH=" + str(deadline),
            tag,
            ], 20)
            command(phase + "-container-run", ["docker", "start", "--attach", own],
                    max(1, deadline - time.time()), cap=16 * 1024**2)
            state = json.loads(command(phase + "-container-state", ["docker", "inspect", "--format",
                                                                   "{{json .State}}", own], 10))
            require(not state["Running"] and not state["OOMKilled"] and state["ExitCode"] == 0,
                    "Owned container did not complete cleanly")
            result.setdefault("container_states", {})[phase] = state
            if phase == "setup":
                setup = json.loads((runtime / "setup-result.json").read_text())
                require(setup["setup_passed"] is True and setup["full_validation_passed"] is False,
                        "Incomplete environment setup")
        lane_result = json.loads((runtime / "lane-result.json").read_text())
        require(lane_result["passed"] is True, "Incomplete lane")
        result.update(passed=True, container_state=state, lane_result=lane_result)
        if lane == "python-notebook":
            notebook = runtime / "source" / manifest["notebook_path"]
            content = notebook.read_bytes()
            require(len(content) <= 65536 and not notebook.is_symlink(), "Notebook output size/type")
            frozen = json.loads((runtime / "frozen-documentation.json").read_text())
            require(hashlib.sha256(content).hexdigest() == frozen["source_sha256"][manifest["notebook_path"]], "Output notebook changed")
            result["formatted_notebook_sha256"] = hashlib.sha256(content).hexdigest()
            result["formatted_patch_sha256"] = frozen["tested_patch_sha256"]
            with open(os.environ["GITHUB_OUTPUT"], "a") as outputs:
                outputs.write("notebook_b64=" + base64.b64encode(content).decode() + "\n")
                outputs.write("notebook_sha256=" + result["formatted_notebook_sha256"] + "\n")
                outputs.write("patch_sha256=" + result["formatted_patch_sha256"] + "\n")
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        # No remove/prune/delete operation. Only stop this run's owned hosted
        # container; all Mac Docker data and external-disk files are untouched.
        stop_records = []
        for own in owned:
            try:
                subprocess.run(["docker", "stop", "--time", "2", own],
                               capture_output=True, timeout=10)
                check = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", own],
                                       capture_output=True, timeout=10)
                state = json.loads(check.stdout) if check.returncode == 0 else None
                stop_records.append({"name": own,
                                     "terminal": state is not None and not state["Running"]})
            except BaseException as error:
                stop_records.append({"name": own, "terminal": False,
                                     "error": f"{type(error).__name__}: {error}"})
        result["owned_stop_verified"] = all(item["terminal"] for item in stop_records)
        result["owned_stop_records"] = stop_records
        try:
            (root / "host-result.json").write_text(json.dumps(result, indent=2))
            selected = [root / "host-result.json", *root.glob("*.log"),
                        *runtime.glob("*.json"), *runtime.glob("*.jsonl"),
                        *runtime.glob("*.xml"), *runtime.glob("*-junit/*.xml"),
                        *runtime.glob("*.ipynb"), *runtime.glob("*.patch"), *runtime.glob("*.html"),
                        *runtime.glob("d0-*/*.json"), *runtime.glob("d0-*/*.jsonl"),
                        *runtime.glob("d0-*/*.xml"), *runtime.glob("*.log")]
            # Select proof directories, never the entire environment/source tree.
            selected = [path for path in selected if path.parent == runtime or
                        path.parent == root or
                        path.parent.name.endswith("-junit") or
                        any(part.startswith("d0-") for part in path.relative_to(root).parts)]
            from evidence_export import export_evidence
            result["evidence_export"] = export_evidence(root, selected, absolute)
            if not (result["evidence_export"]["complete"] and
                    result["evidence_export"]["critical_complete"]):
                result["passed"] = False
        except BaseException as error:
            result["passed"] = False
            result["evidence_export_error"] = f"{type(error).__name__}: {error}"
        finally:
            # Even failed cleanup, collection, or an expired export deadline
            # must leave an unambiguous terminal host receipt in the job log.
            print("HOST1669=" + json.dumps(result), flush=True)
    require(result["passed"] and result["owned_stop_verified"], "Full lane failed")


if __name__ == "__main__":
    main()
