"""Verify all configured candidate contracts and paired native/probability outputs."""
import copy
import json
import math
from pathlib import Path
import struct
from validation_runtime import require

FAMILIES = ['unlinked', 'linked-space', 'linked-equals', 'oaa-zero', 'oaa-one', 'oaa-unspecified',
            'scores-zero', 'scores-one', 'scores-unspecified', 'probabilities-zero',
            'probabilities-one', 'probabilities-unspecified', 'csoaa-native-model', 'ect-native-model']
SUFFIXES = ['', '-custom', '-no-raw', '-no-probability', '-no-prediction', '-reloaded']


def float32(value):
    return struct.unpack('!f', struct.pack('!f', value))[0]


def vector(value, size):
    require(isinstance(value, dict), 'Raw output is not a Vector')
    if value['type'] == 1:
        result = value['values']
    else:
        require(value['type'] == 0 and value['size'] == size, 'Wrong sparse vector size')
        indices = value['indices']
        require(len(indices) == len(set(indices)) == len(value['values']) and
                all(0 <= index < size for index in indices), 'Invalid sparse vector indices')
        result = [0.] * size
        for index, number in zip(indices, value['values']): result[index] = number
    require(len(result) == size, 'Wrong raw vector length')
    return result


def equal_numbers(left, right):
    return len(left) == len(right) and all(a == b or math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
                                         for a, b in zip(left, right))


def inspect(baseline, candidate, language):
    scala = language == 'scala'
    families = FAMILIES if scala else FAMILIES[:-2]
    key = 'modes' if scala else 'observations'
    names = {family + suffix for family in families for suffix in SUFFIXES}
    # Python names disabled columns by parameter, rather than by Scala suffix.
    if not scala:
        names = {name.replace('-no-raw', '-disabled-rawPredictionCol')
                 .replace('-no-probability', '-disabled-probabilityCol')
                 .replace('-no-prediction', '-disabled-predictionCol') for name in names}
    require(candidate['kind'] == 'candidate' and baseline['kind'] == 'unmodified-master-baseline-only', 'Wrong paired role')
    require(candidate['source'] == baseline['source'] == '36dfb77160ec99ea2bf43c7fae5decaf63ee7396', 'Wrong source')
    rows = candidate[key]
    require(len(rows) == len(names) and {row['name'] for row in rows} == names, 'Incomplete candidate mode coverage')
    before = {row['name']: row for row in baseline[key]}
    require(len(before) == len(baseline[key]) and set(before) == names, 'Incomplete baseline mode coverage')
    for record in rows:
        name = record['name']
        family = next(stem for stem in sorted(families, key=len, reverse=True) if name.startswith(stem))
        binary = family.startswith(('unlinked', 'linked'))
        scores = family.startswith(('scores-', 'probabilities-'))
        winner = not binary and not scores
        size = 2 if binary else 3
        require(record['status'] == 'observed', 'Candidate mode failed: ' + name)
        actual = {v['name']: v for v in record['actualSchema' if scala else 'actual']['fields']}
        declared = {v['name']: v for v in record['declaredSchema' if scala else 'declared']['fields']}
        require('' not in actual and '' not in declared, 'Disabled output materialized')
        raw, prob, pred = [record[k] for k in ['rawColumn', 'probabilityColumn', 'predictionColumn']]
        for column in filter(None, [raw, prob, pred]):
            require((column in actual) == (column in declared), 'Declared output presence mismatch')
            if column in actual:
                require(actual[column]['type'] == declared[column]['type'] and
                        actual[column].get('metadata', {}) == declared[column].get('metadata', {}), 'Output schema metadata mismatch')
        if raw:
            require(isinstance(actual[raw]['type'], dict) and 'VectorUDT' in actual[raw]['type'].get('class', '') and
                    actual[raw]['metadata']['ml_attr']['num_attrs'] == size, 'Wrong raw metadata/type')
        if prob: require((prob in actual) == (not winner), 'Fabricated or missing probabilities')
        old = before[name] if before[name]['status'] == 'observed' else before[family]
        old_rows = {json.loads(value)['id']: json.loads(value) for value in old['rows']}
        values = [json.loads(value) for value in record['rows']]
        require({row['id'] for row in values} == set(old_rows), 'Paired input rows changed')
        for row in values:
            previous = old_rows[row['id']]
            detail = row['vowpalWabbitPredictionCol']
            require(detail == previous['vowpalWabbitPredictionCol'], 'Native prediction changed')
            if scores:
                expected = [float32(value) for value in detail['predictions']]
            elif binary:
                number = float32(detail['prediction'])
                expected = [1-number, number] if family.startswith('linked') else [-number, number]
            else:
                base = 0 if family == 'oaa-zero' else 1
                index = detail['prediction'] - base
                require(isinstance(index, int) and 0 <= index < size, 'Native winner outside class count')
                expected = [0.] * size; expected[index] = 1.
            if raw: require(equal_numbers(vector(row[raw], size), expected), 'Wrong raw vector semantics')
            if prob and not winner:
                require(row[prob] == previous[old['probabilityColumn']], 'Probability changed')
            if pred: require(row[pred] == previous[old['predictionColumn']], 'Prediction changed')
    if scala:
        require(len(candidate['native']) == len(baseline['native']) == 18, 'Incomplete native observations')
        stable = ['indexing','kind','input','status','nativeWinner','strictWinner','sparkWinner',
                  'strictMatches','sparkMatches','scores','probabilities','scoredType','arguments']
        require([{k:row.get(k) for k in stable} for row in candidate['native']] ==
                [{k:row.get(k) for k in stable} for row in baseline['native']], 'Native observation changed')
    return {'candidate_contract_verified':True,'modes':len(rows),'native_observations':18 if scala else None,
            'probability_prediction_unchanged':True,'scope':'configured three output types/metadata only',
            'nonfinite_coverage_claimed':False}


