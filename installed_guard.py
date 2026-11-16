"""Real installed baseline singular DML consumer. No candidate or mocked fits."""
import hashlib
import importlib
import json
import math
from pathlib import Path
import random
import sys

from pyspark.ml.regression import LinearRegression
from pyspark.sql import SparkSession
from validation_runtime import require
from g0_contract import installed_result


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text())
    for row in manifest['jars']:
        require(hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256'],'JAR changed')
    require(sum(Path(row['path']).resolve()==Path(manifest['core_jar']).resolve()
                for row in manifest['jars'])==1,'Core JAR is not uniquely hash-bound')
    spark=SparkSession.builder.master('local[3]').appName('installed-native-singular-DML').config(
        'spark.jars',','.join(row['path'] for row in manifest['jars'])).getOrCreate()
    try:
        require(spark.version=='3.5.0','Wrong Spark runtime')
        for name,expected in manifest['python_modules'].items():
            module=importlib.import_module(name)
            location=Path(module.__file__).resolve()
            require(not location.is_relative_to(Path('/runtime/source')),'Imported source instead of installed wheel')
            require(hashlib.sha256(location.read_bytes()).hexdigest()==expected,'Wrong installed wrapper: '+name)
        jvm=spark._jvm
        loader=jvm.java.lang.Thread.currentThread().getContextClassLoader()
        origins={}
        for name in ['DoubleMLEstimator','DoubleMLModel']:
            cls=jvm.java.lang.Class.forName('com.microsoft.azure.synapse.ml.causal.'+name,True,loader)
            origin=Path(cls.getProtectionDomain().getCodeSource().getLocation().toURI().getPath()).resolve()
            require(origin==Path(manifest['core_jar']).resolve(),'DML loaded from wrong JAR')
            origins[name]=str(origin)
        from synapse.ml.causal.DoubleMLEstimator import DoubleMLEstimator
        from synapse.ml.causal.DoubleMLModel import DoubleMLModel
        rng=random.Random(2328)
        rows=[]
        for index in range(120):
            x=rng.gauss(0,1);t=.8*x+rng.gauss(0,1);y=3*t+2*x+rng.gauss(0,.1)
            rows.append((float(t),float(y),float(x)))
        frame=spark.createDataFrame(rows,['treatment','outcome','confounder'])
        estimator=DoubleMLEstimator().setTreatmentCol('treatment').setOutcomeCol('outcome').setMaxIter(2)
        estimator.setTreatmentModel(LinearRegression()).setOutcomeModel(LinearRegression())
        model=estimator.fit(frame)
        raw=list(model._java_obj.getRawTreatmentEffects())
        average=model.getAvgTreatmentEffect();interval=model.getConfidenceInterval();p_value=model.getPValue()
        require(math.isfinite(average) and abs(average-3)<1,'Independent known-linear effect control failed')
        copied=model.copy({})
        copy_equal=(list(copied._java_obj.getRawTreatmentEffects())==raw and
                    copied.getAvgTreatmentEffect()==average and copied.getConfidenceInterval()==interval and
                    copied.getPValue()==p_value)
        destination=Path(sys.argv[1]).parent/'installed-singular-model'
        require(not destination.exists(),'Consumer persistence destination already exists')
        model.save(str(destination))
        loaded=DoubleMLModel.load(str(destination))
        save_load_equal=(list(loaded._java_obj.getRawTreatmentEffects())==raw and
                         loaded.getAvgTreatmentEffect()==average and loaded.getConfidenceInterval()==interval and
                         loaded.getPValue()==p_value)
        result={'baseline_only':True,'real_fit':True,'spark':spark.version,'module_hashes_match':True,
                'jvm_origin_matches':True,'raw_effects':raw,'average':average,'interval':interval,
                'p_value':p_value,'copy_equal':copy_equal,'save_load_equal':save_load_equal,
                'class_origins':origins,'compile_scala':manifest['compile_scala'],
                'runtime_scala':jvm.scala.util.Properties.versionNumberString(),
                'synthetic_data_seed':2328,'fold_bootstrap_seed':'UNSET_NATIVE_BASELINE',
                'statistical_acceptance':'Structural baseline control, not convergence or nominal p-value validity'}
        installed_result(result)
        (Path(sys.argv[1]).parent/'installed-guard-result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    finally:
        spark.stop()


if __name__=='__main__':
    main()
