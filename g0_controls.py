"""Future real-Docker data-only controls; never imports or starts Spark."""
import copy
import json
import xml.etree.ElementTree as ET
from g0_contract import installed_result,clock_admission
from g0_lane import CASES,SUITE,validate_report,observed_event
from validation_runtime import require


def controls(root,packet):
    require(not root.exists(),'G0 control replay')
    root.mkdir()
    completed=[]
    require(clock_admission(0,3000,2880),'Early remaining-stage admission')
    completed.append('clock-positive')
    for label,now,required in [('late-A',121,2880),('late-B',181,2820),
                               ('late-artifacts',1561,1440),('late-consumer',2461,540)]:
        try:clock_admission(now,3000,required)
        except RuntimeError:completed.append('clock-'+label)
        else:raise RuntimeError('Admitted insufficient remaining-stage forecast: '+label)
    suite=ET.Element('testsuite',name=SUITE,tests='10',failures='0',errors='0',skipped='0')
    for name in CASES:
        ET.SubElement(suite,'testcase',name=name,classname=SUITE,time='1.25')
    path=root/'positive.xml';path.write_bytes(ET.tostring(suite))
    require(validate_report(path)['count']==10,'Full baseline positive reader')
    completed.append('full-ten-positive')
    for label in ['missing-test','duplicate-test','failure','skip','wrong-suite','wrong-total']:
        changed=copy.deepcopy(suite)
        if label=='missing-test': changed.remove(list(changed)[0]);changed.set('tests','9')
        elif label=='duplicate-test':changed.append(copy.deepcopy(list(changed)[0]));changed.set('tests','11')
        elif label=='failure':ET.SubElement(list(changed)[0],'failure')
        elif label=='skip':ET.SubElement(list(changed)[0],'skipped')
        elif label=='wrong-suite':changed.set('name','wrong')
        else:changed.set('tests','11')
        target=root/(label+'.xml');target.write_bytes(ET.tostring(changed))
        try:validate_report(target)
        except RuntimeError:completed.append(label)
        else:raise RuntimeError('Accepted invalid full-suite control: '+label)
    result={'baseline_only':True,'real_fit':True,'spark':'3.5.0','module_hashes_match':True,
            'jvm_origin_matches':True,'raw_effects':[2.,4.],'average':3.,'interval':[2.,4.],
            'p_value':.2,'copy_equal':True,'save_load_equal':True}
    require(installed_result(result),'Installed positive contract');completed.append('installed-positive')
    for field,value in [('real_fit',False),('module_hashes_match',False),('jvm_origin_matches',False),
                        ('raw_effects',[2.]),('average',5.),('interval',[4.,2.]),('p_value',float('nan')),
                        ('copy_equal',False),('save_load_equal',False)]:
        bad={**result,field:value}
        try:installed_result(bad)
        except RuntimeError:completed.append('installed-'+field)
        else:raise RuntimeError('Accepted invalid installed contract: '+field)
    require(observed_event('Executing ATE calculation on iteration: 30'),'Native start observer')
    require(observed_event('ATE calculation got exception on iteration 2'),'Native failure observer')
    require(not observed_event('unrelated line'),'Native observer negative')
    completed.extend(['native-start-observer','native-failure-observer','native-unrelated-negative'])
    from generated_artifacts import wheel_modules,MODULES
    import zipfile
    archive=root/'positive.whl'
    with zipfile.ZipFile(archive,'w') as stream:
        for name in MODULES.values():stream.writestr(name,b'fixture')
    require(set(wheel_modules(archive))==set(MODULES),'Wheel positive control')
    completed.append('wheel-positive')
    missing=root/'missing.whl'
    with zipfile.ZipFile(missing,'w') as stream:stream.writestr('unrelated.py',b'fixture')
    try:wheel_modules(missing)
    except RuntimeError:completed.append('wheel-missing-module')
    else:raise RuntimeError('Accepted missing wrapper')
    result={'state':'DATA_ONLY_HELPER_CONTROLS_COMPLETE','project_tests_run':False,
            'controls':completed,'count':len(completed),'same_readers_as_native_baseline':True}
    (root/'g0-controls.json').write_text(json.dumps(result,indent=2))
    return result
