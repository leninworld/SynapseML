"""Complete candidate lane dispatcher; disabled until exact whole-packet approval. Docker only."""
from pathlib import Path
import hashlib,json,os,sys,time
from validation_runtime import docker_only,require
from common import Lab
from bootstrap import download,full_environment


def main():
    docker_only();require(os.getuid()==1001,'Unexpected UID')
    packet,root=Path('/packet'),Path('/runtime');manifest=json.loads((packet/'manifest.json').read_text());require(manifest['execution_enabled'] is True,'Execution disabled')
    name,phase=os.environ['VALIDATION_LANE'],os.environ['VALIDATION_PHASE'];deadline=float(os.environ['VALIDATION_DEADLINE_EPOCH']);require(name in manifest['lanes'],'Unknown lane')
    reused=phase=='validate';require(phase in ('setup','validate'),'Unknown phase')
    for folder in ['home','tmp','bootstrap']:
        if reused:require((root/folder).is_dir(),'Missing prior setup')
        else:(root/folder).mkdir()
    os.environ.update(HOME='/runtime/home',TMPDIR='/runtime/tmp',JAVA_HOME='/opt/java/openjdk',JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=3',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',RAYON_NUM_THREADS='3',MAMBA_ROOT_PREFIX='/runtime/mamba',PIP_CACHE_DIR='/runtime/pip-cache',PYTHONDONTWRITEBYTECODE='1',PYSPARK_SUBMIT_ARGS='--driver-memory '+str(manifest['lanes'][name]['spark_driver_gib'])+'g pyspark-shell',SPARK_SUBMIT_OPTS='-Duser.home=/runtime/home -Djava.io.tmpdir=/runtime/tmp',NODE_OPTIONS='--max-old-space-size=6144',CI='true',DOCUSAURUS_TELEMETRY_DISABLED='true',SYNAPSEML_ENABLE_PUBLISH='false')
    lane=Lab(packet,root,manifest,deadline);result={'lane':name,'phase':phase,'passed':False,'setup_passed':False,'full_validation_passed':False,'baseline_only':False}
    try:
        if reused:
            setup=json.loads((root/'setup-result.json').read_text());require(setup['setup_passed'] and not setup['full_validation_passed'],'Incomplete original environment setup')
            prefix=root/'mamba/envs/synapseml';os.environ.update(CONDA_DEFAULT_ENV='synapseml',CONDA_PREFIX=str(prefix),PATH='/opt/node/bin:/opt/java/openjdk/bin:'+str(prefix/'bin')+':'+os.environ['PATH'])
        lane.command('d0-'+phase,['python',str(packet/'validation_d0.py'),'--root',str(root/('d0-'+phase)),'--original-deadline-epoch',str(deadline)],90,cwd=packet)
        require(json.loads((root/('d0-'+phase)/'D0-result.json').read_text())['status']=='TINY_FIXTURES_PASS','Incomplete G0')
        from g0_controls import controls
        controls(root / ('g0-controls-' + phase), packet)
        from candidate_contract_controls import controls as candidate_controls
        candidate_controls(lane, 'candidate-contract-' + phase)
        from fixture_namespace_controls import controls as namespace_controls
        namespace_controls(lane, 'fixture-namespace-' + phase)
        from native_events import controls as event_controls
        event_controls(root / ('native-event-controls-' + phase))
        from native_candidate_controls import controls as native_controls
        native_control_result = native_controls(root / ('native-candidate-controls-' + phase), packet, manifest['candidate_native'])
        require(native_control_result['count'] == manifest['candidate_native']['expectedNativeCandidateControlCount'], 'Native helper control count incomplete')
        from retained_root_report import replay as replay_retained_root_report
        replay_retained_root_report(lane, phase)
        # Exercised tiny fixture first, before source-copy/dependency acquisition/project code.
        if not reused:
            sbt=download(manifest['sbt'],root/'bootstrap/sbt-launch.jar',deadline);lane.initial_source()
            (root/'bootstrap-inputs.json').write_text(json.dumps({'sbt':sbt},indent=2))
        else:
            for key,file in [('sbt','sbt-launch.jar'),('micromamba','micromamba')]:require(hashlib.new(manifest[key]['hash_algorithm'],(root/'bootstrap'/file).read_bytes()).hexdigest()==manifest[key]['hash'],'Bootstrap changed between phases')
            require(not lane.command('reused-source-clean',['git','status','--porcelain'],10).strip(),'Setup changed source')
        if phase=='setup':
            # Remaining stages are constant forecasts, never total minus elapsed wall time.
            # Lab adds120s once; the other120s is included below (240s combined reserve).
            remaining_forecast=manifest['lanes'][name]['pre_environment_remaining_seconds']
            lane.initial_admission('full-environment-and-complete-finish',remaining_seconds=remaining_forecast)
            mm=download(manifest['micromamba'],root/'bootstrap/micromamba',deadline);(root/'bootstrap/micromamba').chmod(0o755);full_environment(lane)
            versions=lane.command('required-library-versions',['python','-c','import sys,numpy,pyspark,black,nbformat,nbclient; assert sys.version_info[:3]==(3,11,8);assert numpy.__version__=="1.26.4";assert pyspark.__version__=="3.5.0";assert black.__version__=="22.3.0";print(sys.version,numpy.__version__,pyspark.__version__,black.__version__,nbformat.__version__,nbclient.__version__)'],30)
            result.update(setup_passed=True);return
        from candidate_contract import apply_candidate, candidate_unchanged
        apply_candidate(lane)
        if name.startswith('candidate-'):
            lane.command('acquire-standard-test-fixtures', ['python', str(packet/'fixture_data.py'),
                         str(packet), str(root), str(deadline)], 180, cwd=packet)
            fixtures = json.loads((root/'standard-test-fixtures.json').read_text())
            require(fixtures['complete'] is True, 'Incomplete standard test fixtures')
            lane.datasets_home = Path(fixtures['dataset_dir'])
            os.environ['DATASETS_HOME'] = str(lane.datasets_home)
        if name in ('candidate-legacy','candidate-plural') or name.startswith('candidate-causal-'):
            from native_candidate_lane import validate
        elif name == 'candidate-installed':
            from candidate_installed_lane import validate
        else:
            from candidate_docs_lane import validate
        validate(lane)
        candidate_unchanged(lane, 'final-candidate-invariance')
        result.update(passed=True, baseline_only=False, full_validation_passed=True,
                      source_kind='exact-candidate-postimages', scope='this complete lane only; entire feature requires all manifest lanes')
    except BaseException as error:
        result['error']=f'{type(error).__name__}: {error}';raise
    finally:
        try:
            from native_events import summarize
            event_summary = summarize(lane)
            require(event_summary['state'] == 'ACTUAL_JOB_EVENT_CLASSIFICATION_COMPLETE',
                    'Incomplete solver/test-rerun event classification')
            # Keep complete per-line classifications in the exported sidecar, not
            # duplicated inside the bounded terminal producer record.
            summary_file = root/'native-event-summary.json'
            result['nativeEventSummary'] = {key:event_summary[key] for key in
                ('state','lane','solverFallbackCount','testRerunCount','unclassifiedRetryLineCount',
                 'failedDrawWarningCount','allThirtyDrawsSucceeded')}
            result['nativeEventSummary'].update(path=summary_file.name,
                bytes=summary_file.stat().st_size,
                sha256=hashlib.sha256(summary_file.read_bytes()).hexdigest())
        except BaseException as observer_error:
            result['passed'] = False
            result['eventObserverError'] = str(observer_error)
            raise
        finally:
            result['steps']=lane.records
            (root/('setup-result.json' if phase=='setup' else 'lane-result.json')).write_text(json.dumps(result,indent=2))
            print('LANE2328I3='+json.dumps(result),flush=True)
if __name__=='__main__':main()
