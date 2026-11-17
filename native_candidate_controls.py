"""Proposed same-path early Docker input/JUnit controls; not scientific fit substitutes."""
import copy
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from native_candidate_lane import checked_report,package_reports,require_package_success
from validation_runtime import require


def reject(function,expected_error=None):
    try:function()
    except RuntimeError as error:
        if expected_error is not None:
            require(str(error)==expected_error,'Wrong negative-control rejection guard')
        return
    raise RuntimeError('Negative native-candidate helper fixture accepted')


def controls(root,packet,config):
    require(not root.exists(),'Native-candidate control replay')
    root.mkdir()
    completed=[]
    for index,(suite,names) in enumerate(config['suite_cases'].items()):
        allowed=tuple(config.get('suite_allowed_skips',{}).get(suite,()))
        document=ET.Element('testsuite',name=suite,tests=str(len(names)),failures='0',errors='0',
                            skipped=str(len(allowed)))
        for name in names:
            case=ET.SubElement(document,'testcase',name=name,time='0.1')
            if name in allowed:ET.SubElement(case,'skipped')
        good=root/(str(index)+'-positive.xml');good.write_bytes(ET.tostring(document))
        positive=checked_report(good,suite,names,allowed)
        require(positive['count']==len(names) and positive['executedCount']==len(names)-len(allowed) and
                positive['skippedIDs']==list(allowed),'Exact suite positive control')
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
            reject(lambda:checked_report(path,suite,names,allowed));completed.append(str(index)+'-'+mutation)
        if allowed:
            require(len(names)==11 and len(allowed)==1 and positive['executedCount']==10,
                    'Inherited root fuzzing scope changed')
            reject(lambda:checked_report(good,suite,names))
            completed.append(str(index)+'-ignored-without-explicit-allowance')
            for mutation in ['ignored-missing','ignored-not-skipped','ignored-wrong-id',
                             'ignored-duplicate','ignored-wrong-total','unexpected-extra-id',
                             'ignored-status-swapped','ignored-with-failure']:
                bad=copy.deepcopy(document)
                ignored=next(case for case in bad if case.get('name')==allowed[0])
                if mutation=='ignored-missing':
                    bad.remove(ignored);bad.set('tests',str(len(names)-1));bad.set('skipped','0')
                elif mutation=='ignored-not-skipped':
                    ignored.remove(ignored.find('skipped'));bad.set('skipped','0')
                elif mutation=='ignored-wrong-id':ignored.set('name','Unapproved ignored testcase')
                elif mutation=='ignored-duplicate':
                    bad.append(copy.deepcopy(ignored));bad.set('tests',str(len(names)+1));bad.set('skipped','2')
                elif mutation=='ignored-wrong-total':bad.set('skipped','0')
                elif mutation=='ignored-status-swapped':
                    ignored.remove(ignored.find('skipped'))
                    executed=next(case for case in bad if case.get('name')not in allowed)
                    ET.SubElement(executed,'skipped')
                    # Preserve every testcase ID, tests total and skipped=1.
                elif mutation=='ignored-with-failure':
                    ET.SubElement(ignored,'failure',message='deliberate ignored-case failure')
                else:
                    ET.SubElement(bad,'testcase',name='Unapproved extra executed testcase',time='0.1')
                    bad.set('tests',str(len(names)+1))
                path=root/(str(index)+'-'+mutation+'.xml');path.write_bytes(ET.tostring(bad))
                expected_error={
                    'ignored-status-swapped':'Exact inherited skipped IDs mismatch',
                    'ignored-with-failure':'Ignored case has failure/error',
                }.get(mutation)
                reject(lambda:checked_report(path,suite,names,allowed),expected_error)
                completed.append(str(index)+'-'+mutation)
            for other_suite,other_names in config['suite_cases'].items():
                if other_suite != suite:
                    reject(lambda:checked_report(good,other_suite,other_names,allowed))
            completed.append(str(index)+'-ignore-allowance-rejected-for-other-suites')
    # Independent package completeness reconciliation, not a discovered-count tautology.
    package_paths=[]
    for index,suite in enumerate([config['lanes']['candidate-legacy']['suite'],
                                  config['lanes']['candidate-plural']['suite']]):
        names=config['suite_cases'][suite]
        document=ET.Element('testsuite',name=suite,tests=str(len(names)),failures='0',errors='0')
        for name in names:ET.SubElement(document,'testcase',name=name,time='0.1')
        path=root/('package-'+str(index)+'.xml');path.write_bytes(ET.tostring(document));package_paths.append(path)
    case_count=sum(len(config['suite_cases'][suite]) for suite in [config['lanes']['candidate-legacy']['suite'],config['lanes']['candidate-plural']['suite']])
    summary=f'[info] Total number of tests run: {case_count}\n[info] Suites: completed 2, aborted 0\n'
    actual=package_reports(package_paths,summary);require_package_success(actual,0)
    completed.append('whole-package-summary-positive')
    reject(lambda:package_reports(package_paths,summary.replace('run: '+str(case_count),'run: '+str(case_count-1))))
    completed.append('whole-package-wrong-case-total')
    reject(lambda:package_reports(package_paths,summary.replace('completed 2','completed 1')))
    completed.append('whole-package-wrong-suite-total')
    reject(lambda:package_reports([*package_paths,package_paths[0]],summary))
    completed.append('whole-package-duplicate-suite')
    actual['skips']=[['fixture','skipped']]
    reject(lambda:require_package_success(actual,0));completed.append('whole-package-skip-no-waiver')
    from causal_diagnostic_controls import controls as diagnostic_controls
    extra=diagnostic_controls(root,config)
    completed.extend(extra['controls'])
    result={'state' :'NATIVE_CANDIDATE_HELPER_CONTROLS_COMPLETE','projectFitsRun':False,
            'actualCandidateCompileOrRuntimePassed':False,'sameInputAndJUnitReaders':True,
            'count':len(completed),'controls':completed}
    (root/'native-candidate-controls.json').write_text(json.dumps(result,indent=2))
    return result
