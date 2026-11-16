"""Partial forensic evidence, never a replacement for full causal validation."""
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot_reports(source, destination, ledger, operation_returned, error):
    result = {"state": "PARTIAL_FRAMEWORK_REPORT_SNAPSHOT_ONLY", "snapshotComplete": False,
              "operationReturned": operation_returned, "operationError": error,
              "frameworkTotalsReconciled": False, "fullValidationPassed": False,
              "remainingCaseIDs": "UNKNOWN", "files": [], "errors": []}
    used = 0
    try:
        destination.mkdir()
        paths = sorted(source.glob("*.xml"))
        if len(paths) > 4096:
            raise RuntimeError("Report snapshot count exceeds existing nested-proof bound")
        for path in paths:
            row = {"path": path.name, "copied": False}
            result["files"].append(row)
            try:
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError("Nonregular framework report")
                # Same 16 MiB nested-proof envelope; no increased export cap.
                with path.open("rb") as stream:
                    data = stream.read(16 * 1024**2 - used + 1)
                if len(data) + used > 16 * 1024**2:
                    raise RuntimeError("Report snapshot exceeds existing nested-proof bound")
                target = destination / path.name
                with target.open("xb") as output:
                    output.write(data)
                used += len(data)
                row.update(copied=True, bytes=len(data), sha256=sha(data))
                try:
                    document = ET.fromstring(data)
                    row.update(suite=document.get("name"), declaredTotals={key: document.get(key)
                               for key in ["tests", "failures", "errors", "skipped"]},
                               caseIDs=[case.get("name") for case in document.iter("testcase")],
                               failureElements=len(list(document.iter("failure"))),
                               errorElements=len(list(document.iter("error"))),
                               skippedElements=len(list(document.iter("skipped"))))
                except (ET.ParseError, ValueError) as parse_error:
                    row["parseError"] = str(parse_error)
            except (OSError, RuntimeError) as copy_error:
                row["copyError"] = str(copy_error)
        result["snapshotComplete"] = all(row["copied"] for row in result["files"])
        result["sourceReportCount"] = len(paths)
    except (OSError, RuntimeError) as snapshot_error:
        result["errors"].append(str(snapshot_error))
    result["copiedBytes"] = used
    temporary = ledger.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2))
    temporary.replace(ledger)
    return result


def run_with_causal_snapshot(lane, reports, retained, seconds, selector):
    output = None
    error = None
    try:
        output = lane.sbt("candidate-entire-causal-package",
                          [selector],
                          seconds, expected=(0, 1))
        return output
    except BaseException as failure:
        error = f"{type(failure).__name__}: {failure}"
        raise
    finally:
        # Snapshot/ledger errors may fail a normal return, but never replace
        # the original timeout/cap/cancellation exception already propagating.
        try:
            result = snapshot_reports(reports, retained,
                                      lane.root / "candidate-causal-partial-reports.json",
                                      output is not None, error)
            if not result["snapshotComplete"] and error is None:
                raise RuntimeError("Incomplete causal framework report snapshot")
        except BaseException:
            if error is None:
                raise


def partial_log_diagnostic(path, name, excerpt_bytes=32768):
    """Streaming hash/count/histogram plus bounded excerpts; no log filtering."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Nonregular critical log diagnostic source")
    if not 0 < excerpt_bytes <= 32768 or path.stat().st_size > 64 * 1024**2:
        raise RuntimeError("Critical diagnostic exceeds existing raw step bound")
    digest = hashlib.sha256()
    head, tail, prefix = bytearray(), bytearray(), bytearray()
    total = line_count = line_bytes = longest = oversized_lines = 0
    histogram = {key: 0 for key in ["SPARK_INFO", "SPARK_WARN", "SPARK_ERROR",
                 "SPARK_DEBUG", "SPARK_TRACE", "SBT_INFO", "SBT_WARN", "SBT_ERROR",
                 "STACK_OR_CAUSE", "EMPTY", "OTHER"]}

    def finish_line():
        nonlocal line_count, line_bytes, longest, oversized_lines
        line_count += 1
        longest = max(longest, line_bytes)
        oversized_lines += int(line_bytes > 512)
        value = bytes(prefix).decode("utf-8", errors="replace").strip()
        spark = re.match(r"^(?:\[info\] )?\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} "
                         r"(INFO|WARN|ERROR|DEBUG|TRACE)\b", value)
        sbt = re.match(r"^\[(info|warn|error)\]", value)
        if spark:
            kind = "SPARK_" + spark.group(1)
        elif sbt:
            kind = "SBT_" + sbt.group(1).upper()
        elif value.startswith(("at ", "Caused by:", "Suppressed:")):
            kind = "STACK_OR_CAUSE"
        else:
            kind = "OTHER" if value else "EMPTY"
        histogram[kind] += 1
        line_bytes = 0
        prefix.clear()

    with path.open("rb") as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
            head.extend(chunk[:max(0, excerpt_bytes - len(head))])
            tail.extend(chunk)
            if len(tail) > excerpt_bytes:
                del tail[:-excerpt_bytes]
            pieces = chunk.split(b"\n")
            for index, piece in enumerate(pieces):
                line_bytes += len(piece)
                prefix.extend(piece[:max(0, 512 - len(prefix))])
                if index < len(pieces) - 1:
                    finish_line()
    if line_bytes:
        finish_line()

    def excerpt(data):
        return {"bytes": len(data), "sha256": sha(data),
                "textUTF8Replacement": bytes(data).decode("utf-8", errors="replace")}
    return {"state": "INCOMPLETE_CRITICAL_LOG_DIAGNOSTIC_ONLY", "path": name,
            "complete": False, "fullCriticalLogOmitted": True, "rawBytes": total,
            "rawSHA256": digest.hexdigest(), "lineCount": line_count,
            "lineDefinition": "LF-delimited plus one final unterminated line",
            "lineClassHistogram": histogram, "classificationPrefixBytes": 512,
            "linesLongerThanClassificationPrefix": oversized_lines,
            "longestLogicalLineBytes": longest, "head": excerpt(head), "tail": excerpt(tail),
            "noMessagesFilteredDuringTest": True,
            "doesNotEstablishCaseCompletionOrFullFailureClassification": True}
