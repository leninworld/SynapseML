"""UNRUN Docker child: full-N/P10 B10 diagnostic copy, fresh process per route."""
import ast
import json
import os
from pathlib import Path
import sys
import time
from docs_contract import BOOK, book, runtime_book, manifest, verify_runtime, require, sha


def draw_copy(code, original_b):
    needle = '.setMaxIter(' + str(original_b) + ')'
    require(code.count(needle) == 1, 'EXACT_ONE_DRAW_PARAMETER')
    changed = code.replace(needle, '.setMaxIter(10)')
    left, right = ast.parse(code), ast.parse(changed)
    found = 0
    for node in ast.walk(left):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'setMaxIter':
            require(len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and
                    node.args[0].value == original_b, 'UNEXPECTED_DRAW_CALL')
            node.args[0].value = 10
            found += 1
    require(found == 1 and ast.dump(left) == ast.dump(right), 'ONLY_B_CHANGED')
    return changed


def run(config, source, out, route):
    process_started = time.monotonic()
    from validation_runtime import docker_only, cgroup_memory, MemoryTracker
    docker_only()
    require(config['executionApproved'] is True and route in ('singular', 'plural'), 'CALIBRATION_NOT_APPROVED')
    raw = (source / BOOK).read_bytes()
    require(sha(raw) == config['notebookSHA256'], 'CALIBRATION_SOURCE_CHANGED')
    staged = Path(config['stagedCSV'])
    require(not any(p.is_symlink() for p in (staged, *staged.parents)), 'DATA_SYMLINK')
    require(staged.stat().st_size == config['csvBytes'] and sha(staged.read_bytes()) == config['csvSHA256'],
            'CALIBRATION_DATA_CHANGED')
    value = runtime_book(raw, staged.resolve().as_uri())
    info = manifest(config['artifactManifest'])
    os.environ['PYSPARK_SUBMIT_ARGS'] = ('--master local[3] --driver-memory 4g --jars ' +
        ','.join(row['path'] for row in info['jars']) + ' pyspark-shell')
    from pyspark import SparkContext
    from pyspark.sql import SparkSession
    require(SparkContext._active_spark_context is None, 'FRESH_CALIBRATION_PROCESS_REQUIRED')
    spark = SparkSession.builder.master('local[3]').appName('DML full-N ' + route + ' calibration').getOrCreate()
    tracker = None
    result = {'complete': False, 'route': route, 'parallelism': 10, 'requestedDraws': 10,
              'diagnosticOnly': True, 'driverGiB': 4, 'sourceNotebookSHA256': sha(raw)}
    started = time.monotonic()
    try:
        result['provenance'] = verify_runtime(spark, info)
        namespace = {'spark': spark, '__name__': '__main__'}
        exec(compile(''.join(value['cells'][5]['source']), BOOK + ':staged-input', 'exec'), namespace)
        n = namespace['data'].count()
        require(n == 2000, 'FULL_STAGED_N_NOT_2000')
        index, count = (7, 20) if route == 'singular' else (11, 30)
        original = ''.join(value['cells'][index]['source'])
        diagnostic = draw_copy(original, count)
        tree = ast.parse(diagnostic)
        require(isinstance(tree.body[-1], ast.Assign) and isinstance(tree.body[-1].value, ast.Call) and
                isinstance(tree.body[-1].value.func, ast.Attribute) and
                tree.body[-1].value.func.attr == 'fit', 'EXPECTED_FINAL_PUBLIC_FIT')
        exec(compile(ast.Module(body=tree.body[:-1], type_ignores=[]), BOOK + ':B10-prefix', 'exec'), namespace)
        if route == 'plural':
            require(namespace['synthetic'].count() == 2000, 'FULL_SYNTHETIC_N')
        # Baseline is measured after full-N input actions and immediately before
        # this route's fit. This process has no previous fitted Spark model.
        baseline_samples = []
        for _ in range(3):
            m = cgroup_memory()
            baseline_samples.append(m['anon'] + m['kernel'])
            time.sleep(.1)
        baseline = max(baseline_samples)
        trace = out / ('calibration-' + route + '-window.jsonl')
        tracker = MemoryTracker(trace)
        fit_started = time.monotonic()
        exec(compile(ast.Module(body=tree.body[-1:], type_ignores=[]),
                     BOOK + ':B10-' + route, 'exec'), namespace)
        fit_seconds = time.monotonic() - fit_started
        model = namespace['model' if route == 'singular' else 'plural_model']
        estimator = namespace['dml' if route == 'singular' else 'plural_dml']
        require(estimator.getParallelism() == 10, 'DEFAULT_P10_CHANGED')
        rows = (list(model._java_obj.getRawTreatmentEffects()) if route == 'singular'
                else model.getRawTreatmentEffectRows())
        require(len(rows) == 10, 'CALIBRATION_NOT_TEN_SUCCESSFUL_DRAWS')
        # Retained level is observed BEFORE model/context are stopped.
        end_memory = cgroup_memory()
        tracker.stop()
        sample = tracker.snapshot()
        require(sample['error'] is None and sample['samples'] >= 2 and
                sample['max_gap_seconds'] <= 1 and
                time.monotonic() - sample['last_monotonic'] <= 1, 'CALIBRATION_TRACE_INSUFFICIENT')
        end_level = end_memory['anon'] + end_memory['kernel']
        result.update(complete=True, rows=n, originalDrawCount=count,
                      originalCodeSHA256=sha(original.encode()), diagnosticCodeSHA256=sha(diagnostic.encode()),
                      onlyMaxIterChanged=True, fitSeconds=fit_seconds,
                      postSparkReadyDataAndBaselineSeconds=fit_started - started,
                      baselineBytes=baseline, baselineSamples=baseline_samples,
                      observedPeakBytes=sample['sampled_nonreclaimable_peak'],
                      retainedEndBytes=end_level,
                      activeIncrementBytes=max(0, sample['sampled_nonreclaimable_peak'] - baseline),
                      retainedGrowthBytes=max(0, end_level - baseline),
                      window=sample, windowSHA256=sha(trace.read_bytes()), cgroup=end_memory)
    finally:
        if tracker is not None:
            tracker.stop()
        spark.stop()
        result['sparkStopCalled'] = True
        result['childWallSeconds'] = time.monotonic() - process_started
        (out / ('calibration-' + route + '.json')).write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    require(len(sys.argv) == 5, 'CALIBRATION_ARGUMENTS')
    run(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
