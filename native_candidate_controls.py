"""Proposed same-path early Docker input/JUnit controls; not scientific fit substitutes."""
import copy
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from native_candidate_lane import checked_report,package_reports,require_package_success
from validation_runtime import require


def reject(function):
    try:function()
    except RuntimeError:return
    raise RuntimeError('Negative native-candidate helper fixture accepted')


def controls(root,packet,config):
    require(not root.exists(),'Native-candidate control replay')
    root.mkdir()
    completed=[]
    for index,(suite,names) in enumerate(config['suite_cases'].items()):
        document=ET.Element('testsuite',name=suite,tests=str(len(names)),failures='0',errors='0',skipped='0')
        for name in names:ET.SubElement(document,'testcase',name=name,time='0.1')
        good=root/(str(index)+'-positive.xml');good.write_bytes(ET.tostring(document))
        require(checked_report(good,suite,names)['count']==len(names),'Exact suite positive control')
        completed.append(str(index)+'-exact-suite-positive')
        for mutation in ['empty','missing','duplicate','skip','failure','error','wrong-suite','wrong-total']:
            bad=copy.deepcopy(document)
            if mutation=='empty':
                for child in list(bad):bad.remove(child)
                bad.set('tests','0')
            elif mutation=='missing':bad.remove(list(bad)[0]);bad.set('tests',str(len(names)-1))
            elif mutation=='duplicate':bad.append(copy.deepcopy(list(bad)[0]));bad.set('tests',str(len(names)+1))
            elif mutation in ('skip','failure','error'):
                ET.SubElement(list(bad)[0],{'skip':'skipped'}.get(mutation,mutation))
            elif mutation=='wrong-suite':bad.set('name','different')
            else:bad.set('tests',str(len(names)+1))
            path=root/(str(index)+'-'+mutation+'.xml');path.write_bytes(ET.tostring(bad))
            reject(lambda:checked_report(path,suite,names));completed.append(str(index)+'-'+mutation)
    # Independent package completeness reconciliation, not a discovered-count tautology.
    package_paths=[]
    for index,suite in enumerate([config['lanes']['candidate-legacy']['suite'],
                                  config['lanes']['candidate-plural']['suite']]):
        names=config['suite_cases'][suite]
        document=ET.Element('testsuite',name=suite,tests=str(len(names)),failures='0',errors='0')
        for name in names:ET.SubElement(document,'testcase',name=name,time='0.1')
        path=root/('package-'+str(index)+'.xml');path.write_bytes(ET.tostring(document));package_paths.append(path)
    summary='[info] Total number of tests run: 34\n[info] Suites: completed 2, aborted 0\n'
    actual=package_reports(package_paths,summary);require_package_success(actual,0)
    completed.append('whole-package-summary-positive')
    reject(lambda:package_reports(package_paths,summary.replace('run: 34','run: 33')))
    completed.append('whole-package-wrong-case-total')
    reject(lambda:package_reports(package_paths,summary.replace('completed 2','completed 1')))
    completed.append('whole-package-wrong-suite-total')
    reject(lambda:package_reports([*package_paths,package_paths[0]],summary))
    completed.append('whole-package-duplicate-suite')
    actual['skips']=[['fixture','skipped']]
    reject(lambda:require_package_success(actual,0));completed.append('whole-package-skip-no-waiver')
    result={'state' :'NATIVE_CANDIDATE_HELPER_CONTROLS_COMPLETE','projectFitsRun':False,
            'actualCandidateCompileOrRuntimePassed':False,'sameInputAndJUnitReaders':True,
            'count':len(completed),'controls':completed}
    (root/'native-candidate-controls.json').write_text(json.dumps(result,indent=2))
    return result
