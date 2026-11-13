import json
from pathlib import Path
from validation_runtime import require

def azure_black(lane, role, exclude_docs=False):
    """Run the whole-repository Azure gate, retaining pre-existing failures."""
    label = role + ("-shipping-black" if exclude_docs else "-azure-black")
    extra = ["--extend-exclude", "docs/"] if exclude_docs else []
    output = lane.command(label, ["python", "-m", "black", "--check", *extra, "."],
                          180, expected=(0, 1))
    code = lane.records[-1]["exit"]
    failures = []
    for line in output.splitlines():
        if line.startswith("would reformat "):
            path = Path(line[len("would reformat "):])
            path = (path if path.is_absolute() else lane.source / path).resolve()
            require(path.is_relative_to(lane.source.resolve()), "Black path outside source")
            failures.append(str(path.relative_to(lane.source.resolve())))
    require((code == 0 and not failures) or (code == 1 and failures),
            "Black exit is not a verified formatting-only result")
    require("error:" not in output.lower() and "cannot parse" not in output.lower(),
            "Black process/parse error is not a baseline formatting exception")
    result = {"role": role, "exit": code, "failures": sorted(set(failures))}
    (lane.root / (label + ".failures.json")).write_text(json.dumps(result, indent=2))
    if role == "candidate":
        baseline = json.loads((lane.root / (("baseline-shipping-black" if exclude_docs else "baseline-azure-black") + ".failures.json")).read_text())
        require(set(failures) <= set(baseline["failures"]), "New whole-repository Black failure")
        changed = {name for name in lane.manifest["candidate_files"] if name.endswith(".py")}
        require(not changed.intersection(failures), "Touched Python file fails Azure Black")
    return result
