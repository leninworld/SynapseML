"""Same-exporter incompressible controls: authoring only; run in approved D0."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from evidence_export import export_evidence, line_bytes


def controls(root):
    completed = []
    with tempfile.TemporaryDirectory(prefix="encoded-evidence-control-") as temporary:
        directory = Path(temporary)
        raw = os.urandom(1024**2)
        path = directory / "critical.bin"
        path.write_bytes(raw)
        planned_terminals = []
        def terminal(result):
            lines = ["LANE2328I3=" + json.dumps({"lane": "synthetic", "passed": True}),
                     "HOST2328I3=" + json.dumps({"passed": result["critical_complete"] and
                         result["complete"], "evidence_export": result})]
            planned_terminals.append(line_bytes(lines))
            return lines
        def run(limit):
            emitted = []
            planned_terminals.clear()
            def emit(line, flush):
                assert planned_terminals, "Emission preceded terminal planning"
                emitted.append(line)
            result = export_evidence(directory, [path], time.time() + 60, emit=emit,
                                     cap=2*1024**2, terminal_lines=terminal, encoded_cap=limit)
            return result, emitted, max(planned_terminals)
        result, lines, reserve = run(2*1024**2)
        assert result['complete'] and result['critical_complete']
        exact = line_bytes(lines) + reserve
        assert len(raw) < exact <= 2*1024**2
        pieces = [json.loads(line.split('=', 1)[1]) for line in lines]
        rows = [piece for piece in pieces if piece['path'] == 'critical.bin']
        recovered = b''.join(gzip.decompress(base64.b64decode(piece['base64'])) for piece in rows)
        assert recovered == raw and rows[0]['file_sha256'] == hashlib.sha256(raw).hexdigest()
        completed.append('encoded-incompressible-positive')
        accepted, exact_lines, exact_reserve = run(exact)
        assert accepted['critical_complete'] and line_bytes(exact_lines)+exact_reserve == exact
        completed.append('encoded-exact-boundary-positive')
        for limit, label in [(exact-1, 'encoded-boundary-minus-one-critical-omission'),
                             (1024**2, 'encoded-incompressible-critical-omission')]:
            omitted, emitted, terminal_size = run(limit)
            assert omitted['complete'] and not omitted['critical_complete']
            assert any(row['path']=='critical.bin' and row['critical'] and
                       row['reason']=='encoded-line-budget' for row in omitted['omissions'])
            assert line_bytes(emitted)+terminal_size <= limit
            assert all(json.loads(line.split('=',1)[1])['path'] != 'critical.bin' for line in emitted)
            completed.append(label)
    result = {'state':'DATA_ONLY_ENCODED_BUDGET_CONTROLS_COMPLETE', 'controls':completed,
              'count':len(completed), 'actualNetworkCalls':0, 'projectCodeExecuted':False,
              'incompressibleBytes':1024**2, 'sameProductionExporter':True}
    (root/'encoded-budget-controls.json').write_text(json.dumps(result,indent=2))
    return result
