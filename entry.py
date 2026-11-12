"""Private874 G0 measurement; exact approval required, no Mac execution."""
from pathlib import Path
import hashlib,json,os,sys,time
from validation_runtime import docker_only,require
from common import Lab
from bootstrap import download,full_environment


def main():
    docker_only();require(os.getuid()==1001,'Unexpected UID')
    packet,root=Path('/packet'),Path('/runtime');manifest=json.loads((packet/'manifest.json').read_text());require(manifest['execution_enabled'] is True,'Execution disabled')
    name,phase=os.environ['VALIDATION_LANE'],os.environ['VALIDATION_PHASE'];deadline=float(os.environ['VALIDATION_DEADLINE_EPOCH']);require(name in manifest['lanes'],'Unknown lane')
    reused=name=='python-interop' and phase=='validate';require(phase=='validate' or(name=='python-interop' and phase=='setup'),'Unknown phase')
    for folder in ['home','tmp','bootstrap']:
        if reused:require((root/folder).is_dir(),'Missing prior setup')
        else:(root/folder).mkdir()
    os.environ.update(HOME='/runtime/home',TMPDIR='/runtime/tmp',JAVA_HOME='/opt/java/openjdk',JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',RAYON_NUM_THREADS='2',MAMBA_ROOT_PREFIX='/runtime/mamba',PIP_CACHE_DIR='/runtime/pip-cache',PYTHONDONTWRITEBYTECODE='1',PYSPARK_SUBMIT_ARGS='--driver-memory 1g pyspark-shell',SPARK_SUBMIT_OPTS='-Duser.home=/runtime/home -Djava.io.tmpdir=/runtime/tmp',NODE_OPTIONS='--max-old-space-size=6144',CI='true',DOCUSAURUS_TELEMETRY_DISABLED='true',SYNAPSEML_ENABLE_PUBLISH='false')
    lane=Lab(packet,root,manifest,deadline);result={'lane':name,'phase':phase,'passed':False,'setup_passed':False,'full_validation_passed':False}
    try:
        if reused:
            setup=json.loads((root/'setup-result.json').read_text());require(setup['setup_passed'] and not setup['full_validation_passed'],'Incomplete original environment setup')
            prefix=root/'mamba/envs/synapseml';os.environ.update(CONDA_DEFAULT_ENV='synapseml',CONDA_PREFIX=str(prefix),PATH='/opt/node/bin:/opt/java/openjdk/bin:'+str(prefix/'bin')+':'+os.environ['PATH'])
        lane.command('d0-'+phase,['python',str(packet/'validation_d0.py'),'--root',str(root/('d0-'+phase)),'--original-deadline-epoch',str(deadline)],90,cwd=packet)
        require(json.loads((root/('d0-'+phase)/'D0-result.json').read_text())['status']=='TINY_FIXTURES_PASS','Incomplete G0')
        # Exercised tiny fixture first, before source-copy/dependency acquisition/project code.
        if not reused:
            sbt=download(manifest['sbt'],root/'bootstrap/sbt-launch.jar',deadline);lane.initial_source()
            (root/'bootstrap-inputs.json').write_text(json.dumps({'sbt':sbt},indent=2))
        else:
            for key,file in [('sbt','sbt-launch.jar'),('micromamba','micromamba')]:require(hashlib.new(manifest[key]['hash_algorithm'],(root/'bootstrap'/file).read_bytes()).hexdigest()==manifest[key]['hash'],'Bootstrap changed between phases')
            require(not lane.command('reused-source-clean',['git','status','--porcelain'],10).strip(),'Setup changed source')
        if phase=='setup':
            lane.initial_admission('full-environment-and-complete-finish',remaining_seconds=1500)
            mm=download(manifest['micromamba'],root/'bootstrap/micromamba',deadline);(root/'bootstrap/micromamba').chmod(0o755);full_environment(lane)
            versions=lane.command('required-library-versions',['python','-c','import sys,numpy,pyspark,black,nbformat,nbclient; assert sys.version_info[:3]==(3,11,8);assert numpy.__version__=="1.26.4";assert pyspark.__version__=="3.5.0";assert black.__version__=="22.3.0";print(sys.version,numpy.__version__,pyspark.__version__,black.__version__,nbformat.__version__,nbclient.__version__)'],30)
            result.update(setup_passed=True);return
        lane.command('toolchain-java',['java','-version'],20)
        from interop_lane import measure
        measure(lane)
        result.update(passed=True, full_validation_passed=False,
                      source_kind='unchanged-upstream', measurement='PYTHON_NATIVE_INTEROP_G0',
                      qualification='Not candidate tutorial/test/website validation')
    except BaseException as error:
        result['error']=f'{type(error).__name__}: {error}';raise
    finally:
        result['steps']=lane.records;(root/('setup-result.json' if phase=='setup' else 'lane-result.json')).write_text(json.dumps(result,indent=2));print('LANE874='+json.dumps(result),flush=True)
if __name__=='__main__':main()
