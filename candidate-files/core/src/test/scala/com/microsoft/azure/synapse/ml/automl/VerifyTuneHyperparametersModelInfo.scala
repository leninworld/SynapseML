// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.automl

import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import com.microsoft.azure.synapse.ml.train.{TrainClassifier, TrainRegressor, TrainedClassifierModel,
  TrainedRegressorModel}
import org.apache.spark.ml.Estimator
import org.apache.spark.ml.feature.VectorAssembler
import org.apache.spark.ml.param.ParamMap
import org.apache.spark.sql.DataFrame
import org.apache.spark.sql.functions.col

class VerifyTuneHyperparametersModelInfo extends TestBase {
  private def data: DataFrame = {
    val rows = (-16 to 16).filter(_ != 0).map { value =>
      (value.toDouble, if (value > 0) 1.0 else 0.0)
    }
    spark.createDataFrame(rows).toDF("x", "label")
  }

  private def tune(estimator: Estimator[_], learner: TestableModelInfoClassifier, input: DataFrame)
      : TuneHyperparametersModel = {
    val params = new GridSpace(Array(ParamMap(learner.knob -> 1), ParamMap(learner.knob -> -1)))
    new TuneHyperparameters().setModels(Array(estimator)).setNumFolds(2).setNumRuns(2)
      .setParallelism(1).setEvaluationMetric("accuracy").setSeed(7).setParamSpace(params).fit(input)
  }

  private def vectorData: DataFrame = {
    new VectorAssembler().setInputCols(Array("x")).setOutputCol("features").transform(data)
  }

  test("winning wrapped classifier reports the applied training setting instead of the last run") {
    val learner = new TestableModelInfoClassifier()
    learner.set(learner.buffer, Array[Byte](1, 2, 3))
    learner.set(learner.unrenderable, "synthetic value")
    learner.set(learner.note, "line\nquoted\"value")
    val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val best = tune(trainer, learner, data)
    val fitted = best.getBestModel.asInstanceOf[TrainedClassifierModel].getLastStage
    val info = best.getBestModelInfo

    assert(best.getBestMetric === 1.0)
    assert(!fitted.hasParam("knob"))
    assert(ModelInfoFitRecord.applied.get(fitted.uid).intValue() === 1)
    assert(info.contains("knob: 1"))
    assert(!info.contains("knob: -1"))
    assert(info.contains("trainer TrainClassifier("))
    assert(info.contains("estimator TestableModelInfoClassifier("))
    assert(info.contains("buffer: <binary omitted>"))
    assert(info.contains("model: <TestableModelInfoClassifier>"))
    assert(info.contains("unrenderable: <unrenderable>"))
    assert(!info.contains("synthetic encoder failure"))
    assert(!info.contains("[B@"))
    assert(info.contains("note: \"line\\nquoted\\\"value\""))
    assert(best.copy(ParamMap.empty).getBestModelInfo === info)
    assert(best.getBestModelParamsInfo === info)
  }

  test("an informational legacy formatter failure does not change a successful fit") {
    val learner = new TestableModelInfoClassifier()
    learner.set(learner.legacyFormattingFailure, 1)
    val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val best = tune(trainer, learner, data)

    assert(best.getBestMetric === 1.0)
    assert(best.getBestModelInfo.startsWith("model: <unrenderable>; "))
    assert(best.getBestModelInfo.contains("knob: 1"))
    assert(!best.getBestModelInfo.contains("synthetic fitted formatting failure"))
    assert(best.transform(data).filter(col("prediction") =!= col("label")).count() === 0L)
    val legacy = best.copy(ParamMap.empty)
    legacy.clear(legacy.bestModelParamsInfo)
    val failure = intercept[IllegalArgumentException] { legacy.getBestModelInfo }
    assert(failure.getMessage === "synthetic fitted formatting failure")
  }

  test("a direct classifier reports its training setting without a legacy model-type error") {
    val learner = new TestableModelInfoClassifier()
    val best = tune(learner, learner, vectorData)

    assert(best.getBestMetric === 1.0)
    assert(best.getBestModelInfo.contains("knob: 1"))
    assert(!best.getBestModelInfo.startsWith("model:"))
  }

