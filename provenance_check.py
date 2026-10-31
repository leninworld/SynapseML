"""Required execution harness only; not proposed as a product test."""
import hashlib,inspect,os
from pathlib import Path
from synapse.ml.lightgbm import LightGBMClassifier
from synapse.ml.core.init_spark import init_spark
spark=init_spark()
expected=Path(os.environ['SYNAPSEML_EXPECTED_LIGHTGBM_JAR']).resolve(strict=True)
root=Path(os.environ['SYNAPSEML_EXPECTED_GENERATED_ROOT']).resolve(strict=True)
setter=LightGBMClassifier.setFeatureFractionByNode
actual_wrapper=Path(inspect.getfile(setter)).resolve(strict=True)
module=inspect.getmodule(setter).__name__
generated=root/(module.replace('.','/')+'.py')
assert hashlib.sha256(actual_wrapper.read_bytes()).digest()==hashlib.sha256(generated.read_bytes()).digest(), 'WRAPPER_NOT_BRANCH_GENERATED'
stage=LightGBMClassifier()
actual=Path(stage._java_obj.getClass().getProtectionDomain().getCodeSource().getLocation().toURI().getPath()).resolve(strict=True)
assert hashlib.sha256(actual.read_bytes()).digest()==hashlib.sha256(expected.read_bytes()).digest(), 'JVM_NOT_BRANCH_BUILT'
print({'installed_wrapper':str(actual_wrapper),'generated_source':str(generated),'jvm_jar':str(actual),'jvm_sha256':hashlib.sha256(actual.read_bytes()).hexdigest(),'spark_packages':spark.sparkContext.getConf().get('spark.jars.packages'),'ivy_location':str(actual),'local_maven_jar_candidates':[str(x) for x in Path('/runtime/home/.m2/repository').glob('com/microsoft/azure/synapseml-lightgbm_2.12/0.0.2-i789-SNAPSHOT/*.jar')]})

spark.stop()
