"""Unexecuted tiny-fixture lane; run only under the final approved controller."""

import argparse
import base64
import hashlib
import gzip
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import time
import zipfile
import secrets
import xml.etree.ElementTree as ET

from validation_runtime import (
    Runtime, admit_from_baseline, cgroup_memory, docker_only,
    extract_archive, require, check_csv_header, check_junit, decode_evidence, MemoryTracker,
)


def rejected(call, expected, error_type=RuntimeError):
    try:
        call()
    except error_type as error:
        require(str(error) == expected, f"Wrong rejection: {error}")
    else:
        raise RuntimeError(f"Fixture incorrectly accepted: {expected}")


def main():
    docker_only()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--original-deadline-epoch", type=float, required=True)
    parser.add_argument("--with-node", action="store_true")
    parser.add_argument("--with-pytest", action="store_true")
    args = parser.parse_args()
    require(not args.root.exists(), "D0 evidence already exists; no replay")
    args.root.mkdir(parents=True)
    runtime = Runtime(args.original_deadline_epoch, args.root)
    memory = cgroup_memory()
    runtime.guard()
    runtime.run("java-path", ["java", "-version"], args.root, 10)
    require(re.search(r'version\s+"11(?:\.|\")',
                      (args.root / "java-path.log").read_text()), "Expected pinned JDK11")
    runtime.run("git-path", ["git", "--version"], args.root, 5)
    helper_path = Path("/source/website/doctest.py")
    spec = importlib.util.spec_from_file_location("actual_upstream_doctest", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    probe_folder = args.root / "helper-probe"
    probe_folder.mkdir()
    probe = probe_folder / "_Probe.md"
    probe.write_text('<!--pytest-codeblocks:cont-->\n```python\n'
                     'assert D0VALUE == 42\nprint("PYTEST_HELPER_SHAPE_EXECUTED")\n```\n')
    helper.add_python_helper_to_markdown(str(probe_folder), probe.name, "0.0.1-i658-SNAPSHOT")
    transformed = probe.read_text()
    require(len(re.findall(r"```python\n", transformed)) == 2 and
            transformed.count("<!--pytest-codeblocks:cont-->") == 1 and
            "com.microsoft.azure:synapseml_2.12:0.0.1-i658-SNAPSHOT" in transformed,
            "Actual upstream Markdown helper structure changed")
    pair_items = None
    if args.with_pytest:
        # Exercise the real installed parser on the real helper's markup shape,
        # replacing ONLY the startup body with pure math. This is a synthetic
        # parser fixture, not a Spark or product-transport verification.
        probe.write_text(re.sub(r"```python\n.*?\n```",
                               "```python\nD0VALUE = 42\n```", transformed, count=1, flags=re.S))
        witness = probe_folder / "continued-witness.txt"
        nonce = secrets.token_hex(16)
        require(not witness.exists(), "Parser witness pre-exists")
        os.environ.update(IMAGE_EXAMPLE_WITNESS=str(witness), IMAGE_EXAMPLE_NONCE=nonce)
        statement = ("import os\nfrom pathlib import Path\n"
                     "with Path(os.environ['IMAGE_EXAMPLE_WITNESS']).open('x') as witness_file:\n"
                     "    witness_file.write(os.environ['IMAGE_EXAMPLE_NONCE'])")
        # The exact same statement in a non-Python fence is a negative control.
        negative = probe_folder / "_Negative.md"
        negative.write_text("```text\n" + statement + "\n```\n")
        runtime.run("actual-pytest-nonpython-negative", [sys.executable, "-m", "pytest", "-q",
                    "--codeblocks", "-p", "no:cacheprovider", str(negative)], probe_folder, 30, expected=(5,))
        require(not witness.exists(), "Non-Python fence executed")
        positive = probe.read_text()
        require(positive.count('print("PYTEST_HELPER_SHAPE_EXECUTED")') == 1,
                "Missing synthetic continuation location")
        probe.write_text(positive.replace('print("PYTEST_HELPER_SHAPE_EXECUTED")', statement, 1))
        report = args.root / "helper-parser.xml"
        runtime.run("actual-pytest-helper-parser", [sys.executable, "-m", "pytest", "-q", "-s",
                    "--codeblocks", "-p", "no:cacheprovider", "--junitxml=" + str(report), str(probe)], probe_folder, 30)
        parsed = ET.parse(report).getroot()
        cases = list(parsed.iter("testcase"))
        require(cases and not list(parsed.iter("failure")) and not list(parsed.iter("error")) and
                not list(parsed.iter("skipped")), "Actual pytest helper-shape fixture failed")
        require(witness.is_file() and witness.read_text() == nonce,
                "Actual parser omitted the continued example witness")
        pair_items = len(cases)
    # Exercise the exact new overlay/injection and XML classifier before project work.
    from common import Lab, sha
    import shutil
    fixture_root = args.root / "apply-fixture"
    fixture_root.mkdir()
    fixture_source = fixture_root / "source"
    fixture_source.mkdir()
    packet = Path(__file__).resolve().parent
    manifest = json.loads((packet / "manifest.json").read_text())
    for name, expected in manifest["baseline_files"].items():
        if expected is not None:
            target = fixture_source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path('/source') / name, target)
            require(sha(target) == expected, 'G0 baseline source changed')
    runtime.run('fixture-git-init', ['git', 'init', str(fixture_source)], args.root, 5)
    runtime.run('fixture-git-add', ['git', 'add', '.'], fixture_source, 5)
    runtime.run('fixture-git-commit', ['git', '-c', 'user.name=Fixture', '-c',
                'user.email=fixture@example.invalid', 'commit', '-m', 'Synthetic overlay fixture'],
                fixture_source, 5)
    lab = Lab(packet, fixture_root, manifest, args.original_deadline_epoch)
    lab.inject([manifest['scala_regression_path'], manifest['python_regression_path']])
    (fixture_source / 'synthetic-build-output.txt').write_text('pre-existing owned artifact')
    lab.apply()
    lab.verify_candidate('fixture-final')
    lab.initial_admission('fixture-cold-admission',remaining_seconds=1)
    rejected(lambda: lab.initial_admission('fixture-expired-projection',remaining_seconds=10**9),
             'Full finish cannot fit original clock at this stage')
    # XML fixture drives the production Lab.suites parser, including wrong-cause rejection.
    suite = 'Fixture'
    xml_root = fixture_source / 'core/target/test-reports'
    xml_root.mkdir(parents=True)
    lab.manifest = {**manifest, 'scala_cases': {suite: ['pass', 'fail']}}
    def fake_sbt(label, tasks, seconds, expected):
        (xml_root/'TEST-Fixture.xml').write_text(
            '<testsuite name="Fixture"><testcase name="pass"/>'
            '<testcase name="fail"><failure message="specific fixture cause"/>'
            '</testcase></testsuite>')
    lab.sbt = fake_sbt
    lab.suites('fixture-xml-valid', [suite], {suite: {'fail': ['specific fixture cause']}})
    rejected(lambda: lab.suites('fixture-xml-bad-cause', [suite],
                                {suite: {'fail': ['different cause']}}),
             'Wrong fail-before cause: fail')
    # Failed runtime receipts must survive the Lab adapter's exception path.
    runtime_lab = Lab(packet, args.root / 'receipt-fixture', manifest, args.original_deadline_epoch)
    runtime_lab.root.mkdir()
    try:
        runtime_lab.command('expected-command-failure', [sys.executable, '-c', 'raise SystemExit(1)'],
                            10, cwd=args.root)
    except RuntimeError:
        require(runtime_lab.records and runtime_lab.records[-1]['exit'] == 1,
                'Failed command missing from lane records')
    else:
        raise RuntimeError('Nonzero fixture command accepted')
    # The production JUnit classifier handles only these observed inherited assertions.
    reader = 'com.microsoft.azure.synapse.ml.io.split1.ImageReaderSuite'
    lab.manifest = {**manifest, 'scala_cases': {reader: ['read images with subsample']}}
    def inherited_sbt(label, tasks, seconds, expected):
        (xml_root/'TEST-Fixture.xml').write_text(
            '<testsuite name="' + reader + '"><testcase name="read images with subsample">'
            '<failure message="scala.Predef.Set.apply[Int](2, 3, 4).apply(imageDF.count().toInt) was false '
            '(ImageReaderSuite.scala:51)"/></testcase></testsuite>')
    lab.sbt = inherited_sbt
    original = lab.suites('fixture-inherited-baseline', [reader], capture_inherited=True)
    lab.suites('fixture-inherited-identical', [reader], inherited=original)
    wrong = {reader: {'inherited_signatures': {}}}
    rejected(lambda: lab.suites('fixture-inherited-different', [reader], inherited=wrong),
             'Changed inherited failure set/assertion/line: ' + reader)
    require((fixture_root/'fixture-inherited-different-junit/TEST-Fixture.xml').is_file() and
            (fixture_root/'fixture-inherited-different-selection.json').is_file(),
            'Rejected classifier lost JUnit/selection evidence')
    actual_page_items = {}
    if args.with_pytest:
        for role, source in [('baseline', Path('/source/docs/Quick Examples/transformers/core/_Image.md')),
                             ('candidate', packet/'candidate-files/docs/Quick Examples/transformers/core/_Image.md')]:
            folder = args.root / ('actual-page-' + role)
            folder.mkdir()
            sample = folder / '_Image.md'
            shutil.copy2(source, sample)
            helper.add_python_helper_to_markdown(str(folder), sample.name, '0.0.2-i658-SNAPSHOT')
            runtime.run('actual-page-collect-'+role, [sys.executable,'-m','pytest','--collect-only','-q',
                        '--codeblocks','-p','no:cacheprovider',str(sample)],folder,30)
            text = (args.root/('actual-page-collect-'+role+'.log')).read_text()
            nodes = [line.strip() for line in text.splitlines() if '_Image.md::' in line]
            require(nodes and len(nodes) == len(set(nodes)), 'Actual full-page collection missing/duplicate')
            actual_page_items[role] = {'count':len(nodes),'nodes':nodes,
                                      'transformed_sha256':sha(sample)}
    from compare_environments import compare
    baseline_closure = {'conda_urls':['https://example.invalid/numpy.conda#hash123'],
                       'python_distributions':[{'name':'example_package','version':'1.0',
                         'direct_url':{'url':'https://example.invalid/package.whl'},'record_sha256':'a'*64}]}
    candidate_closure = json.loads(json.dumps(baseline_closure))
    candidate_closure['python_distributions'][0]['record_sha256'] = 'b'*64
    require(compare(baseline_closure,candidate_closure)['equal'],'RECORD bytes incorrectly gate equality')
    left, right = args.root/'env-baseline.json',args.root/'env-candidate.json'
    left.write_text(json.dumps(baseline_closure));right.write_text(json.dumps(candidate_closure))
    runtime.run('environment-equality-cli',[sys.executable,str(packet/'collect.py'),'--compare-environments',
                str(left),str(right),str(args.root/'environment-equal.json')],args.root,5)
    require(json.loads((args.root/'environment-equal.json').read_text())['equal'],
            'Actual environment comparison CLI omitted equality')
    candidate_closure['python_distributions'][0]['version'] = '2.0'
    right.write_text(json.dumps(candidate_closure))
    runtime.run('environment-mismatch-cli',[sys.executable,str(packet/'collect.py'),'--compare-environments',
                str(left),str(right),str(args.root/'environment-mismatch.json')],args.root,5,expected=(1,))
    require(not json.loads((args.root/'environment-mismatch.json').read_text())['equal'],
            'Actual environment comparison CLI accepted mismatched versions')
    changed_url = json.loads(json.dumps(baseline_closure))
    changed_url['conda_urls'][0] += '-changed'
    require(not compare(baseline_closure,changed_url)['equal'],'Conda URL/hash mismatch accepted')
    changed_direct = json.loads(json.dumps(baseline_closure))
    changed_direct['python_distributions'][0]['direct_url']['url'] += '-changed'
    require(not compare(baseline_closure,changed_direct)['equal'],'Direct URL mismatch accepted')
    payload = b"label,feature\n0,1\n"
    from generated_artifacts import MODULES, inspect_wheel, resolve_runtime_classpath
    from collect import reassemble
    from evidence_export import export_evidence
    from projection_checks import verify_projection

    evidence_root = args.root / "priority-evidence-fixture"
    evidence_root.mkdir()
    (evidence_root / "host-result.json").write_text('{"passed":false}')
    (evidence_root / "lane-result.json").write_text('{"passed":false}')
    (evidence_root / "z-test.xml").write_text('<testsuite tests="1"/>')
    (evidence_root / "a-build.log").write_bytes(b"x" * 4096 + b"FAILURE-TAIL")
    emitted = []
    exported = export_evidence(evidence_root, list(evidence_root.iterdir()),
                               args.original_deadline_epoch,
                               emit=lambda line, **kwargs: emitted.append(line), cap=256,
                               tail_bytes=32)
    # Tiny synthetic cap forces full-log omission, while preserving structured
    # proof and the requested tail. No full build/test is substituted here.
    records = [json.loads(line.split("=", 1)[1]) for line in emitted]
    reconstructed = reassemble(records)
    require(exported["complete"] and exported["critical_complete"] and
            list(reconstructed)[:4] == ["evidence-export.json", "host-result.json",
                                       "lane-result.json", "z-test.xml"] and
            reconstructed["a-build.log.tail"].endswith(b"FAILURE-TAIL") and
            any(item["reason"] == "full-log-budget" for item in exported["omissions"]),
            "Priority evidence fixture lost structured results")
    expired = export_evidence(evidence_root, [], time.time() - 1,
                              emit=lambda *args, **kwargs: None)
    require(not expired["complete"] and not expired["critical_complete"],
            "Expired evidence export falsely succeeded")

    wheel = args.root / "synthetic.whl"
    with zipfile.ZipFile(wheel, "x") as writer:
        for member in MODULES.values():
            writer.writestr(member, "# synthetic module\n")
    require(inspect_wheel(wheel).keys() == MODULES.keys(), "Wheel module fixture failed")
    fake_source = args.root / "classpath-source"
    fake_classes = fake_source / "core/target/scala-2.12/classes"
    fake_classes.mkdir(parents=True)
    fake_jar = args.root / "core.jar"
    fake_jar.write_bytes(b"synthetic classpath-mapping fixture, not a loadable product JAR")
    classpath = str(fake_classes) + ":" + str(fake_jar)
    require(resolve_runtime_classpath(classpath, fake_source, {"core": fake_jar}) == [fake_jar],
            "Project class directory mapping fixture failed")
    rejected(lambda: resolve_runtime_classpath(classpath + "\n" + classpath,
                                               fake_source, {"core": fake_jar}),
             "Ambiguous actual runtime classpath")
    inputs, outputs = args.root / "projection-in", args.root / "projection-out"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "sample.md").write_text("unchanged markdown\n")
    (inputs / "notebook.ipynb").write_text("{}")
    (outputs / "sample.md").write_text("unchanged markdown\n")
    (outputs / "notebook.md").write_text("---\ntitle: sample\n---\n")
    (outputs / "tracked.md").write_text("pre-existing tracked output\n")
    projection = verify_projection(inputs, outputs, {"tracked.md"})
    require(projection["input_files"] == 2 and projection["output_files"] == 3,
            "Tracked website projection fixture failed")
    (outputs / "extra.md").write_text("unaccounted output\n")
    rejected(lambda: verify_projection(inputs, outputs, {"tracked.md"}),
             "Incomplete or extra generated output: ['extra.md']", ValueError)
    csv_path = args.root / "header.csv"
    csv_path.write_bytes(payload)
    check_csv_header(csv_path, ["label", "feature"])
    rejected(lambda: check_csv_header(csv_path, ["label", "feature-changed"]),
             "Dataset header mismatch")
    encoded = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
               "base64": base64.b64encode(payload).decode("ascii")}
    require(decode_evidence(encoded, 1024) == payload, "Evidence fixture failed")
    compressed = {**encoded, "encoding": "gzip-base64",
                  "base64": base64.b64encode(gzip.compress(payload)).decode("ascii")}
    require(decode_evidence(compressed, 1024) == payload, "Compressed evidence fixture failed")
    rejected(lambda: decode_evidence({**compressed, "bytes": 1}, 1024),
             "Incomplete/oversized compressed evidence")
    rejected(lambda: decode_evidence({**encoded, "sha256": "0" * 64}, 1024),
             "Evidence hash mismatch")
    rejected(lambda: decode_evidence({**encoded, "bytes": len(payload) + 1}, 1024),
             "Evidence decoded length mismatch")
    whole = os.urandom(8192) + b"end"
    chunks = []
    for index, piece in enumerate((whole[:8192], whole[8192:])):
        chunks.append({"path": "runtime/example.log", "bytes": len(piece),
                       "sha256": hashlib.sha256(piece).hexdigest(), "encoding": "gzip-base64",
                       "index": index, "parts": 2, "file_bytes": len(whole),
                       "file_sha256": hashlib.sha256(whole).hexdigest(),
                       "base64": base64.b64encode(gzip.compress(piece)).decode("ascii")})
    require(reassemble(chunks)["runtime/example.log"] == whole, "Chunked evidence fixture failed")
    rejected(lambda: reassemble(chunks[:1]), "Missing evidence chunks")
    rejected(lambda: reassemble(chunks + chunks[:1]), "Duplicate evidence chunk")
    require(all(len(json.dumps(record).encode()) < 14000 for record in chunks),
            "Worst-case tiny evidence line too large")
    job_log = args.root / "synthetic-job.log"
    job_log.write_text("\n".join("2026-11-06T00:00:00Z EVIDENCE658=" + json.dumps(record)
                                  for record in chunks) + "\n")
    runtime.run("collector-cli-fixture", [sys.executable,
                str(Path(__file__).with_name("collect.py")), str(job_log),
                str(args.root / "collected")], args.root, 5)
    require((args.root / "collected/runtime/example.log").read_bytes() == whole,
            "Actual collector CLI did not preserve bytes")
    report = args.root / "junit.xml"
    report.write_text('<testsuite><testcase classname="Fixture" name="pass"/>'
                      '<testcase classname="Fixture" name="fail"><failure/>'
                      '</testcase></testsuite>')
    ids = [("Fixture", "pass"), ("Fixture", "fail")]
    check_junit([report], ids, expected_failures=[("Fixture", "fail")])
    rejected(lambda: check_junit([report], ids), "Unexpected test failures")
    rejected(lambda: check_junit([report], ids + [("Fixture", "missing")]),
             "Test selection mismatch")
    rejected(lambda: check_junit([report], []), "Empty/duplicate expected test IDs")
    rejected(lambda: check_junit([report, report], ids), "Missing/duplicate actual test ID")

    for kind in ("zip", "tar"):
        archive = args.root / ("nested." + kind)
        if kind == "zip":
            with zipfile.ZipFile(archive, "x") as writer:
                writer.writestr("parent/", b"")
                writer.writestr("parent/child/data.csv", payload)
        else:
            with tarfile.open(archive, "w") as writer:
                directory = tarfile.TarInfo("parent/")
                directory.type = tarfile.DIRTYPE
                writer.addfile(directory)
                member = tarfile.TarInfo("parent/child/data.csv")
                member.size = len(payload)
                writer.addfile(member, io.BytesIO(payload))
        records = extract_archive(archive, args.root / (kind + "-out"), 1024)
        require(records == [{"path": "parent/child/data.csv", "bytes": len(payload),
                             "sha256": hashlib.sha256(payload).hexdigest()}],
                "Nested extraction/identity fixture failed")

    for index, name in enumerate(("/escape", "../escape", "a/../escape", "C:/escape")):
        archive = args.root / f"bad-path-{index}.zip"
        with zipfile.ZipFile(archive, "x") as writer:
            writer.writestr(name, b"x")
        expected = ("Unsafe archive path" if name.startswith("/") else
                    "Unsafe absolute/drive path" if ":" in name else
                    "Unsafe archive component")
        rejected(lambda: extract_archive(archive, args.root / f"bad-out-{index}", 1024),
                 expected)
    archive = args.root / "link.tar"
    with tarfile.open(archive, "w") as writer:
        member = tarfile.TarInfo("link")
        member.type, member.linkname = tarfile.SYMTYPE, "../escape"
        writer.addfile(member)
    rejected(lambda: extract_archive(archive, args.root / "link-out", 1024),
             "TAR special/link entry rejected")
    archive = args.root / "overflow.zip"
    with zipfile.ZipFile(archive, "x") as writer:
        writer.writestr("too-large", b"x" * 32)
    rejected(lambda: extract_archive(archive, args.root / "overflow-out", 31),
             "Archive expansion cap")

    runtime.run("expected-success", [sys.executable, "-c", "print('fixture')"],
                args.root, 5)
    runtime.run("expected-exit", [sys.executable, "-c", "raise SystemExit(3)"],
                args.root, 5, expected=(3,))
    rejected(lambda: runtime.run("wrong-exit", [sys.executable, "-c", "raise SystemExit(4)"],
                                 args.root, 5), "Unexpected exit 4")
    rejected(lambda: runtime.run("owned-timeout", [sys.executable, "-c",
                                                    "import time; time.sleep(30)"],
                                 args.root, 0.25), "Step deadline")
    rejected(lambda: runtime.run("bounded-output", [sys.executable, "-c",
                                                     "print('x' * 4096)"],
                                 args.root, 5, output_cap=1024), "Step output cap")
    child_record = args.root / "descendant.json"
    program = (
        "import json,os,pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':child.pid,'pgid':os.getpgid(child.pid)})); "
        "time.sleep(30)"
    )
    rejected(lambda: runtime.run("owned-descendant", [sys.executable, "-c", program,
                                                       str(child_record)], args.root, 1),
             "Step deadline")
    child = json.loads(child_record.read_text())
    require(child["pgid"] > 1 and child["pid"] > 1, "Missing actual child ownership")
    state_path = Path("/proc") / str(child["pid"]) / "stat"
    child_state = "ABSENT"
    until = time.monotonic() + 2
    while state_path.exists():
        # A killed orphan can briefly remain as a zombie awaiting PID1 reap.
        # That is terminal, not a running descendant; retain the observed state.
        try:
            child_state = state_path.read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            child_state = "ABSENT"
            break
        if child_state in ("Z", "X"):
            break
        require(time.monotonic() < until, "Owned descendant still running")
        time.sleep(0.05)
    if args.with_node:
        runtime.run("node-path", ["node", "-e",
                                  "if(Number(process.versions.node.split('.')[0])<24)process.exit(1);"
                                  "console.log(process.version)"], args.root, 5)
        runtime.run("npm-path", ["npm", "--version"], args.root, 10)
    for label in ("expected-success", "expected-exit", "wrong-exit",
                  "owned-timeout", "bounded-output"):
        record = json.loads((args.root / (label + ".json")).read_text())
        require(record["leader_terminal"], "Owned fixture leader still running")
    # Logic boundary controls supplement, never replace, the real original
    # baseline peak measurement required before candidate full validation.
    tracker = MemoryTracker(start=False)
    tracker.observe({"anon": 100, "kernel": 20, "file": 900, "peak": 1020}, 0.0)
    tracker.observe({"anon": 200, "kernel": 10, "file": 5000, "peak": 5210}, 0.1)
    state = tracker.snapshot()
    require(state["sampled_nonreclaimable_peak"] == 210 and state["samples"] == 2 and
            state["max_gap_seconds"] == 0.1, "Non-reclaimable tracker fixture failed")
    fake = {"sampled_nonreclaimable_peak": 1024**3, "limit": 8 * 1024**3,
            "events": {"oom": 0, "oom_kill": 0}}
    require(admit_from_baseline(fake, 3 * 1024**3)["required_bytes"] == 3 * 1024**3,
            "Memory equality boundary failed")
    rejected(lambda: admit_from_baseline(fake, 3 * 1024**3 - 1),
             "Full validation memory admission failed")
    (args.root / "D0-result.json").write_text(json.dumps({
        "status": "TINY_FIXTURES_PASS", "actual_initial_cgroup": memory,
        "actual_final_cgroup": cgroup_memory(),
        "project_tests_run": False, "full_memory_admission": False,
        "original_deadline_epoch": args.original_deadline_epoch,
        "owned_descendant": {**child, "observed_final_state": child_state},
        "node_image_path_exercised": args.with_node,
        "upstream_helper_sha256": hashlib.sha256(helper_path.read_bytes()).hexdigest(),
        "synthetic_helper_pair_test_items": pair_items,
        "actual_page_items": actual_page_items,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
