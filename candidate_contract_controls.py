"""Real filesystem/Git controls inside the same approved Docker plan."""
import hashlib
import json
from types import SimpleNamespace

from candidate_contract import apply_candidate, candidate_unchanged, source_file
from validation_runtime import require


def controls(lane, label):
    fixture = lane.root / label
    require(not fixture.exists(), 'Contract fixture replay')
    source, packet, evidence = fixture / 'source', fixture / 'packet', fixture / 'evidence'
    for path in (source, packet / 'candidate', evidence):
        path.mkdir(parents=True)
    names = ['f%d.txt' % i for i in range(10)]
    baseline, candidate = {}, {}
    for name in names:
        old, new = ('old-' + name).encode(), ('new-' + name).encode()
        (source / name).write_bytes(old)
        (packet / 'candidate' / name).write_bytes(new)
        baseline[name] = hashlib.sha256(old).hexdigest()
        candidate[name] = hashlib.sha256(new).hexdigest()

    def command(name, argv, seconds):
        return lane.command(label + '-' + name, argv, seconds, cwd=source)

    command('git-init', ['git', 'init'], 10)
    command('git-add', ['git', 'add', '.'], 10)
    command('git-commit', ['git', '-c', 'user.name=Validation Fixture',
                         '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture'], 10)
    item = SimpleNamespace(source=source, packet=packet, root=evidence, candidate=False,
                           command=command, manifest={'baseline_files': baseline,
                           'candidate_files': candidate, 'source_commit': 'fixture-only'})
    apply_candidate(item)
    passed = ['real-git-ten-file-apply-and-invariance']

    def rejected(name, action):
        try:
            action()
        except RuntimeError:
            passed.append(name)
        else:
            raise RuntimeError('Accepted bad source control: ' + name)

    rejected('replay', lambda: apply_candidate(item))
    for name in ('../escape', '/escape'):
        rejected('unsafe-path-' + name, lambda: source_file(source, name))
    (source / names[0]).write_text('tampered')
    rejected('tampered-candidate', lambda: candidate_unchanged(item, 'tamper'))
    (source / names[0]).write_bytes((packet / 'candidate' / names[0]).read_bytes())
    (source / 'unapproved.txt').write_text('extra')
    rejected('extra-untracked-source', lambda: candidate_unchanged(item, 'extra'))
    (source / 'unapproved.txt').rename(fixture / 'retained-extra-control.txt')
    candidate_unchanged(item, 'final-valid')
    (lane.root / (label + '-result.json')).write_text(json.dumps({'passed': True, 'cases': passed,
        'real_git': True, 'real_filesystem': True, 'project_code_executed': False}, indent=2))
