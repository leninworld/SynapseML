"""Complete whole-website finish of the exact previously tested eight-file patch.
No new execution authority: ordinary two attempts exhausted; explicit exception required.
"""
import hashlib,json,shutil
from pathlib import Path
from validation_runtime import require

def validate(lane):
    lane.initial_admission('same-source-whole-website-finish',remaining_seconds=1500)
    lane.command('real-inspect-build-success-failure-fixtures',
        ['python','-c','from website_contract import fixture,inspect_build_fixture,zero_match_diagnostics_fixture;fixture();inspect_build_fixture();zero_match_diagnostics_fixture()'],
        30,cwd=lane.packet)
    lane.apply()
    require(lane.manifest['patch_sha256']==lane.manifest['prior_candidate_evidence']['tested_patch_sha256'],
            'Candidate patch differs from actual prior native validation')
    lane.verify_candidate('frozen-reused-native-source')
    (lane.root/'native-results-reuse-binding.json').write_text(json.dumps({
        'reuse_only_not_new_native_execution':True,
        'source_commit':lane.manifest['source_commit'],
        'all_eight_actual_tested_source_sha256':lane.manifest['candidate_files'],
        'prior':lane.manifest['prior_candidate_evidence']},indent=2))
    website(lane)
    result={'source_sha256':lane.manifest['candidate_files'],
            'tested_patch_sha256':lane.manifest['patch_sha256'],
            'input_patch_sha256':lane.manifest['patch_sha256'],
            'no_source_change_from_actual_prior_native_run':True}
    (lane.root/'frozen-documentation.json').write_text(json.dumps(result,indent=2))
    (lane.root/'tested-documentation.patch').write_bytes((lane.packet/'candidate.patch').read_bytes())

def website(lane):
    from website_contract import inspect_build
    for name, expected in lane.manifest['website_input_sha256'].items():
        require(hashlib.sha256((lane.source / 'website' / name).read_bytes()).hexdigest() == expected,
                'Website dependency input changed')
    lane.initial_admission('whole-website-finish', remaining_seconds=900)
    lane.command('npm-ci', ['npm', 'ci', '--no-audit', '--no-fund'], 600, cwd=lane.source / 'website')
    lane.command('actual-header-positive-negative-fixture',
                 ['python', str(lane.packet / 'website_check.py'), '--fixture'], 30, cwd=lane.source / 'website')
    lane.command('website-article-contract-fixtures', ['python', '-c',
                 'from website_contract import fixture;fixture()'], 15, cwd=lane.packet)
    lane.command('docgen-import-closure', ['python', '-c',
                 'import nbformat,nbconvert,pypandoc,markdownify,markdown,traitlets,yaml,bs4,requests; '
                 'print(nbformat.__version__,nbconvert.__version__)'], 30)
    lane.command('docgen-install', ['python', '-m', 'pip', 'install', '--no-deps', 'tools/docgen'], 120)
    lane.command('whole-website-generate', ['python', str(lane.packet / 'website_generate.py')], 300)
    lane.command('whole-website-tests', ['npm', 'test'], 180, cwd=lane.source / 'website')
    lane.command('whole-website-production-build', ['npm', 'run', 'build'], 900, cwd=lane.source / 'website')
    page, overview, route = inspect_build(lane.source / 'website/build', lane.root / 'website-SSR-diagnostics')
    shutil.copy2(page, lane.root / 'tutorial-rendered.html')
    shutil.copy2(overview, lane.root / 'overview-rendered.html')
    (lane.root / 'website-SSR.json').write_text(json.dumps({'route': route, 'passed': True,
        'production_article_overview_sidebar_verified': True}))
    lane.command('actual-dev-server-new-page-route', ['python', str(lane.packet / 'website_check.py')], 600)
    # Generation may update only website/docs, preserving the complete product patch.
    for name, expected in lane.manifest['candidate_files'].items():
        require(hashlib.sha256((lane.source / name).read_bytes()).hexdigest() == expected, 'Product source changed')
    changed = set(lane.command('final-tracked-scope', ['git', 'diff', '--name-only', 'HEAD'], 10).splitlines())
    wanted = {name for name, value in lane.manifest['baseline_files'].items() if value is not None}
    require({name for name in changed if not name.startswith('website/docs/')} == wanted, 'Unexpected tracked source')
    new = set(lane.command('final-untracked-scope', ['git', 'ls-files', '--others', '--exclude-standard'], 10).splitlines())
    wanted_new = {name for name, value in lane.manifest['baseline_files'].items() if value is None}
    require(new == wanted_new, 'Unexpected final untracked source')
