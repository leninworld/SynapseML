// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.automl

import com.microsoft.azure.synapse.ml.param.ByteArrayParam
import com.microsoft.azure.synapse.ml.train.{TrainClassifier, TrainedClassifierModel}
import org.apache.spark.ml.Estimator
import org.apache.spark.ml.classification.{ProbabilisticClassificationModel, ProbabilisticClassifier}
import org.apache.spark.ml.regression.{RegressionModel, Regressor}
import org.apache.spark.ml.linalg.{Vector, Vectors}
import org.apache.spark.ml.param.{IntParam, Param, ParamMap}
import org.apache.spark.ml.util.{DefaultParamsReadable, DefaultParamsWritable, Identifiable}
import org.apache.spark.sql.Dataset

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** The fit record is independent of the reporting helper and remains on the driver. */
private[automl] object ModelInfoFitRecord {
  val applied = new ConcurrentHashMap[String, Int]()
  val overriddenFits = new AtomicInteger()
}

class TestableModelInfoClassifier(override val uid: String)
  extends ProbabilisticClassifier[Vector, TestableModelInfoClassifier, TestableModelInfoClassificationModel]
    with DefaultParamsWritable {
  def this() = this(Identifiable.randomUID("modelInfoClassifier"))

  val knob = new IntParam(this, "knob", "A training-only setting absent from the fitted model")
  val buffer = new ByteArrayParam(this, "buffer", "Synthetic training buffer")
  val note = new Param[String](this, "note", "Synthetic scalar rendering control")
  val unrenderable = new Param[String](this, "unrenderable", "Synthetic encoder-failure control") {
    override def jsonEncode(value: String): String = throw new IllegalArgumentException("synthetic encoder failure")
  }
  val quality = new IntParam(this, "quality", "A second independently sampled training setting")
  val legacyFormattingFailure = new IntParam(this, "legacyFormattingFailure", "Synthetic fitted-format control")
  setDefault(knob -> 0, quality -> 1, legacyFormattingFailure -> 0)

  override protected def train(dataset: Dataset[_]): TestableModelInfoClassificationModel = {
    ModelInfoFitRecord.applied.put(uid, $(knob))
    val fitted = new TestableModelInfoClassificationModel(uid).setOrientation($(knob) * $(quality))
    if ($(legacyFormattingFailure) != 0) {
      fitted.set(fitted.legacyDisplay, new ModelInfoOpaqueValue())
    }
    fitted
  }

  override def copy(extra: ParamMap): TestableModelInfoClassifier = defaultCopy(extra)
}

object TestableModelInfoClassifier extends DefaultParamsReadable[TestableModelInfoClassifier]

class ModelInfoOpaqueValue extends Serializable {
  override def toString: String = throw new IllegalArgumentException("synthetic fitted formatting failure")
}

class TestableModelInfoClassificationModel(override val uid: String)
  extends ProbabilisticClassificationModel[Vector, TestableModelInfoClassificationModel] with DefaultParamsWritable {
  def this() = this(Identifiable.randomUID("modelInfoClassificationModel"))

  val legacyDisplay = new Param[ModelInfoOpaqueValue](this, "legacyDisplay", "Synthetic legacy-format control")
  val orientation = new IntParam(this, "orientation", "Prediction behavior produced by training")
  def getOrientation: Int = $(orientation)
  def setOrientation(value: Int): this.type = set(orientation, value)

  override val numClasses: Int = 2
  override val numFeatures: Int = 1

  override def predictRaw(features: Vector): Vector = {
    val positive = features(0) * getOrientation > 0.0
    if (positive) Vectors.dense(0.0, 1.0) else Vectors.dense(1.0, 0.0)
  }

  override protected def raw2probabilityInPlace(rawPrediction: Vector): Vector = rawPrediction

  override def copy(extra: ParamMap): TestableModelInfoClassificationModel = defaultCopy(extra)
}

object TestableModelInfoClassificationModel extends DefaultParamsReadable[TestableModelInfoClassificationModel]

class TestableOverridingModelInfoClassifier(uid: String) extends TestableModelInfoClassifier(uid) {
  def this() = this(Identifiable.randomUID("overridingModelInfoClassifier"))

  override def fit(dataset: Dataset[_], params: ParamMap): TestableModelInfoClassificationModel = {
    ModelInfoFitRecord.overriddenFits.incrementAndGet()
    val model = super.fit(dataset, params)
    model.setOrientation(-model.getOrientation)
  }
}

class TestableModelInfoRegressor(override val uid: String)
  extends Regressor[Vector, TestableModelInfoRegressor, TestableModelInfoRegressionModel] with DefaultParamsWritable {
  def this() = this(Identifiable.randomUID("modelInfoRegressor"))
  val knob = new IntParam(this, "knob", "Training-only prediction direction")
  setDefault(knob -> 0)

  override protected def train(dataset: Dataset[_]): TestableModelInfoRegressionModel = {
    ModelInfoFitRecord.applied.put(uid, $(knob))
    new TestableModelInfoRegressionModel(uid).setOrientation($(knob))
  }

  override def copy(extra: ParamMap): TestableModelInfoRegressor = defaultCopy(extra)
}

object TestableModelInfoRegressor extends DefaultParamsReadable[TestableModelInfoRegressor]

class TestableModelInfoRegressionModel(override val uid: String)
  extends RegressionModel[Vector, TestableModelInfoRegressionModel] with DefaultParamsWritable {
  def this() = this(Identifiable.randomUID("modelInfoRegressionModel"))
  val orientation = new IntParam(this, "orientation", "Prediction direction produced by fitting")
  def setOrientation(value: Int): this.type = set(orientation, value)
  override val numFeatures: Int = 1
  override def predict(features: Vector): Double = features(0) * $(orientation)
  override def copy(extra: ParamMap): TestableModelInfoRegressionModel = defaultCopy(extra)
}

object TestableModelInfoRegressionModel extends DefaultParamsReadable[TestableModelInfoRegressionModel]

class TestableOverridingModelInfoTrainer(uid: String) extends TrainClassifier(uid) {
  def this() = this(Identifiable.randomUID("overridingModelInfoTrainer"))

  override def fit(dataset: Dataset[_], params: ParamMap): TrainedClassifierModel = {
    ModelInfoFitRecord.overriddenFits.incrementAndGet()
    val result = super.fit(dataset, params)
    val last = result.getLastStage.asInstanceOf[TestableModelInfoClassificationModel]
    last.setOrientation(-last.getOrientation)
    result
  }
}

trait ModelInfoParamFitOverride extends Estimator[TestableModelInfoClassificationModel] {
  abstract override def fit(dataset: Dataset[_], params: ParamMap): TestableModelInfoClassificationModel = {
    super.fit(dataset, params)
  }
}

class TestableTraitOverridingModelInfoClassifier(uid: String)
  extends TestableModelInfoClassifier(uid) with ModelInfoParamFitOverride {
  def this() = this(Identifiable.randomUID("traitOverridingModelInfoClassifier"))
}