  test("custom ParamMap fit dispatch remains active for every fold and the final refit") {
    ModelInfoFitRecord.overriddenFits.set(0)
    val learner = new TestableOverridingModelInfoClassifier()
    val best = tune(learner, learner, vectorData)

    assert(!FitDispatch.usesInheritedParamMapFit(learner))
    assert(ModelInfoFitRecord.overriddenFits.get() === 5)
    assert(best.getBestMetric === 1.0)
    assert(best.transform(vectorData).filter(col("prediction") =!= col("label")).count() === 0L)
    assert(best.get(best.bestModelParamsInfo).isEmpty)
    val failure = intercept[Exception] { best.getBestModelInfo }
    assert(failure.getMessage === EvaluationUtils.ModelTypeUnsupportedErr)
  }

  test("models without a snapshot retain the exact legacy string and unsupported-type error") {
    val learner = new TestableModelInfoClassifier()
    val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val wrapped = tune(trainer, learner, data)
    val legacy = wrapped.copy(ParamMap.empty)
    legacy.clear(legacy.bestModelParamsInfo)

    assert(legacy.getBestModelInfo === EvaluationUtils.modelParamsToString(legacy.getBestModel))
    assert(wrapped.getBestModelInfo === wrapped.getBestModelParamsInfo)

    val direct = tune(learner, learner, vectorData)
    direct.clear(direct.bestModelParamsInfo)
    val failure = intercept[Exception] { direct.getBestModelInfo }
    assert(failure.getMessage === EvaluationUtils.ModelTypeUnsupportedErr)
  }

  test("inherited ParamMap fit is distinguished from a covariant public override") {
    assert(FitDispatch.usesInheritedParamMapFit(new TestableModelInfoClassifier()))
    assert(FitDispatch.usesInheritedParamMapFit(new TrainClassifier()))
    assert(!FitDispatch.usesInheritedParamMapFit(new TestableOverridingModelInfoClassifier()))
  }

  test("wrapped and direct regressors report their independently recorded applied setting") {
    val regressionData = data.withColumn("label", col("x"))
    val learner = new TestableModelInfoRegressor()
    val trainer = new TrainRegressor().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val params = new GridSpace(Array(ParamMap(learner.knob -> 1), ParamMap(learner.knob -> -1)))
    def fitted(estimator: Estimator[_], input: DataFrame): TuneHyperparametersModel = {
      new TuneHyperparameters().setModels(Array(estimator)).setNumFolds(2).setNumRuns(2).setParallelism(1)
        .setEvaluationMetric("rmse").setSeed(7).setParamSpace(params).fit(input)
    }
    val wrapped = fitted(trainer, regressionData)
    val wrappedModel = wrapped.getBestModel.asInstanceOf[TrainedRegressorModel].getLastStage
    val directData = new VectorAssembler().setInputCols(Array("x")).setOutputCol("features")
      .transform(regressionData)
    val direct = fitted(learner, directData)

    assert(wrapped.getBestMetric === 0.0)
    assert(direct.getBestMetric === 0.0)
    assert(ModelInfoFitRecord.applied.get(wrappedModel.uid).intValue() === 1)
    assert(ModelInfoFitRecord.applied.get(direct.getBestModel.uid).intValue() === 1)
    assert(wrapped.getBestModelInfo.contains("knob: 1"))
    assert(direct.getBestModelInfo.contains("knob: 1"))
    assert(!direct.getBestModelInfo.startsWith("model:"))
  }

  test("same-named parameters belonging to a losing candidate never enter the winner snapshot") {
    val first = new TestableModelInfoClassifier()
    val second = new TestableModelInfoClassifier()
    val params = new GridSpace(Array(
      ParamMap(first.knob -> 1, second.knob -> -1),
      ParamMap(first.knob -> -1, second.knob -> -1)))
    val best = new TuneHyperparameters().setModels(Array[Estimator[_]](first, second))
      .setNumFolds(2).setNumRuns(2).setParallelism(1).setEvaluationMetric("accuracy")
      .setSeed(7).setParamSpace(params).fit(vectorData)

    assert(best.getBestMetric === 1.0)
    assert(best.getBestModel.uid === first.uid)
    assert(ModelInfoFitRecord.applied.get(first.uid).intValue() === 1)
    assert(best.getBestModelInfo.contains(first.uid))
    assert(!best.getBestModelInfo.contains(second.uid))
    assert(best.getBestModelInfo.contains("knob: 1"))
  }

