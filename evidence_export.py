"""Bounded priority evidence export for the unexecuted hosted controller."""

import base64
import gzip
import hashlib
import json
import time


CRITICAL_LOGS = {'docs-full-N-B10-plural.log', 'candidate-entire-causal-package.log', 'upstream-core-testPython-generated.log', 'docs-full-N-B10-singular.log', 'docs-exact-two-python-fences.log', 'native-full-VerifyDML.log', 'candidate-full-native-suite.log', 'upstream-core-testPython-handwritten.log', 'docs-full-fifteen-cell-notebook.log', 'docs-compile-and-run-two-scala-fences.log', 'candidate-root-fuzzing.log'}

def select_evidence(root, paths, cap=63 * 1024**2, tail_bytes=32768):
    """Keep structured proof first, then log tails, then affordable full logs.

    Reserve one MiB outside this payload budget for the omission manifest.
    Never silently turn a missing critical proof into a successful export.
    """
    selected, omitted, used = {}, [], 0
    regular = []
    for path in sorted(set(paths)):
        name = str(path.relative_to(root))
        if path.is_symlink() or not path.is_file():
            omitted.append({"path": name, "reason": "not-regular", "critical": True})
            continue
        regular.append(path)
    def priority(path):
        name = str(path.relative_to(root))
        if path.name == "host-result.json":
            return (0, name)
        if path.name in ("lane-result.json", "setup-result.json"):
            return (1, name)
        if "admission" in path.name:
            return (2, name)
        if path.suffix == ".xml":
            return (3, name)
        if any(part.startswith("d0-") for part in path.relative_to(root).parts):
            return (4, name)
        return (5, name)

    critical = sorted((path for path in regular if (path.suffix != ".log" or path.name in CRITICAL_LOGS)), key=priority)
    logs = [path for path in regular if path.suffix == ".log" and path.name not in CRITICAL_LOGS]
    for path in critical:
        size = path.stat().st_size
        if used + size <= cap:
            data = path.read_bytes()
            selected[str(path.relative_to(root))] = data
            used += len(data)
        else:
            omitted.append({"path": str(path.relative_to(root)), "bytes": size,
                            "reason": "structured-budget", "critical": True})
    for path in logs:
        size = path.stat().st_size
        count = min(size, tail_bytes)
        name = str(path.relative_to(root))
        if used + count <= cap:
            with path.open("rb") as source:
                source.seek(size - count)
                data = source.read(count)
            selected[name + ".tail"] = data
            used += len(data)
        else:
            omitted.append({"path": name + ".tail", "bytes": count,
                            "reason": "tail-budget", "critical": False})
    for path in logs:
        size = path.stat().st_size
        name = str(path.relative_to(root))
        if used + size <= cap:
            data = path.read_bytes()
            selected[name] = data
            used += len(data)
        else:
            omitted.append({"path": name, "bytes": size,
                            "reason": "full-log-budget", "critical": False})
    return selected, omitted


def attach_critical_diagnostics(root, selected, omissions, cap):
    from causal_diagnostics import partial_log_diagnostic
    seen=set()
    index=0
    while index<len(omissions):
        item=omissions[index];index+=1
        name=item['path']
        if not (item['critical'] and name.endswith('.log') and name not in seen):
            continue
        seen.add(name)
        source=root/name
        if source.is_symlink() or not source.is_file():
            continue
        diagnostic_name=name+'.partial-diagnostic.json'
        if diagnostic_name in selected:
            continue
        data=json.dumps(partial_log_diagnostic(source,name),sort_keys=True).encode()
        # Diagnostics consume the SAME payload envelope. Reverse removals keep
        # established priorities; all displaced critical proof stays a failure.
        while selected and sum(len(value)for value in selected.values())+len(data)>cap:
            removed=next(reversed(selected));value=selected.pop(removed)
            critical=not(removed.endswith('.tail')or(removed.endswith('.log')and
                         removed.rsplit('/',1)[-1]not in CRITICAL_LOGS))
            omissions.append({'path':removed,'bytes':len(value),'critical':critical,
                              'reason':'critical-diagnostic-budget'})
        if len(data)>cap:
            omissions.append({'path':diagnostic_name,'bytes':len(data),'critical':True,
                              'reason':'critical-diagnostic-budget'})
        else:
            selected[diagnostic_name]=data
    return selected,omissions


