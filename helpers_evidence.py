"""Docker-only oracles against actual notebook functions, never mirrored helpers."""
import argparse
import ast
from collections import defaultdict
import hashlib
import json
from math import pi
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('notebook', type=Path)
    parser.add_argument('--mode', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not Path('/.dockerenv').exists():
        raise SystemExit('Docker-only evidence harness')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from pyspark.ml.linalg import DenseVector
    import pyspark.sql.functions as F

    raw = args.notebook.read_bytes()
    notebook = json.loads(raw)
    names = {'plot_dependence_for_numeric', 'plot_dependence_for_categorical',
             'plot_ice_numeric', 'plot_ice_categorical', 'overlay_ice_with_pdp'}
    namespace = {'plt': plt, 'F': F}
    found, extracted = set(), []
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code':
            continue
        source = ''.join(cell['source'])
        tree = ast.parse(source)
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if not any(node.name in names for node in functions):
            continue
        # Include private helpers in the SAME actual cell; do not reimplement them.
        for node in functions:
            if node.name in found:
                raise AssertionError('Duplicate notebook function: ' + node.name)
            found.add(node.name)
        for node in tree.body:
            assert isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)) or (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            ), 'Non-declarative top-level helper cell statement'
        exec(compile(source, str(args.notebook), 'exec'), namespace)
        extracted.append({'cell_sha256': hashlib.sha256(source.encode()).hexdigest(),
                          'functions': [node.name for node in functions]})
    assert names <= found, names - found
    records = []

    def record(name, detail=None):
        records.append({'case': name, 'result': 'PASS', 'detail': detail})

    def frame(keys, rows):
        columns = [str(key) for key in keys]
        records = [[DenseVector([value]) if value is not None else None for value in row] for row in rows]
        result = pd.DataFrame.from_records(records, columns=columns)
        assert all(value is None or isinstance(value, DenseVector)
                   for row in result.itertuples(index=False, name=None) for value in row)
        return result

    def invoke(name, *values, **kwargs):
        plt.close('all')
        namespace[name](*values, **kwargs)
        return plt.gcf().axes[0]

    def line_matches(line, xs, ys):
        x = np.asarray(line.get_xdata(orig=True))
        y = np.asarray(line.get_ydata(orig=True))
        assert x.dtype.kind in 'fiu', ('categorical rather than numeric x', x.tolist())
        np.testing.assert_allclose(x, xs, rtol=0, atol=1e-12)
        np.testing.assert_allclose(y, ys, rtol=0, atol=1e-12)

    numeric = frame(['10.5', '-1.25', '0.25', '0.1'], [[1.05, 0.125, 0.025, 0.01],
                                                                   [2.05, 0.225, 0.125, 0.11]])
    if args.mode == 'baseline':
        try:
            invoke('plot_dependence_for_numeric', numeric.iloc[:1], 'feature')
        except ValueError as exc:
            assert 'int' in str(exc) and '10.5' in str(exc), str(exc)
            record('base-float-key-ValueError', str(exc))
        else:
            raise AssertionError('Expected unchanged baseline float-key exception not reproduced')
        ax = invoke('plot_dependence_for_numeric', numeric.iloc[:1], 'feature', col_int=False)
        original = np.asarray(ax.lines[0].get_xdata(orig=True))
        assert original.dtype.kind in 'OUS', original
        record('base-string-categorical-axis', original.tolist())
        ice = frame(['10', '1', '2'], [[0.9, 0.1, 0.2], [0.8, 0.3, 0.4]])
        pdp = frame(['2', '10', '1'], [[0.2, 0.9, 0.1]])
        ax = invoke('overlay_ice_with_pdp', ice, pdp, 'feature')
        line = ax.lines[-1]
        assert list(line.get_xdata(orig=True)) == ['10', '1', '2']
        np.testing.assert_allclose(line.get_ydata(orig=True), [0.1, 0.2, 0.9])
        assert not np.allclose(line.get_ydata(orig=True), [0.9, 0.1, 0.2])
        record('base-overlay-key-value-misalignment', {'x': list(line.get_xdata(orig=True)),
                                                        'y': list(line.get_ydata(orig=True))})
    else:
        xs = [-1.25, 0.1, 0.25, 10.5]
        expected = [[0.125, 0.01, 0.025, 1.05], [0.225, 0.11, 0.125, 2.05]]
        for col_int in (True, False):
            ax = invoke('plot_dependence_for_numeric', numeric.iloc[:1], 'feature', col_int=col_int)
            assert len(ax.lines) == 1
            line_matches(ax.lines[0], xs, expected[0])
            record('numeric-pdp-float-axis-col_int-' + str(col_int))
            ax = invoke('plot_ice_numeric', numeric, 'feature', col_int=col_int)
            assert len(ax.lines) == 2
            for line, values in zip(ax.lines, expected):
                line_matches(line, xs, values)
            record('numeric-ice-row-key-identity-col_int-' + str(col_int))
            independent_pdp = frame(['7', '-2', '0.25'], [[0.7, 0.2, 0.025]])
            ax = invoke('overlay_ice_with_pdp', numeric, independent_pdp, 'feature', col_int=col_int)
            assert len(ax.lines) == 3
            for line, values in zip(ax.lines[:2], expected):
                line_matches(line, xs, values)
            line_matches(ax.lines[-1], [-2, 0.25, 7], [0.2, 0.025, 0.7])
            record('overlay-independent-order-and-grid-col_int-' + str(col_int))
        single = frame(['0.1'], [[0.4]])
        for name in ('plot_dependence_for_numeric', 'plot_ice_numeric', 'overlay_ice_with_pdp'):
            values = (single, single, 'feature') if name.startswith('overlay') else (single, 'feature')
            ax = invoke(name, *values)
            for line in ax.lines:
                line_matches(line, [0.1], [0.4])
                assert line.get_marker() not in (None, 'None', '', ' '), 'Single point not visible'
            record(name + '-single-point-marker')
        duplicate = frame(['-0.0', '0.0'], [[0.1, 0.2]])
        missing_pdp = frame(['0.1', '0.25'], [[0.1, None]])
        missing_ice = frame(['0.1', '0.25'], [[0.1, 0.2], [0.3, None]])
        for name in ('plot_dependence_for_numeric', 'plot_ice_numeric', 'overlay_ice_with_pdp'):
            missing = missing_pdp if name == 'plot_dependence_for_numeric' else missing_ice
            row = 0 if name == 'plot_dependence_for_numeric' else 1
            for bad, kind in ((duplicate, 'duplicate'), (missing, 'missing')):
                values = (bad, single, 'feature') if name.startswith('overlay') else (bad, 'feature')
                try:
                    invoke(name, *values)
                except ValueError as exc:
                    if kind == 'missing':
                        assert f'row {row}' in str(exc) and "column '0.25'" in str(exc), str(exc)
                else:
                    raise AssertionError(name + ' silently accepted ' + kind)
                record(name + '-' + kind + '-ValueError')
        for bad, kind in ((duplicate, 'duplicate'), (missing_pdp, 'missing')):
            try:
                invoke('overlay_ice_with_pdp', single, bad, 'feature')
            except ValueError as exc:
                if kind == 'missing':
                    assert 'row 0' in str(exc) and "column '0.25'" in str(exc), str(exc)
            else:
                raise AssertionError('Overlay silently accepted bad PDP ' + kind)
            record('overlay-PDP-side-' + kind + '-ValueError')
    # Unchanged categorical behavior is checked in BOTH modes.
    category = frame(['B', 'A'], [[0.8, 0.2]])
    ax = invoke('plot_dependence_for_categorical', category, 'category', col_int=False)
    ax.figure.canvas.draw()
    assert [label.get_text() for label in ax.get_xticklabels()] == ['A', 'B']
    np.testing.assert_allclose([bar.get_height() for bar in ax.patches], [0.2, 0.8])
    record('categorical-control')
    plt.close('all')
    args.out.write_text(json.dumps({'mode': args.mode, 'status': 'PASS',
        'notebook_sha256': hashlib.sha256(raw).hexdigest(), 'extracted': extracted,
        'versions': {'matplotlib': matplotlib.__version__, 'pandas': pd.__version__},
        'real_DenseVector': True, 'mirrored_implementation': False, 'cases': records}, indent=2) + '\n')

if __name__ == '__main__':
    main()
