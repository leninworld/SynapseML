"""Exact approved candidate postimages and complete Git source invariance."""
import json
from pathlib import Path

from common import sha
from validation_runtime import require


def source_file(root, relative):
    name = Path(relative)
    require(not name.is_absolute() and '..' not in name.parts, 'Unsafe candidate path')
    result = root / name
    require(result.resolve().is_relative_to(root.resolve()), 'Source path escapes root')
    require(not result.is_symlink(), 'Symlink candidate postimage')
    return result


def apply_candidate(lane):
    require(not lane.candidate, 'Candidate already applied; no replay')
    for name, digest in lane.manifest['baseline_files'].items():
        path = source_file(lane.source, name)
        require((not path.exists()) if digest is None else sha(path) == digest,
                'Baseline moved before candidate apply: ' + name)
    candidate = lane.manifest['candidate_files']
    require(len(candidate) == 10, 'Full ten-file candidate scope required')
    for name, digest in candidate.items():
        original = source_file(lane.packet / 'candidate', name)
        require(original.is_file() and sha(original) == digest, 'Packet postimage mismatch: ' + name)
        path = source_file(lane.source, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original.read_bytes())
    lane.candidate = True
    candidate_unchanged(lane, 'candidate-applied')


def candidate_unchanged(lane, label):
    require(lane.candidate, 'Candidate not applied')
    expected = dict(lane.manifest['baseline_files'])
    expected.update(lane.manifest['candidate_files'])
    for name, digest in expected.items():
        path = source_file(lane.source, name)
        require((not path.exists()) if digest is None else sha(path) == digest,
                'Candidate/unaffected source changed: ' + name)
    tracked = lane.command(label + '-tracked', ['git', 'diff', '--name-only', 'HEAD'], 10)
    untracked = lane.command(label + '-untracked',
                             ['git', 'ls-files', '--others', '--exclude-standard'], 10)
    observed = set(tracked.splitlines()) | set(untracked.splitlines())
    require(observed == set(lane.manifest['candidate_files']),
            'Extra/missing source modifications: ' + repr(sorted(observed)))
    (lane.root / (label + '.json')).write_text(json.dumps({
        'passed': True, 'candidate': lane.manifest['candidate_files'],
        'all_changed_paths': sorted(observed), 'source_pin': lane.manifest['source_commit'],
        'unchanged_adopted_source_count': len(set(expected) - set(lane.manifest['candidate_files'])),
    }, indent=2))