ENCODED_CAP = 58 * 1024**2


def encoded_lines(name, data):
    """Exactly the bytes that print emits, including marker and newline."""
    parts = max(1, (len(data) + 8191) // 8192)
    file_sha = hashlib.sha256(data).hexdigest()
    lines = []
    for index in range(parts):
        piece = data[index * 8192:(index + 1) * 8192]
        record = {"path": name, "bytes": len(piece),
                  "sha256": hashlib.sha256(piece).hexdigest(), "encoding": "gzip-base64",
                  "index": index, "parts": parts, "file_bytes": len(data),
                  "file_sha256": file_sha,
                  "base64": base64.b64encode(gzip.compress(piece, mtime=0)).decode("ascii")}
        encoded = json.dumps(record)
        if len(encoded.encode()) >= 14000:
            raise RuntimeError("Evidence log line cap")
        lines.append("EVIDENCE1669=" + encoded)
    return lines


def line_bytes(lines):
    return sum(len(line.encode("utf-8")) + 1 for line in lines)


def export_evidence(root, paths, absolute, emit=print, cap=63 * 1024**2,
                    tail_bytes=32768, terminal_lines=None, encoded_cap=ENCODED_CAP):
    if not 0 < encoded_cap <= ENCODED_CAP:
        raise RuntimeError("Encoded cap cannot exceed 58 MiB")
    # Cache the exact compression results; omission never recompresses payloads.
    selected, omissions = select_evidence(root, paths, cap, tail_bytes)
    selected, omissions = attach_critical_diagnostics(root, selected, omissions, cap)
    encoded = {name: encoded_lines(name, data) for name, data in selected.items()}
    terminal_lines = terminal_lines or (lambda result: [])
    while True:
        inventory = {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                     for name, data in selected.items()}
        summary = {"selected": inventory, "omissions": omissions,
                   "critical_complete": not any(item["critical"] for item in omissions)}
        manifest = json.dumps(summary, sort_keys=True).encode()
        if len(manifest) > 1024**2:
            raise RuntimeError("Evidence inventory exceeds reserved one MiB")
        first = encoded_lines("evidence-export.json", manifest)
        result = {"complete": True, "sent": ["evidence-export.json", *selected],
                  "critical_complete": summary["critical_complete"], "omissions": omissions}
        # Deadline failure can occur after any prefix. Its longest terminal
        # representation has the full sent list; reserve that variant too.
        partial = {**result, "complete": False, "critical_complete": False,
                   "reason": "absolute-deadline"}
        terminal_size = max(line_bytes(terminal_lines(result)),
                            line_bytes(terminal_lines(partial)))
        total = line_bytes(first) + sum(line_bytes(encoded[n]) for n in selected) + terminal_size
        if total <= encoded_cap:
            break
        if not selected:
            raise RuntimeError("Terminal/inventory lines exceed encoded evidence budget")
        # Selection insertion order is structured priority, tails, then full
        # noncritical logs. Reverse removal preserves those existing priorities.
        name = next(reversed(selected))
        data = selected.pop(name)
        encoded.pop(name)
        omissions.append({"path": name, "bytes": len(data), "reason": "encoded-line-budget",
                          "critical": not (name.endswith(".tail") or
                              (name.endswith(".log") and name.rsplit("/", 1)[-1] not in CRITICAL_LOGS))})
        if name.endswith('.log') and name.rsplit('/',1)[-1] in CRITICAL_LOGS:
            selected, omissions = attach_critical_diagnostics(root, selected, omissions, cap)
            encoded = {key: encoded_lines(key, value) for key, value in selected.items()}
    # No evidence is emitted until the entire encoded plan and terminal fit.
    sent = []
    for name, lines in [("evidence-export.json", first), *encoded.items()]:
        for line in lines:
            if time.time() >= absolute:
                return {**partial, "sent": sent}
            emit(line, flush=True)
        sent.append(name)
    return result
