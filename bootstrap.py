from pathlib import Path
import hashlib,json,os,time,urllib.parse,urllib.request
from validation_runtime import require

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
    require("repo.anaconda.com" not in explicit and "https://conda.anaconda.org/conda-forge/" in explicit,
            "Missing resolved Conda URLs or disallowed default channel")
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
    raw = lane.command("installed-python-distribution-records", ["python", "-c", script], 60)
    distributions = json.loads(raw)
    conda_urls = sorted(line.strip() for line in explicit.splitlines() if line.startswith('https://'))
    require(conda_urls and distributions, 'Empty resolved environment closure')
    closure = {'conda_urls':conda_urls,'python_distributions':sorted(distributions,key=lambda item:item['name'].lower())}
    (lane.root/'environment-closure.json').write_text(json.dumps(closure,sort_keys=True,indent=2))
    (lane.root / "environment-optional-diagnostics.json").write_text(json.dumps({
        "pip_check": pip_check,
        "status": "Actual diagnostic result; installed RECORD hashes recorded, no second wheel download",
    }, indent=2))
