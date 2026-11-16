"""Execute exact frozen four fences or emit exact Scala source. UNRUN private draft."""
import hashlib
import json
import os
import zipfile
from pathlib import Path
from docs_contract import QUICK, blocks, manifest, verify_runtime, require, sha


def python_examples(source, artifact_path, out):
    artifact = manifest(artifact_path)
    os.environ['PYSPARK_SUBMIT_ARGS'] = ('--master local[3] --driver-memory 4g --jars ' +
        ','.join(row['path'] for row in artifact['jars']) + ' pyspark-shell')
    from pyspark import SparkContext
    from pyspark.sql import SparkSession
    require(SparkContext._active_spark_context is None, 'FRESH_SPARK_REQUIRED')
    spark = SparkSession.builder.master('local[3]').appName('DML exact Python docs').config(
        'spark.jars', ','.join(row['path'] for row in artifact['jars'])).getOrCreate()
    result = {'complete': False, 'blocks': []}
    try:
        result['provenance'] = verify_runtime(spark, artifact)
        namespace = {'spark': spark, '__name__': '__main__'}
        for index, (language, code) in enumerate(blocks((source / QUICK).read_text())):
            if language != 'python':
                continue
            row = {'fenceIndex': index, 'sha256': sha(code.encode()), 'attempted': True}
            result['blocks'].append(row)
            try:
                exec(compile(code, QUICK + ':' + str(index), 'exec'), namespace)
                model = namespace['dmlModel' if index == 0 else 'plural_model']
                if index == 0:
                    row['rawDrawCount'] = len(model._java_obj.getRawTreatmentEffects())
                    row['average'] = model.getAvgTreatmentEffect()
                    row['interval'] = model.getConfidenceInterval()
                    row['requestedDraws'] = 20
                    row['legacyDroppedDraws'] = 20 - row['rawDrawCount']
                else:
                    require(model.getFittedTreatmentCols() == ['T1', 'T2'], 'PLURAL_ORDER')
                    require(len(model.getRawTreatmentEffectRows()) == 30, 'PLURAL_THIRTY_DRAWS')
                    row.update(requestedDraws=30, fittedNames=model.getFittedTreatmentCols(),
                               means=model.getAvgTreatmentEffects(), intervals=model.getConfidenceIntervals(),
                               pValues=model.getPValues())
                row['completed'] = True
            finally:
                (out / 'quick-python-result.json').write_text(json.dumps(result, indent=2))
        require(len(result['blocks']) == 2, 'EXACT_TWO_PYTHON_BLOCKS')
        result['complete'] = True
    finally:
        spark.stop()
        result['sparkStopped'] = True
        (out / 'quick-python-result.json').write_text(json.dumps(result, indent=2))
    return result


def scala_source(source, artifact_path, out):
    value = manifest(artifact_path)
    selected = [(i, code) for i, (lang, code) in enumerate(blocks((source / QUICK).read_text()))
                if lang == 'scala']
    require([i for i, _ in selected] == [1, 3], 'EXACT_TWO_SCALA_BLOCKS')
    header = '''// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.
package com.microsoft.azure.synapse.ml.causal
import com.microsoft.azure.synapse.ml.core.test.base.TestBase
class ExactDMLDocumentationExamples extends TestBase {
  import spark.implicits._
'''
    # R7 requires actual loaded JAR origin, not compiled-class byte equality.
    expected_jar = json.dumps(str(Path(value['core_jar']).resolve(strict=True)))
    provenance = ("Seq(classOf[DoubleMLEstimator], classOf[DoubleMLModel]).foreach { cls =>\n" +
        "val actual=new java.io.File(cls.getProtectionDomain.getCodeSource.getLocation.toURI).getCanonicalPath\n" +
        "assert(actual == " + expected_jar + ")\n}\n")
    tests = []
    for i, code in selected:
        # Assertions follow the literal fenced source; the fence itself is never rewritten.
        extra = ('assert(dmlModel.getRawTreatmentEffects.nonEmpty)' if i == 1 else
                 'assert(pluralModel.getFittedTreatmentCols.sameElements(Array("T1", "T2")))\n'
                 'assert(pluralModel.getRawTreatmentEffectRows.length == 30)')
        outputs = (
            'println("DML_SCALA_FENCE_1_VALUES\\t" + dmlModel.getAvgTreatmentEffect + "\\t" + '
            'dmlModel.getConfidenceInterval.mkString(",") + "\\t" + dmlModel.getRawTreatmentEffects.length)'
            if i == 1 else
            'println("DML_SCALA_FENCE_3_VALUES\\t" + pluralModel.getFittedTreatmentCols.mkString(",") + '
            '"\\t" + pluralModel.getAvgTreatmentEffects.mkString(",") + "\\t" + '
            'pluralModel.getConfidenceIntervals.map(_.mkString(",")).mkString(";") + "\\t" + '
            'pluralModel.getPValues.mkString(",") + "\\t" + pluralModel.getRawTreatmentEffectRows.length)')
        tests.append('  test("Exact DML Scala fence ' + str(i) + '") {\n' + provenance + code + '\n' + extra + '\n' + outputs +
                     '\n    println("DML_SCALA_FENCE_' + str(i) + '_COMPLETE")\n  }\n')
    text = header + ''.join(tests) + '}\n'
    destination = out / 'ExactDMLDocumentationExamples.scala'
    destination.write_text(text)
    (out / 'quick-scala-inputs.json').write_text(json.dumps({
        'sourceSHA256': sha(text.encode()), 'coreJarSHA256': sha(Path(value['core_jar']).read_bytes()),
        'fences': [{'index': i, 'sha256': sha(code.encode())} for i, code in selected],
        'requiredSuite': 'com.microsoft.azure.synapse.ml.causal.ExactDMLDocumentationExamples',
        'requiredJUnitNames': ['Exact DML Scala fence 1', 'Exact DML Scala fence 3'],
        'requiredSuccessMarkers': ['DML_SCALA_FENCE_1_COMPLETE', 'DML_SCALA_FENCE_3_COMPLETE'],
        'classProvenance': 'Actual loaded estimator/model CodeSource equals exact built candidate core JAR.',
        'executionPassed': False}, indent=2))
    return destination