def fixture():
    base = {'kind':'unmodified-master-baseline-only','source':'36dfb77160ec99ea2bf43c7fae5decaf63ee7396','modes':[],'native':[{'input':str(i)} for i in range(18)]}
    candidate = dict(base, kind='candidate', modes=[])
    for family in FAMILIES:
        binary = family.startswith(('unlinked','linked')); scores = family.startswith(('scores-','probabilities-'))
        size = 2 if binary else 3; winner = not binary and not scores
        detail = {'predictions':[-1.,0.,1.]} if scores else {'prediction':.5 if binary else (1 if family=='oaa-zero' else 2)}
        expected = ([.5,.5] if family.startswith('linked') else [-.5,.5]) if binary else ([-1.,0.,1.] if scores else [0.,1.,0.])
        for suffix in SUFFIXES:
            raw,prob,pred = ('rawCustom','probCustom','predCustom') if suffix=='-custom' else ('rawPrediction','probability','prediction')
            if suffix=='-no-raw':raw=''
            if suffix=='-no-probability':prob=''
            if suffix=='-no-prediction':pred=''
            fields=[]; value={'id':0,'vowpalWabbitPredictionCol':detail}
            for column,number in [(raw,expected),(prob,None if winner else [.5,.5] if binary else [-1.,0.,1.]),(pred,0. if binary else 2. if scores else float(detail['prediction']))]:
                if not column or number is None:continue
                is_vector=isinstance(number,list)
                value[column]={'type':1,'values':number} if is_vector else number
                fields.append({'name':column,'type':{'class':'org.apache.spark.ml.linalg.VectorUDT'} if is_vector else 'double','metadata':{'ml_attr':{'num_attrs':size}} if is_vector else {}})
            record={'name':family+suffix,'status':'observed','rawColumn':raw,'probabilityColumn':prob,'predictionColumn':pred,'rows':[json.dumps(value)],'actualSchema':{'fields':fields},'declaredSchema':{'fields':copy.deepcopy(fields)}}
            base['modes'].append(copy.deepcopy(record));candidate['modes'].append(record)
    base=json.loads(json.dumps(base));candidate=json.loads(json.dumps(candidate))
    require(inspect(base,candidate,'scala')['modes']==84,'Positive candidate fixture failed')
    rejected=0
    for mutation in ['length','metadata','prediction','coverage','native']:
        bad=copy.deepcopy(candidate)
        if mutation=='length':
            row=json.loads(bad['modes'][0]['rows'][0]);row['rawPrediction']['values']=[1.];bad['modes'][0]['rows']=[json.dumps(row)]
        elif mutation=='metadata':bad['modes'][0]['actualSchema']['fields'][0]['metadata']={}
        elif mutation=='prediction':
            row=json.loads(bad['modes'][0]['rows'][0]);row['prediction']=9.;bad['modes'][0]['rows']=[json.dumps(row)]
        elif mutation=='coverage':bad['modes'].pop()
        else:bad['native'][0]['status']='changed'
        try:inspect(base,bad,'scala')
        except RuntimeError:rejected+=1
        else:raise RuntimeError('Invalid candidate evidence accepted: '+mutation)
    require(rejected==5,'Missing evidence rejection fixture')
    python_base = {k:v for k,v in base.items() if k not in ('modes','native')}
    python_candidate = dict(python_base, kind='candidate')
    for target, source in [(python_base,base),(python_candidate,candidate)]:
        target['observations']=[]
        for row in source['modes']:
            if row['name'].startswith(('csoaa-native-model','ect-native-model')):continue
            item=copy.deepcopy(row)
            item['name']=item['name'].replace('-no-raw','-disabled-rawPredictionCol').replace('-no-probability','-disabled-probabilityCol').replace('-no-prediction','-disabled-predictionCol')
            item['actual']=item.pop('actualSchema');item['declared']=item.pop('declaredSchema')
            target['observations'].append(item)
    require(inspect(python_base,python_candidate,'python')['modes']==72,'Installed Python oracle fixture failed')
    python_candidate['observations'].pop()
    try:inspect(python_base,python_candidate,'python')
    except RuntimeError:pass
    else:raise RuntimeError('Missing Python mode accepted')



if __name__=='__main__':
    import sys
    if sys.argv[1]=='fixture':fixture()
    else:
        language,left,right,out=sys.argv[1:]
        require(language in ['scala','python'],'Unknown observation contract')
        Path(out).write_text(json.dumps(inspect(json.loads(Path(left).read_text()),json.loads(Path(right).read_text()),language),indent=2))
