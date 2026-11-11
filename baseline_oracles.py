"""Classify actual baseline evidence; no candidate success is inferred."""
import json
from pathlib import Path

from validation_runtime import require


def inspect(proof):
    require(proof['kind'] == 'unmodified-master-baseline-only', 'Wrong evidence role')
    require(proof['source'] == '36dfb77160ec99ea2bf43c7fae5decaf63ee7396', 'Wrong baseline source')
    rows = proof['modes']
    names = [row['name'] for row in rows]
    require(len(names) == len(set(names)), 'Duplicate mode observations')
    by_name = {row['name']: row for row in rows}
    for name in ('unlinked', 'linked-space', 'linked-equals', 'oaa-zero', 'oaa-one',
                 'oaa-unspecified', 'scores-zero', 'scores-one', 'scores-unspecified',
                 'probabilities-zero', 'probabilities-one', 'probabilities-unspecified',
                 'csoaa-native-model', 'ect-native-model'):
        require(name in by_name and by_name[name]['status'] == 'observed',
                'Missing real baseline mode: ' + name)
        for suffix in ('-custom', '-no-raw', '-no-probability', '-no-prediction', '-reloaded'):
            require(name + suffix in by_name, 'Missing column/persistence observation')
        original, restored = by_name[name], by_name[name + '-reloaded']
        require(restored['status'] == 'observed' and restored['rows'] == original['rows'],
                'Baseline persistence differs: ' + name)
    unlinked = by_name['unlinked']
    declared = {f['name']: f for f in unlinked['declaredSchema']['fields']}
    actual = {f['name']: f for f in unlinked['actualSchema']['fields']}
    require(actual['rawPrediction']['type'] == 'double', 'Expected scalar baseline not reproduced')
    require('VectorUDT' in declared['rawPrediction']['type']['class'],
            'Expected declared Vector schema not reproduced')
    require('vowpalWabbitPredictionCol' in actual, 'Native intermediate column not recorded')
    for name in ('scores-zero', 'probabilities-zero'):
        fields = {f['name']: f for f in by_name[name]['actualSchema']['fields']}
        require(fields['rawPrediction']['type']['type'] == 'array',
                'Expected multiclass array baseline not reproduced')
    native = proof['native']
    require(len(native) == 18, 'Incomplete native score/indexing matrix')
    finite = [row for row in native if 'nan' not in row['input']]
    require(len(finite) == 12 and all(row['status'] == 'observed' for row in finite),
            'Native finite/tie modes were not actually exercised')
    # Mismatch is retained for the design decision; it is never relabelled as
    # proof that a score-based replacement preserves the winner.
    return {'baseline_schema_gap_reproduced': True, 'modes_recorded': len(rows),
            'native_observations': len(native),
            'native_strict_mismatches': [r for r in finite if not r['strictMatches']],
            'native_spark_argmax_mismatches': [r for r in finite if not r['sparkMatches']],
            'nonfinite_results': [r for r in native if 'nan' in r['input']],
            'disabled_column_errors': [r for r in rows if r['status'] == 'error'],
            'candidate_validated': False}


def fixture():
    # Exercise the exact evidence classifier's role/selection failures before
    # compiling any product code. Real evidence still comes from the JNI run.
    def refused(proof, expected):
        try:
            inspect(proof)
        except RuntimeError as error:
            require(str(error) == expected, 'Wrong evidence rejection')
        else:
            raise RuntimeError('Malformed evidence accepted')
    refused({'kind': 'candidate'}, 'Wrong evidence role')
    refused({'kind': 'unmodified-master-baseline-only', 'source': 'wrong'}, 'Wrong baseline source')
    refused({'kind': 'unmodified-master-baseline-only',
             'source': '36dfb77160ec99ea2bf43c7fae5decaf63ee7396',
             'modes': [{'name': 'duplicate'}, {'name': 'duplicate'}]}, 'Duplicate mode observations')
    names = ['unlinked', 'linked-space', 'linked-equals', 'oaa-zero', 'oaa-one',
             'oaa-unspecified', 'scores-zero', 'scores-one', 'scores-unspecified',
             'probabilities-zero', 'probabilities-one', 'probabilities-unspecified',
             'csoaa-native-model', 'ect-native-model']
    vector = {'type': 'udt', 'class': 'org.apache.spark.ml.linalg.VectorUDT'}
    declared = {'fields': [{'name': 'rawPrediction', 'type': vector}]}
    scalar = {'fields': [{'name': 'rawPrediction', 'type': 'double'},
                         {'name': 'vowpalWabbitPredictionCol', 'type': 'struct'}]}
    array = {'fields': [{'name': 'rawPrediction', 'type': {'type': 'array'}}]}
    modes = []
    for name in names:
        for suffix in ('', '-custom', '-no-raw', '-no-probability', '-no-prediction', '-reloaded'):
            modes.append({'name': name + suffix, 'status': 'observed', 'rows': ['synthetic-row'],
                          'declaredSchema': declared,
                          'actualSchema': array if name.startswith(('scores-', 'probabilities-')) else scalar})
    native = [{'input': value, 'status': 'observed', 'strictMatches': True, 'sparkMatches': True}
              for _ in range(6) for value in ('|f x:1', '|f x:0', '|f x:nan')]
    positive = {'kind': 'unmodified-master-baseline-only',
                'source': '36dfb77160ec99ea2bf43c7fae5decaf63ee7396', 'modes': modes, 'native': native}
    # Actual stdlib JSON encoding/decoding is the relevant dependency here.
    positive = json.loads(json.dumps(positive))
    result = inspect(positive)
    require(result['modes_recorded'] == 84 and not result['candidate_validated'],
            'Evidence success fixture failed')
    positive['modes'][-1]['rows'] = ['changed-row']
    refused(positive, 'Baseline persistence differs: ect-native-model')
    positive['modes'][-1]['rows'] = ['synthetic-row']
    positive['native'][0]['strictMatches'] = False
    require(len(inspect(positive)['native_strict_mismatches']) == 1,
            'Native mismatch falsely discarded')


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'fixture':
        fixture()
    else:
        source, destination = map(Path, sys.argv[1:])
        destination.write_text(json.dumps(inspect(json.loads(source.read_text())), indent=2) + '\n')