def scala_result(report, log, out):
    from junit_reader import verified_report
    suite = 'com.microsoft.azure.synapse.ml.causal.ExactDMLDocumentationExamples'
    result = verified_report(report, suite,
                             ['Exact DML Scala fence 1', 'Exact DML Scala fence 3'], ())
    raw = log.read_text()
    for index in (1, 3):
        require(raw.count('DML_SCALA_FENCE_' + str(index) + '_COMPLETE') == 1,
                'SCALA_SUCCESS_MARKER_MISSING_OR_DUPLICATE')
    import math
    actual_outputs = {}
    for index in (1, 3):
        prefix = 'DML_SCALA_FENCE_' + str(index) + '_VALUES\t'
        lines = [line[line.index(prefix):] for line in raw.splitlines() if prefix in line]
        require(len(lines) == 1, 'SCALA_ACTUAL_GETTERS_MISSING_OR_DUPLICATE')
        parts = lines[0].split('\t')
        if index == 1:
            require(len(parts) == 4, 'SINGULAR_GETTER_RECORD_SHAPE')
            require(math.isfinite(float(parts[1])), 'SINGULAR_AVERAGE_NONFINITE')
            interval = [float(x) for x in parts[2].split(',')]
            require(len(interval) == 2 and all(math.isfinite(x) for x in interval), 'SINGULAR_INTERVAL')
            require(0 < int(parts[3]) <= 20, 'SINGULAR_DRAW_COUNT')
        else:
            require(len(parts) == 6 and parts[1] == 'T1,T2' and int(parts[5]) == 30,
                    'PLURAL_GETTER_RECORD_SHAPE')
            means = [float(x) for x in parts[2].split(',')]
            intervals = [[float(x) for x in row.split(',')] for row in parts[3].split(';')]
            ps = [float(x) for x in parts[4].split(',')]
            require(len(means) == len(intervals) == len(ps) == 2 and
                    all(math.isfinite(x) for x in means) and
                    all(len(row) == 2 and all(math.isfinite(x) for x in row) for row in intervals) and
                    all(math.isnan(x) or 0 <= x <= 1 for x in ps), 'PLURAL_GETTER_VALUES')
        actual_outputs[str(index)] = parts[1:]
    result.update(complete=True, actualGetterRecords=actual_outputs,
                  junitSHA256=sha(report.read_bytes()), logSHA256=sha(log.read_bytes()))
    (out / 'quick-scala-result.json').write_text(json.dumps(result, indent=2))
    return result


def scala_tasks(artifact_path, temporary_source):
    """Proposed exact SBT settings; typed compatibility remains UNRUN until early gate."""
    info = manifest(artifact_path)
    jar = Path(info['core_jar']).resolve(strict=True)
    classes = Path(info['core_class_directory']).resolve(strict=True)
    require(classes.name == 'classes' and classes.parent == jar.parent,
            'UNEXPECTED_CORE_COMPILE_CLASS_DIRECTORY')
    quoted_source, quoted_classes, quoted_jar = map(
        json.dumps, [str(temporary_source), str(classes), str(jar)])
    setting = ('set core / Test / fullClasspath ~= { entries => entries.map { entry => '
               'if (entry.data.getCanonicalPath == ' + quoted_classes + ') '
               'entry.map(_ => file(' + quoted_jar + ')) else entry } }')
    return ['set core / Test / unmanagedSources += file(' + quoted_source + ')',
            setting, 'core/Test/compile', 'show core / Test / fullClasspath',
            'core/testOnly com.microsoft.azure.synapse.ml.causal.ExactDMLDocumentationExamples']


def compile_failure_source(out):
    path = out / 'DMLDocumentationCompileFailureControl.scala'
    path.write_text('package com.microsoft.azure.synapse.ml.causal\n'
                    'object DMLDocumentationCompileFailureControl {\n'
                    ' val DML_COMPILE_FAILURE_CONTROL: Int = "must not compile"\n}\n')
    return path


def compile_failure_result(log):
    text = log.read_text()
    require('DMLDocumentationCompileFailureControl.scala' in text and
            'type mismatch' in text and 'DML_COMPILE_FAILURE_CONTROL' in text,
            'WRONG_COMPILE_FAILURE_CAUSE')
    return {'expectedCompileFailureObserved': True, 'rawLogSHA256': sha(log.read_bytes())}


def missing_evidence_fixture(out):
    """Future same-reader control: missing JUnit/marker cannot become a success."""
    import xml.etree.ElementTree as ET
    suite = ET.Element('testsuite', name='com.microsoft.azure.synapse.ml.causal.ExactDMLDocumentationExamples',
                       tests='1', failures='0', errors='0', skipped='0')
    ET.SubElement(suite, 'testcase', name='Exact DML Scala fence 1')
    report, log = out / 'missing-scala-evidence.xml', out / 'missing-scala-evidence.log'
    report.write_bytes(ET.tostring(suite))
    log.write_text('DML_SCALA_FENCE_1_COMPLETE\n')
    try:
        scala_result(report, log, out)
    except RuntimeError:
        return {'missingEvidenceRejected': True}
    raise RuntimeError('SCALA_MISSING_EVIDENCE_ACCEPTED')
