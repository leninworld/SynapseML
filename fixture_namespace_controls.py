"""Real Lab.command/Runtime filesystem controls; future approved Docker only."""
import hashlib
import json
from pathlib import Path
import sys

from common import Lab
from validation_runtime import require


PRODUCER = (
    "import json,sys;from pathlib import Path;"
    "p=Path(sys.argv[1]);"
    "p.write_text(json.dumps({'complete':True,'kind':'owned-tiny-producer'}));"
    "Path(sys.argv[2]).write_text('actual-child-completed');"
    "print('ACTUAL_TINY_PRODUCER_COMPLETE',flush=True)"
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def controls(lane, label):
    root = lane.root / label
    require(not root.exists(), 'Namespace control replay')
    root.mkdir()
    item = Lab(lane.packet, root, lane.manifest, lane.deadline)
    item.source = root
    producer = root / 'canonical-fixtures.json'
    marker = root / 'positive-child.txt'
    raw = item.command('fixture-acquisition-step',
                       [sys.executable, '-c', PRODUCER, str(producer), str(marker)], 20)
    require(marker.read_text() == 'actual-child-completed' and
            json.loads(producer.read_text())['complete'] is True,
            'Real positive producer did not complete')
    journal = root / 'fixture-acquisition-step.json'
    receipt = json.loads(journal.read_text())
    require(receipt['exit'] == 0 and receipt['error'] is None and receipt['leader_terminal'] and
            'ACTUAL_TINY_PRODUCER_COMPLETE' in raw,
            'Real Runtime distinct journal did not succeed')
    positive_sha = sha(producer)
    collision = root / 'intentional-collision.json'
    collision_marker = root / 'negative-child.txt'
    error = None
    try:
        item.command('intentional-collision',
                     [sys.executable, '-c', PRODUCER, str(collision), str(collision_marker)], 20)
    except FileExistsError as caught:
        require(Path(caught.filename) == collision, 'Wrong Runtime collision path')
        error = str(caught)
    else:
        raise RuntimeError('Actual Runtime journal collision was not rejected')
    require(collision_marker.read_text() == 'actual-child-completed' and
            json.loads(collision.read_text()) == {'complete': True, 'kind': 'owned-tiny-producer'} and
            'ACTUAL_TINY_PRODUCER_COMPLETE' in (root / 'intentional-collision.log').read_text(),
            'Collision did not preserve the actual completed child producer receipt')
    require(sha(producer) == positive_sha, 'Independent positive producer changed')
    result = {
        'state': 'REAL_LAB_RUNTIME_NAMESPACE_CONTROLS_COMPLETE',
        'cases': ['distinct-producer-and-runtime-journal-success',
                  'same-path-producer-and-runtime-journal-rejected'],
        'caseCount': 2, 'realLabCommand': True, 'realRuntimeFilesystem': True,
        'projectCodeExecuted': False, 'sameOriginalDeadlineEpoch': lane.deadline,
        'positive': {'producer': producer.name, 'producerSHA256': positive_sha,
                     'journal': journal.name, 'journalSHA256': sha(journal)},
        'negative': {'actualError': error, 'producer': collision.name,
                     'producerSHA256': sha(collision), 'childCompleted': True,
                     'rawLog': 'intentional-collision.log',
                     'rawLogSHA256': sha(root / 'intentional-collision.log'),
                     'journalCannotOverwriteProducer': True},
    }
    (lane.root / (label + '-result.json')).write_text(json.dumps(result, indent=2))
    return result
