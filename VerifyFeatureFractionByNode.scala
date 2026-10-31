// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.lightgbm

import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import com.microsoft.azure.synapse.ml.lightgbm.params.LightGBMParams
import org.apache.spark.ml.Transformer
import org.apache.spark.ml.attribute.AttributeGroup
import org.apache.spark.ml.linalg.Vectors
import org.apache.spark.ml.param.ParamMap
import org.apache.spark.ml.util.MLWritable
import org.apache.spark.sql.DataFrame

class VerifyFeatureFractionByNode extends TestBase {
  private val fraction = 0.4
  private val overrideFraction = 0.7
  private val nativeKey = "feature_fraction_bynode"
  private val aliases = Seq("colsample_bynode", "sub_feature_bynode")
  private val names = Seq("classifier", "regressor", "ranker")
  private val modes = Seq("bulk", "streaming")
  private var port = 29000

  private def learner(name: String): LightGBMParams = {
    port += 1
    val stage: LightGBMParams = name match {
      case "classifier" => new LightGBMClassifier()
      case "regressor" => new LightGBMRegressor()
      case "ranker" => new LightGBMRanker().setGroupCol("group")
    }
    stage.setNumIterations(3).setNumLeaves(3).setMinDataInLeaf(1).setMinDataPerBin(1)
      .setNumTasks(1).setNumThreads(1).setDefaultListenPort(port).setSeed(42)
  }

  private def config(stage: LightGBMParams): String = {
    val schema = new AttributeGroup("features", 4).toStructField()
    stage match {
      case classifier: LightGBMClassifier => classifier.getTrainParams(1, schema, 1).toString
      case regressor: LightGBMRegressor => regressor.getTrainParams(1, schema, 1).toString
      case ranker: LightGBMRanker => ranker.getTrainParams(1, schema, 1).toString
    }
  }

  private def values(config: String): Seq[String] = {
    config.split("\\s+").filter(_.startsWith(nativeKey + "=")).map(_.split("=", 2)(1)).toSeq
  }

  private def restore(name: String, stage: LightGBMParams, suffix: String): LightGBMParams = {
    val path = tmpDir.resolve(s"$name-$suffix").toString
    stage.write.save(path)
    name match {
      case "classifier" => LightGBMClassifier.load(path)
      case "regressor" => LightGBMRegressor.load(path)
      case "ranker" => LightGBMRanker.load(path)
    }
  }

  names.foreach { name =>
    test(s"$name defaults and clears featureFractionByNode without emitting it") {
      val stage = learner(name)
      assert(stage.getFeatureFractionByNode == 1.0)
      assert(!stage.isSet(stage.featureFractionByNode))
      assert(values(config(stage)).isEmpty)
      val copied = stage.copy(ParamMap.empty).asInstanceOf[LightGBMParams]
      val loaded = restore(name, stage, "unset")
      Seq(copied, loaded).foreach { value =>
        assert(value.getFeatureFractionByNode == 1.0)
        assert(!value.isSet(value.featureFractionByNode))
        assert(values(config(value)).isEmpty)
      }
      stage.setFeatureFractionByNode(fraction)
      stage.clear(stage.featureFractionByNode)
      assert(stage.getFeatureFractionByNode == 1.0)
      assert(values(config(stage)).isEmpty)
    }

    test(s"$name emits explicit featureFractionByNode and preserves canonical overrides") {
      val stage = learner(name).setFeatureFractionByNode(fraction)
      assert(values(config(stage)) == Seq(fraction.toString), "EXPLICIT_NODE_FRACTION_MISSING")
      Seq(stage.copy(ParamMap.empty).asInstanceOf[LightGBMParams], restore(name, stage, "set"))
        .foreach { value =>
          assert(value.getFeatureFractionByNode == fraction)
          assert(value.isSet(value.featureFractionByNode))
          assert(values(config(value)) == Seq(fraction.toString))
        }
      stage.setPassThroughArgs(s"$nativeKey=$overrideFraction")
      assert(values(config(stage)) == Seq(overrideFraction.toString))
    }

    test(s"$name preserves alias-only passThroughArgs without injecting a canonical default") {
      aliases.foreach { alias =>
        val stage = learner(name).setPassThroughArgs(s"$alias=$overrideFraction")
        assert(config(stage).contains(s"$alias=$overrideFraction"))
        assert(values(config(stage)).isEmpty)
      }
    }
  }

