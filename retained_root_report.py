"""Real retained ScalaTest XML reader replay only; never a product-test rerun."""
import hashlib,json
from junit_reader import verified_report,ROOT_FUZZING_SUITE,ROOT_FUZZING_IGNORED
from validation_runtime import require

def replay(lane,phase):
 config=lane.manifest['candidate_native'];bound=config['retained_root_report'];path=lane.packet/bound['path']
 raw=path.read_bytes();require(len(raw)==bound['bytes'] and hashlib.sha256(raw).hexdigest()==bound['sha256'],'Retained root report changed')
 result=verified_report(path,ROOT_FUZZING_SUITE,config['suite_cases'][ROOT_FUZZING_SUITE],(),config['suite_allowed_skips'][ROOT_FUZZING_SUITE])
 require(result['count']==11 and result['executedCount']==10 and result['skippedCount']==1 and result['skippedIDs']==[ROOT_FUZZING_IGNORED],'Retained root report scope changed')
 receipt={'state':'RETAINED_ACTUAL_R4_ROOT_XML_READER_REPLAY_COMPLETE','phase':phase,'actualProducerRun':43794926147,'rawSHA256':bound['sha256'],'report':result,'projectCodeExecuted':False,'doesNotReplaceRequiredFreshLegacyLane':True}
 (lane.root/('retained-root-reader-replay-'+phase+'.json')).write_text(json.dumps(receipt,indent=2))
 return receipt
