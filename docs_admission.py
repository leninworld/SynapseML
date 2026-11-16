"""UNRUN incremental docs admission; no source/model imports and no global peak reuse."""
import json
import math
from pathlib import Path
import time
from docs_contract import require
GIB = 1024**3


def predicate(baseline, increments, retained, available, limit, remaining, work):
    require(all(isinstance(x, (int, float)) and math.isfinite(x) and x >= 0 for x in
                [baseline, *increments, *retained, available, limit, remaining, work]), 'BAD_ADMISSION_VALUES')
    needed = baseline + 1.5 * (sum(increments) + sum(retained)) + GIB
    bound = min(limit * .9, available)
    return {'freshOrMeasuredBaselineBytes': baseline, 'activeIncrementsBytes': increments,
            'retainedGrowthTermsBytes': retained, 'forecastBytes': needed,
            'effectiveAnonymousKernelBoundBytes': bound,
            'remainingSeconds': remaining, 'remainingWorkIncludingReservesSeconds': work,
            'admitted': needed <= bound and remaining > work,
            'qualifiedForecastNotMeasurementGuarantee': True}


def calibration(path):
    r = json.loads(Path(path).read_text())
    require(r['complete'] is True and r['parallelism'] == 10 and r['requestedDraws'] == 10 and
            r['rows'] == 2000 and r['sparkStopCalled'] is True, 'CALIBRATION_INCOMPLETE')
    require(math.isfinite(r['fitSeconds']) and r['fitSeconds'] > 0 and
            len(r['baselineSamples']) == 3 and all(math.isfinite(x) and x > 0
            for x in r['baselineSamples']), 'CALIBRATION_NONFINITE_OR_MISSING_BASELINE')
    w = r['window']
    require(w['error'] is None and w['samples'] >= 2 and w['max_gap_seconds'] <= 1,
            'CALIBRATION_WINDOW_INVALID')
    require(r['activeIncrementBytes'] == max(0, r['observedPeakBytes'] - r['baselineBytes']) and
            r['retainedGrowthBytes'] == max(0, r['retainedEndBytes'] - r['baselineBytes']),
            'CALIBRATION_INCREMENT_MISMATCH')
    require(not any(r['cgroup']['events'].get(k, 0) for k in ('oom', 'oom_kill')), 'CALIBRATION_OOM')
    return r


def notebook_gate(config, out, phase, first_baseline=None):
    from validation_runtime import cgroup_memory
    singular = calibration(config['singularCalibration'])
    plural = calibration(config['pluralCalibration'])
    memory = cgroup_memory()
    capacity = json.loads(Path('/capacity/host.json').read_text())
    require(0 <= time.time() - capacity['at'] < 5, 'STALE_HOST_CAPACITY')
    current = memory['anon'] + memory['kernel']
    b = current if first_baseline is None else first_baseline
    if phase == 'before-singular':
        # Sequential fits: each stage has ONE active fit, never both active peaks.
        work = 1.5 * (2 * singular['fitSeconds'] + 3 * plural['fitSeconds']) + 120 + 240
        stage_terms = [
            ('singular', [singular['activeIncrementBytes']],
             [2 * singular['retainedGrowthBytes']]),
            ('plural', [plural['activeIncrementBytes']],
             [2 * singular['retainedGrowthBytes'], 3 * plural['retainedGrowthBytes']])]
    else:
        require(phase == 'before-plural' and first_baseline is not None, 'UNKNOWN_NOTEBOOK_PHASE')
        increments = [plural['activeIncrementBytes']]
        retained = [max(0, current - first_baseline), 3 * plural['retainedGrowthBytes']]
        work = 1.5 * 3 * plural['fitSeconds'] + 120 + 240
    available = max(0, capacity['available'] - 2 * GIB)
    remaining = config['originalDeadlineEpoch'] - time.time()
    if phase == 'before-singular':
        stages = {name: predicate(b, active, growth, available, memory['limit'], remaining, work)
                  for name, active, growth in stage_terms}
        r = dict(max(stages.values(), key=lambda value: value['forecastBytes']))
        r['stageForecasts'] = stages
        r['forecastRule'] = 'MAX_SEQUENTIAL_SINGULAR_AND_PLURAL_STAGE_FORECASTS'
        r['admitted'] = all(stage['admitted'] for stage in stages.values())
    else:
        r = predicate(b, increments, retained, available, memory['limit'], remaining, work)
        r['forecastRule'] = 'ACTUAL_RETAINED_AFTER_SINGULAR_PLUS_ONLY_PLURAL_ACTIVE_INCREMENT'
    r.update(phase=phase, actualCgroup=memory, actualHostCapacity=capacity,
             measuredCurrentAnonKernelBytes=current, initialNotebookBaselineBytes=b)
    (out / ('notebook-' + phase + '-admission.json')).write_text(json.dumps(r, indent=2))
    require(r['admitted'], 'FULL_NOTEBOOK_REMAINING_TIME_OR_INCREMENTAL_MEMORY_HOLD')
    return b


def fixture():
    require(predicate(GIB, [GIB], [0], 11 * GIB, 12 * GIB, 2000, 1000)['admitted'], 'GATE_POSITIVE')
    require(not predicate(GIB, [8 * GIB], [], 11 * GIB, 12 * GIB, 2000, 1000)['admitted'],
            'MEMORY_NEGATIVE')
    require(not predicate(GIB, [], [], 11 * GIB, 12 * GIB, 100, 100)['admitted'], 'TIME_NEGATIVE')
    return {'positive': 1, 'negative': 2, 'projectFits': 0}
