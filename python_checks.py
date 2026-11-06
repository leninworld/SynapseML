"""Unexecuted full affected generated-Python and changed-example checks."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tarfile
import xml.etree.ElementTree as ET

from validation_runtime import require


SECTION = "#### Inspect the selected training parameters"


def azure_black(lane, role):
    """Run the whole-repository Azure gate, retaining pre-existing failures."""
    label = role + "-azure-black"
    output = lane.command(label, ["python", "-m", "black", "--check", "."],
                          180, expected=(0, 1))
    code = lane.records[-1]["exit"]
    failures = []
    for line in output.splitlines():
        if line.startswith("would reformat "):
            path = Path(line[len("would reformat "):]).resolve()
            require(path.is_relative_to(lane.source.resolve()), "Black path outside source")
            failures.append(str(path.relative_to(lane.source.resolve())))
    require((code == 0 and not failures) or (code == 1 and failures),
            "Black exit is not a verified formatting-only result")
    require("error:" not in output.lower() and "cannot parse" not in output.lower(),
            "Black process/parse error is not a baseline formatting exception")
    result = {"role": role, "exit": code, "failures": sorted(set(failures))}
    (lane.root / (label + ".failures.json")).write_text(json.dumps(result, indent=2))
    if role == "candidate":
        baseline = json.loads((lane.root / "baseline-azure-black.failures.json").read_text())
        require(set(failures) <= set(baseline["failures"]), "New whole-repository Black failure")
        changed = {name for name in lane.manifest["candidate_files"] if name.endswith(".py")}
        require(not changed.intersection(failures), "Touched Python file fails Azure Black")
    return result


def changed_example(markdown):
    require(markdown.count(SECTION) == 1, "Missing/duplicate changed example section")
    section = markdown.split(SECTION, 1)[1]
    blocks = re.findall(r"```python\n(.*?)\n```", section, re.S)
    require(len(blocks) == 1 and 'assert "numLeaves: 7"' in blocks[0],
            "Changed Python example selection mismatch")
    return blocks[0]


def run_pytest(lane, label, paths, count, artifact_manifest, extra=()):
    os.environ["MODEL_INFO_ARTIFACT_MANIFEST"] = str(artifact_manifest)
    provenance = lane.root / (label + "-provenance.json")
    os.environ["MODEL_INFO_PROVENANCE_RESULT"] = str(provenance)
    os.environ["PYTHONPATH"] = str(lane.packet)
    report = lane.root / (label + ".xml")
    options = ["--import-mode=importlib", "-p", "provenance_pytest", *extra]
    output = lane.command(label + "-collect", ["python", "-m", "pytest", "--collect-only",
                                               "-q", *options, *map(str, paths)], 180)
    nodes = [line.strip() for line in output.splitlines() if "::" in line and
             any(path.name in line.split("::", 1)[0] for path in paths)]
    require(len(nodes) == count and len(set(nodes)) == count, "Unexpected Python collection")
    # Collection itself ran the real provenance plugin and stopped that JVM.
    require(provenance.is_file(), "Collection omitted installed/JVM provenance")
    os.environ["MODEL_INFO_PROVENANCE_RESULT"] = str(lane.root / (label + "-run-provenance.json"))
    output = lane.command(label, ["python", "-m", "pytest", "-q", "-s",
                                  "--junitxml=" + str(report), *options, *map(str, paths)], 600)
    root = ET.parse(report).getroot()
    cases = list(root.iter("testcase"))
    require(len(cases) == count and not list(root.iter("failure")) and
            not list(root.iter("error")) and not list(root.iter("skipped")),
            "Generated Python check failed or skipped")
    require(sorted(case.get("name") for case in cases) ==
            sorted(node.rsplit("::", 1)[1] for node in nodes), "Python executed test IDs changed")
    (lane.root / (label + "-nodes.json")).write_text(json.dumps(nodes, indent=2))
    return output


def affected_python_checks(lane, artifact_manifest):
    azure_black(lane, "candidate")
    for label, path in (
        ("generated-core-python", "core/src/test/python/synapsemltest/automl/test_model_info.py"),
        ("generated-native-python", "lightgbm/src/test/python/synapsemltest/lightgbm/test_tuning_model_info.py"),
    ):
        run_pytest(lane, label, [lane.source / path], 1, artifact_manifest)
    lane.command("black-diff", ["python", "-m", "black", "--diff", "--color",
                                 "--extend-exclude", "docs/", "."], 180)
    lane.command("black-check", ["python", "-m", "black", "--check", "-q",
                                  "--extend-exclude", "docs/", "."], 180)
    doc = lane.source / "docs/Quick Examples/estimators/core/_AutoML.md"
    code = changed_example(doc.read_text())
    folder = lane.root / "website-example"
    folder.mkdir()
    sample = folder / "_AutoML.md"
    markdown = doc.read_text()
    require(markdown.count(code) == 1, "Ambiguous final example witness location")
    require(len(re.findall(r"```python\n", markdown)) == 4,
            "Complete AutoML page no longer has the four reviewed examples")
    # Preserve the COMPLETE sequential page, appending only the final witness.
    sample.write_text(markdown.replace(code, code +
                      '\nprint("NEW_MODEL_INFO_BLOCK_1850_EXECUTED")', 1))
    spec = importlib.util.spec_from_file_location("upstream_website_doctest",
                                               lane.source / "website/doctest.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.add_python_helper_to_markdown(str(folder), sample.name, "0.0.2-i1850-SNAPSHOT")
    d0 = json.loads((lane.root / "d0-validate/D0-result.json").read_text())
    expected = 4 * d0["synthetic_helper_pair_test_items"]
    output = run_pytest(lane, "complete-website-page", [sample], expected, artifact_manifest,
                        extra=("--codeblocks",))
    require("NEW_MODEL_INFO_BLOCK_1850_EXECUTED" in output, "Changed example witness missing")
    (lane.root / "changed-example-identity.json").write_text(json.dumps({
        "source": str(doc), "section": SECTION,
        "exact_added_code_sha256": hashlib.sha256(code.encode()).hexdigest(),
        "transformed_sample_sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
        "scope": "Complete changed AutoML page: all four Python examples through the upstream helper",
    }, indent=2))
    roots = list(lane.source.glob("core/target/scala-*/generated/src/python"))
    require(len(roots) == 1, "Ambiguous generated core package source")
    shipping = lane.root / "shipping"
    shipping.mkdir()
    step = lane.runtime.run("core-shipping-sdist", ["python", "setup.py", "sdist",
                            "--dist-dir", str(shipping)], roots[0], 180)
    lane.records.append({"label": "core-shipping-sdist", **step})
    archives = list(shipping.glob("*.tar.gz"))
    require(len(archives) == 1, "Missing/ambiguous shipping source distribution")
    with tarfile.open(archives[0], "r:gz") as archive:
        members = [member for member in archive.getmembers()
                   if member.name.endswith("/synapse/ml/automl/_TuneHyperparametersModel.py")]
        require(len(members) == 1 and members[0].isfile() and members[0].size < 8 * 1024**2,
                "Shipping internal wrapper missing or oversized")
        with archive.extractfile(members[0]) as source:
            code = source.read(8 * 1024**2)
        require(b"def getBestModelParamsInfo" in code, "Shipping sdist lacks generated getter")
    (lane.root / "shipping-artifacts.json").write_text(json.dumps({
        "path": str(archives[0]), "bytes": archives[0].stat().st_size,
        "sha256": hashlib.sha256(archives[0].read_bytes()).hexdigest(),
    }, indent=2))