  test("heterogeneous maps report inherited effective values missing from the winning sampled map") {
    val learner = new TestableModelInfoClassifier()
    val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val winner = ParamMap(learner.quality -> 1)
    val params = new GridSpace(Array(ParamMap(learner.knob -> 1, learner.quality -> -1), winner))
    val best = new TuneHyperparameters().setModels(Array[Estimator[_]](trainer)).setNumFolds(2).setNumRuns(2)
      .setParallelism(1).setEvaluationMetric("accuracy").setSeed(7).setParamSpace(params).fit(data)
    val fitted = best.getBestModel.asInstanceOf[TrainedClassifierModel].getLastStage

    assert(!winner.contains(learner.knob))
    assert(best.getBestMetric === 1.0)
    assert(ModelInfoFitRecord.applied.get(fitted.uid).intValue() === 1)
    assert(best.getBestModelInfo.contains("knob: 1"))
    assert(best.getBestModelInfo.contains("quality: 1"))
  }

  test("custom wrapper ParamMap fit retains legacy reporting and its final override behavior") {
    ModelInfoFitRecord.overriddenFits.set(0)
    val learner = new TestableModelInfoClassifier()
    val trainer = new TestableOverridingModelInfoTrainer().setModel(learner).setLabelCol("label")
      .setInputCols(Array("x"))
    val best = tune(trainer, learner, data)
    val fitted = best.getBestModel.asInstanceOf[TrainedClassifierModel].getLastStage
      .asInstanceOf[TestableModelInfoClassificationModel]

    assert(ModelInfoFitRecord.overriddenFits.get() === 5)
    assert(best.getBestMetric === 1.0)
    assert(fitted.getOrientation === 1)
    assert(best.get(best.bestModelParamsInfo).isEmpty)
    assert(best.getBestModelInfo === EvaluationUtils.modelParamsToString(best.getBestModel))
  }

  test("saved and loaded snapshots and legacy models retain their respective reporting contracts") {
    val learner = new TestableModelInfoClassifier()
    val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val best = tune(trainer, learner, data)
    val snapshotPath = tmpDir.resolve("snapshot").toString
    best.write.save(snapshotPath)
    val loaded = TuneHyperparametersModel.load(snapshotPath)
    val legacy = best.copy(ParamMap.empty)
    legacy.clear(legacy.bestModelParamsInfo)
    val legacyPath = tmpDir.resolve("legacy").toString
    legacy.write.save(legacyPath)
    val loadedLegacy = TuneHyperparametersModel.load(legacyPath)

    assert(loaded.getBestModelInfo === best.getBestModelInfo)
    assert(loaded.copy(ParamMap.empty).getBestModelInfo === best.getBestModelInfo)
    assert(loaded.transform(data).count() === data.count())
    assert(loadedLegacy.get(loadedLegacy.bestModelParamsInfo).isEmpty)
    assert(loadedLegacy.getBestModelInfo === EvaluationUtils.modelParamsToString(loadedLegacy.getBestModel))
  }

  test("trait-mixin and Java overrides conservatively retain custom fit dispatch") {
    assert(!FitDispatch.usesInheritedParamMapFit(new TestableTraitOverridingModelInfoClassifier()))
    assert(!FitDispatch.usesInheritedParamMapFit(new TestableJavaOverridingModelInfoClassifier()))
  }

  test("independent fits render stable scalar sections after normalizing their owner UIDs") {
    def rendered(): String = {
      val learner = new TestableModelInfoClassifier()
      val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
      tune(trainer, learner, data).getBestModelInfo
        .replace(trainer.uid, "trainer-owner").replace(learner.uid, "learner-owner")
    }

    val first = rendered()
    val second = rendered()

    assert(first === second)
    assert(!first.contains("[B@"))
  }
}
