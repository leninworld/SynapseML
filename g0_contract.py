"""Data-only native baseline acceptance contracts, also exercised by early Docker fixtures."""
import math
from validation_runtime import require


def installed_result(result):
    require(result['baseline_only'] is True and result['real_fit'] is True,'Not an actual baseline fit')
    require(result['spark']=='3.5.0','Wrong installed Spark')
    require(result['module_hashes_match'] is True and result['jvm_origin_matches'] is True,
            'Installed artifact provenance mismatch')
    raw=result['raw_effects']
    require(len(raw)==2 and all(math.isfinite(value) for value in raw),'Missing successful baseline draws')
    require(math.isfinite(result['average']) and abs(result['average']-sum(raw)/len(raw))<1e-10,
            'Wrong baseline average')
    require(len(result['interval'])==2 and all(math.isfinite(x) for x in result['interval']) and
            result['interval'][0]<=result['interval'][1],'Wrong baseline confidence interval')
    require(math.isfinite(result['p_value']) and 0<=result['p_value']<=1,'Wrong baseline p-value')
    require(result['copy_equal'] is True and result['save_load_equal'] is True,'Persistence mismatch')
    return True


def clock_admission(now,deadline,required_seconds):
    require(required_seconds>=0 and now+required_seconds<deadline,
            'Full remaining stage forecast cannot fit original deadline')
    return True
