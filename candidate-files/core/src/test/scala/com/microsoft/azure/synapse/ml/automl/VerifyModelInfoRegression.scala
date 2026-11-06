// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.automl

import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import com.microsoft.azure.synapse.ml.train.{TrainClassifier, TrainRegressor}
import org.apache.spark.ml.Estimator
import org.apache.spark.ml.feature.VectorAssembler
import org.apache.spark.ml.param.ParamMap
import org.apache.spark.sql.DataFrame
import org.apache.spark.sql.functions.col

/** These public-API oracles compile against the unmodified production baseline. */
class VerifyModelInfoRegression extends TestBase {
  private def data: DataFrame = {
    val rows = (-16 to 16).filter(_ != 0).map(value =>
      (value.toDouble, if (value > 0) 1.0 else 0.0))
    spark.createDataFrame(rows).toDF("x", "label")
  }

  private def fit(estimator: Estimator[_], params: GridSpace, metric: String, input: DataFrame)
      : TuneHyperparametersModel = {
    new TuneHyperparameters().setModels(Array(estimator)).setNumFolds(2).setNumRuns(2)
      .setParallelism(1).setEvaluationMetric(metric).setSeed(7).setParamSpace(params).fit(input)
  }

  test("wrapped classifier reports the winning training-only knob") {
    val learner = new TestableModelInfoClassifier()
    val trainer = new TrainClassifier().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val params = new GridSpace(Array(ParamMap(learner.knob -> 1), ParamMap(learner.knob -> -1)))
    val best = fit(trainer, params, "accuracy", data)

    assert(best.getBestMetric === 1.0)
    assert(ModelInfoFitRecord.applied.get(learner.uid).intValue() === 1)
    assert(best.getBestModelInfo.contains("knob: 1"))
  }

  test("wrapped regressor reports the winning training-only knob") {
    val learner = new TestableModelInfoRegressor()
    val trainer = new TrainRegressor().setModel(learner).setLabelCol("label").setInputCols(Array("x"))
    val params = new GridSpace(Array(ParamMap(learner.knob -> 1), ParamMap(learner.knob -> -1)))
    val best = fit(trainer, params, "rmse", data.withColumn("label", col("x")))

    assert(best.getBestMetric === 0.0)
    assert(ModelInfoFitRecord.applied.get(learner.uid).intValue() === 1)
    assert(best.getBestModelInfo.contains("knob: 1"))
  }

  test("a direct classifier exposes its applied training-only setting") {
    val learner = new TestableModelInfoClassifier()
    val input = new VectorAssembler().setInputCols(Array("x")).setOutputCol("features").transform(data)
    val params = new GridSpace(Array(ParamMap(learner.knob -> 1), ParamMap(learner.knob -> -1)))
    val best = fit(learner, params, "accuracy", input)

    assert(best.getBestMetric === 1.0)
    assert(best.getBestModelInfo.contains("knob: 1"))
  }
}
