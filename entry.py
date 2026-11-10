"""Unexecuted container entry point for the complete #667 baseline packet."""

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


def main():
    docker_only()
    require(os.getuid() == 1001, "Unexpected validation UID")
    packet, root = Path('/packet'), Path('/runtime')
    manifest = json.loads((packet / 'manifest.json').read_text())
    require(manifest['execution_enabled'] is True, 'Execution not approved')
    deadline = float(os.environ['VALIDATION_DEADLINE_EPOCH'])
    lane_name, phase = os.environ['VALIDATION_LANE'], os.environ['VALIDATION_PHASE']
    require(lane_name in manifest['lanes'], 'Unknown lane')
    require(phase == 'validate' or (phase == 'setup' and lane_name.startswith('python-')), 'Unknown phase')
    reused = lane_name.startswith('python-') and phase == 'validate'
    for folder in ('home', 'tmp', 'bootstrap'):
        if reused:
            require((root / folder).is_dir(), 'Missing setup folder')
        else:
            (root / folder).mkdir()
    os.environ.update(HOME='/runtime/home', TMPDIR='/runtime/tmp', JAVA_HOME='/opt/java/openjdk',
                      OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                      MAMBA_ROOT_PREFIX='/runtime/mamba', PIP_CACHE_DIR='/runtime/pip-cache',
                      PYSPARK_SUBMIT_ARGS='--driver-memory 2g pyspark-shell',
                      SPARK_SUBMIT_OPTS='-Duser.home=/runtime/home -Djava.io.tmpdir=/runtime/tmp',
                      NODE_OPTIONS='--max-old-space-size=6144', CI='true',
                      DOCUSAURUS_TELEMETRY_DISABLED='true', SYNAPSEML_ENABLE_PUBLISH='false')
    from common import Lab
    lane = Lab(packet, root, manifest, deadline)
    result = {'lane': lane_name, 'phase': phase, 'passed': False, 'setup_passed': False,
              'full_validation_passed': False}
    try:
        if reused:
            setup = json.loads((root / 'setup-result.json').read_text())
            require(setup['setup_passed'] and not setup['full_validation_passed'], 'Missing complete setup')
            prefix = root / 'mamba/envs/synapseml'
            os.environ.update(CONDA_DEFAULT_ENV='synapseml', CONDA_PREFIX=str(prefix),
                              PATH='/opt/node/bin:/opt/java/openjdk/bin:' + str(prefix/'bin') + ':' + os.environ['PATH'])
        args = ['python', str(packet/'validation_d0.py'), '--root', str(root/('d0-'+phase)),
                '--original-deadline-epoch', str(deadline), '--with-node']
        # This code-only scope has no Markdown/codeblocks workflow; retain
        # the runtime/Git/XML/artifact/evidence fixtures, not image-page tests.
        lane.command('d0-'+phase, args, 90, cwd=packet)
        fixture = json.loads((root/('d0-'+phase)/'D0-result.json').read_text())
        require(fixture['status'] == 'TINY_FIXTURES_PASS', 'Incomplete G0')
        if not reused:
            records = [download(manifest['sbt'], root/'bootstrap/sbt-launch.jar', deadline)]
            lane.initial_source()
        else:
            records = []
            for name, filename in (('sbt','sbt-launch.jar'), ('micromamba','micromamba')):
                item = manifest[name]
                require(hashlib.new(item['hash_algorithm'], (root/'bootstrap'/filename).read_bytes())
                        .hexdigest() == item['hash'], 'Reused bootstrap changed')
            require(not lane.command('reused-source-clean', ['git','status','--porcelain'], 10).strip(),
                    'Setup changed baseline source')
        if lane_name.startswith('python-') and phase == 'setup':
            records.append(download(manifest['micromamba'], root/'bootstrap/micromamba', deadline))
            (root/'bootstrap/micromamba').chmod(0o755)
        (root/('bootstrap-inputs-'+phase+'.json')).write_text(json.dumps(records, indent=2))
        if lane_name.startswith('python-') and phase == 'setup':
            full_environment(lane)
            result.update(setup_passed=True)
            return
        from lanes import root_lane, python_lane
        if lane_name.startswith('root-'):
            root_lane(lane)
        elif lane_name.startswith('python-'):
            python_lane(lane)
        else:
            raise RuntimeError('Unknown #667 baseline workload')
        require(not lane.candidate, 'Product fix forbidden in G0 baseline')
        from baseline_checks import freeze
        freeze(lane, 'entry-final')
        result.update(source_kind='UNMODIFIED_BASELINE')
        result.update(passed=True, full_validation_passed=True)
    except BaseException as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        result['steps'] = lane.records
        name = 'setup-result.json' if phase == 'setup' else 'lane-result.json'
        (root/name).write_text(json.dumps(result, indent=2))
        print('LANE667='+json.dumps(result), flush=True)

if __name__ == '__main__':
    main()