  private lazy val trainingData: DataFrame = {
    val rows = (0 until 48).map { index =>
      val label = (index % 2).toDouble
      (label, index / 6, Vectors.dense(label, (index % 3).toDouble, (index % 5).toDouble, index.toDouble))
    }
    spark.createDataFrame(rows).toDF("label", "group", "features").coalesce(1)
  }

  private def fit(stage: LightGBMParams): Transformer with LightGBMModelMethods = {
    val model = stage match {
      case classifier: LightGBMClassifier => classifier.fit(trainingData)
      case regressor: LightGBMRegressor => regressor.fit(trainingData)
      case ranker: LightGBMRanker => ranker.fit(trainingData)
    }
    model.asInstanceOf[Transformer with LightGBMModelMethods]
  }

  private def effectiveFraction(model: LightGBMModelMethods): Double = {
    val pattern = "(?m)^\\[feature_fraction_bynode: ([^\\]]+)\\]$".r
    val matched = pattern.findFirstMatchIn(model.getNativeModel())
    assert(matched.isDefined, "Native model must record the effective feature_fraction_bynode")
    matched.get.group(1).toDouble
  }

  for (name <- names; mode <- modes) {
    val cases = Seq("typed", "canonical") ++ aliases ++ aliases.map(alias => s"typed-$alias")
    cases.foreach { setting =>
      test(s"$name $mode trains and persists native featureFractionByNode via $setting") {
        val stage = learner(name).setDataTransferMode(mode)
        val expected = if (setting.startsWith("typed-")) None else {
          Some(if (setting == "typed") fraction else overrideFraction)
        }
        if (setting == "typed") {
          stage.setFeatureFractionByNode(fraction)
        } else if (setting == "canonical") {
          stage.setFeatureFractionByNode(fraction).setPassThroughArgs(s"$nativeKey=$overrideFraction")
        } else if (setting.startsWith("typed-")) {
          val alias = setting.stripPrefix("typed-")
          stage.setFeatureFractionByNode(fraction).setPassThroughArgs(s"$alias=$overrideFraction")
          assert(values(config(stage)) == Seq(fraction.toString))
          assert(config(stage).contains(s"$alias=$overrideFraction"))
        } else {
          stage.setPassThroughArgs(s"$setting=$overrideFraction")
        }
        val model = fit(stage)
        try {
          val observed = effectiveFraction(model)
          assert(!observed.isNaN && observed > 0 && observed <= 1)
          expected.foreach(value => assert(math.abs(observed - value) < 0.000001))
          if (expected.isEmpty) {
            info(s"$name $mode $setting native effective fraction: $observed")
          }
          assert(model.transform(trainingData).count() == trainingData.count())
          val path = tmpDir.resolve(s"model-$name-$mode-$setting").toString
          model.asInstanceOf[MLWritable].write.save(path)
          val loaded = name match {
            case "classifier" => LightGBMClassificationModel.load(path)
            case "regressor" => LightGBMRegressionModel.load(path)
            case "ranker" => LightGBMRankerModel.load(path)
          }
          try {
            assert(math.abs(effectiveFraction(loaded) - observed) < 0.000001)
            assert(loaded.transform(trainingData).collect().toSeq == model.transform(trainingData).collect().toSeq)
          } finally {
            loaded.getLightGBMBooster.freeNativeMemory()
          }
        } finally {
          model.getLightGBMBooster.freeNativeMemory()
        }
      }
    }
  }
}
