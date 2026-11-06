// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.lightgbm.split1

import com.microsoft.azure.synapse.ml.automl.{GridSpace, TuneHyperparameters, TuneHyperparametersModel}
import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import com.microsoft.azure.synapse.ml.lightgbm.{LightGBMClassificationModel, LightGBMClassifier}
import com.microsoft.azure.synapse.ml.train.{TrainClassifier, TrainedClassifierModel}
import org.apache.spark.ml.Estimator
import org.apache.spark.ml.param.ParamMap

class VerifyLightGBMTuningModelInfo extends TestBase {
  private def release(model: TuneHyperparametersModel): Unit = {
    model.getBestModel.asInstanceOf[TrainedClassifierModel].getLastStage
      .asInstanceOf[LightGBMClassificationModel].getLightGBMBooster.freeNativeMemory()
  }

  Seq("bulk", "streaming").foreach { mode =>
    test(s"winning LightGBM training parameters survive copy and persistence in $mode mode") {
      val session = spark
      import session.implicits._
      val data = (0 until 64).map(index =>
        ((index % 8 - 4).toDouble, if (index % 8 >= 4) 1.0 else 0.0)).toDF("x", "label").coalesce(1)
      val learner = new LightGBMClassifier().setNumTasks(1).setNumThreads(1).setNumIterations(2)
        .setVerbosity(-1).setDataTransferMode(mode).setPassThroughArgs("feature_pre_filter=false")
      val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
      val params = new GridSpace(Array(
        ParamMap(learner.numLeaves -> 7, learner.minDataInLeaf -> 1),
        ParamMap(learner.numLeaves -> 31, learner.minDataInLeaf -> 1000)))
      val best = new TuneHyperparameters().setModels(Array[Estimator[_]](trainer)).setNumFolds(2).setNumRuns(2)
        .setParallelism(1).setEvaluationMetric("accuracy").setSeed(7).setParamSpace(params).fit(data)
      var loaded = Option.empty[TuneHyperparametersModel]
      try {
        val nativeModel = best.getBestModel.asInstanceOf[TrainedClassifierModel].getLastStage
        val info = best.getBestModelInfo
        val path = tmpDir.resolve(s"model-info-$mode").toString
        best.write.save(path)
        loaded = Some(TuneHyperparametersModel.load(path))

        assert(best.getBestMetric === 1.0)
        assert(!nativeModel.hasParam("numLeaves"))
        assert(info.contains("numLeaves: 7"))
        assert(info.contains("minDataInLeaf: 1"))
        assert(!info.contains("numLeaves: 31"))
        assert(info.contains("referenceDataset: <binary omitted>"))
        assert(best.copy(ParamMap.empty).getBestModelInfo === info)
        loaded.foreach { restored =>
          assert(restored.getBestModelInfo === info)
          assert(restored.transform(data).count() === data.count())
        }
      } finally {
        try {
          loaded.foreach(release)
        } finally {
          release(best)
        }
      }
    }
  }
}
