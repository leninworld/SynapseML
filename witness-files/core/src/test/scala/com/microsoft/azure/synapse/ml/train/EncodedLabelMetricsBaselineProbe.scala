// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.train

import com.microsoft.azure.synapse.ml.automl.{FindBestModel, GridSpace, TuneHyperparameters}
import com.microsoft.azure.synapse.ml.core.metrics.MetricConstants
import com.microsoft.azure.synapse.ml.core.schema.SchemaConstants
import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import com.microsoft.azure.synapse.ml.featurize.{IndexToValue, ValueIndexer, ValueIndexerModel}
import org.apache.spark.ml.{Estimator, Pipeline, PipelineModel, PipelineStage, Transformer}
import org.apache.spark.ml.classification.{LogisticRegression, RandomForestClassifier}
import org.apache.spark.ml.evaluation.{BinaryClassificationEvaluator, MulticlassClassificationEvaluator}
import org.apache.spark.ml.feature.StringIndexer
import org.apache.spark.ml.linalg.{SQLDataTypes, Vector, Vectors}
import org.apache.spark.ml.param.ParamMap
import org.apache.spark.sql.{DataFrame, Row}
import org.apache.spark.sql.functions.{col, lit, udf}
import org.apache.spark.sql.types._

import java.nio.file.Files
import scala.collection.JavaConverters._

/** Private G0 witnesses: original production source, real emitted metadata. */
class EncodedLabelMetricsBaselineProbe extends TestBase {
  private def causes(error: Throwable): Iterator[Throwable] =
    Iterator.iterate(error)(_.getCause).takeWhile(_ != null)

  private def encodingCause(error: Throwable): Boolean = causes(error).exists { t =>
    (t.isInstanceOf[NoSuchElementException] && t.getStackTrace.exists(
      _.getClassName.startsWith("com.microsoft.azure.synapse.ml.train.ComputeModelStatistics"))) ||
      Option(t.getMessage).exists(m => m.contains("key not found") && m.contains("ComputeModelStatistics"))
  }

  private def regression[T](body: => T): T =
    try body catch {
      case error: Throwable if encodingCause(error) =>
        fail("CMS_LABEL_ENCODING_REGRESSION product cause: " + error, error)
    }

  private val variants: Seq[(String, DataType, Seq[Any])] = Seq(
    ("int", IntegerType, Seq[Any](24, 85)),
    ("long", LongType, Seq[Any](10001L, 30001L)),
    ("double", DoubleType, Seq[Any](0.25, 1.75)),
    ("string", StringType, Seq[Any]("negative", "positive")),
    ("boolean", BooleanType, Seq[Any](false, true)),
    ("multiclass", IntegerType, Seq[Any](10, 20, 30)))

  private def frame(dataType: DataType, levels: Seq[Any]): DataFrame = {
    val rows = (0 until levels.length * 24).map { i =>
      val index = i % levels.length
      val features = Vectors.dense(Array.tabulate(levels.length) { j =>
        if (j == index) 1.0 else 0.0
      })
      Row(levels(index), features)
    }
    val schema = StructType(Seq(
      StructField("label", dataType, nullable = false),
      StructField("features", SQLDataTypes.VectorType, nullable = false)))
    spark.createDataFrame(rows.asJava, schema)
  }

  private def lr(): LogisticRegression = new LogisticRegression()
    .setMaxIter(12).setRegParam(0.01).setTol(1e-6)

  private def index(data: DataFrame): DataFrame =
    new ValueIndexer().setInputCol("label").setOutputCol("label").fit(data).transform(data)

  private def cms(metric: String): ComputeModelStatistics = new ComputeModelStatistics()
    .setLabelCol("label").setScoredLabelsCol("prediction")
    .setScoresCol("rawPrediction").setEvaluationMetric(metric)

  private def sparkAccuracy(scored: DataFrame): Double =
    new MulticlassClassificationEvaluator().setMetricName("accuracy").evaluate(scored)

  private def assertAccuracy(scored: DataFrame): Unit = {
    val expected = sparkAccuracy(scored)
    val actual = cms(MetricConstants.AccuracySparkMetric).transform(scored)
      .first().getAs[Double](MetricConstants.AccuracyColumnName)
    assert(math.abs(actual - expected) < 1e-10,
      s"CMS_LABEL_ENCODING_REGRESSION actual=$actual expected=$expected")
  }

  variants.foreach { case (name, dataType, levels) =>
    test(s"indexed $name labels retain their classifier indices") {
      regression {
        val data = index(frame(dataType, levels))
        assertAccuracy(lr().fit(data).transform(data))
      }
    }
  }

  test("indexed subset with overlapping original values does not silently remap") {
    regression {
      val data = index(frame(IntegerType, Seq[Any](1, 2)))
      val scored = lr().fit(data).transform(data).filter(col("label") === lit(1))
      assert(scored.count() > 0)
      assertAccuracy(scored)
    }
  }

  for ((display, metric, column) <- Seq(
    ("AUC", MetricConstants.AucSparkMetric, MetricConstants.AucColumnName),
    ("areaUnderPR", MetricConstants.AreaUnderPRMetric, MetricConstants.AreaUnderPRColumnName));
       sparse <- Seq(false, true)) {
    test(s"indexed labels preserve $display with sparse=$sparse scores") {
      regression {
        val data = index(frame(IntegerType, Seq[Any](24, 85)))
        val dense = lr().fit(data).transform(data)
        val toSparse = udf((v: Vector) => (v.toSparse: Vector))
        val scored = if (sparse) dense.withColumn("rawPrediction", toSparse(col("rawPrediction"))) else dense
        val sparkMetric = if (display == "AUC") "areaUnderROC" else "areaUnderPR"
        val expected = new BinaryClassificationEvaluator().setMetricName(sparkMetric).evaluate(scored)
        val actual = cms(metric).transform(scored).first().getAs[Double](column)
        assert(math.abs(actual - expected) < 1e-10,
          s"CMS_LABEL_ENCODING_REGRESSION metric=$metric actual=$actual expected=$expected")
      }
    }
  }

  test("TrainClassifier preserves labels that were already indexed") {
    regression {
      val data = index(frame(IntegerType, Seq[Any](24, 85)))
      val model = new TrainClassifier().setModel(lr()).setLabelCol("label")
        .setFeaturesCol("mlfeatures").setNumFeatures(32).fit(data)
      assertAccuracy(model.transform(data))
    }
  }

  test("TrainClassifier explicit levels with reindexLabel false use encoded labels") {
    regression {
      val data = frame(IntegerType, Seq[Any](0, 1))
      val model = new TrainClassifier().setModel(lr()).setLabelCol("label")
        .setFeaturesCol("mlfeatures").setNumFeatures(32)
        .setReindexLabel(false).setLabels(Array("negative", "positive")).fit(data)
      assertAccuracy(model.transform(data))
    }
  }

  for (kind <- Seq("LR", "RF")) {
    test(s"TuneHyperparameters $kind retains indexed labels across folds") {
      regression {
        val data = index(frame(IntegerType, Seq[Any](24, 85)))
        val estimator: Estimator[_] = if (kind == "LR") lr() else
          new RandomForestClassifier().setNumTrees(3).setMaxDepth(2).setSeed(11L)
        val model = new TuneHyperparameters().setModels(Array[Estimator[_]](estimator))
          .setParamSpace(new GridSpace(Array(new ParamMap())))
          .setNumFolds(2).setNumRuns(1).setParallelism(1).setSeed(11L)
          .setEvaluationMetric(MetricConstants.AccuracySparkMetric).fit(data)
        assert(!model.getBestMetric.isNaN && model.getBestMetric >= 0.0 && model.getBestMetric <= 1.0)
      }
    }
  }

  test("FindBestModel preserves preindexed TrainClassifier evaluation") {
    regression {
      val data = index(frame(IntegerType, Seq[Any](24, 85)))
      val model = new TrainClassifier().setModel(lr()).setLabelCol("label")
        .setFeaturesCol("mlfeatures").setNumFeatures(32).fit(data)
      val best = new FindBestModel().setModels(Array[Transformer](model))
        .setEvaluationMetric(MetricConstants.AccuracySparkMetric).fit(data)
      assert(best.getBestModelMetrics.count() == 1)
    }
  }

  test("persisted Pipeline retains indexer classifier and evaluator metadata") {
    regression {
      val data = frame(IntegerType, Seq[Any](24, 85))
      val classifier = lr().setLabelCol("label").setFeaturesCol("features")
      val stages = Array[PipelineStage](
        new ValueIndexer().setInputCol("label").setOutputCol("label"),
        classifier, cms(MetricConstants.AccuracySparkMetric))
      val model = new Pipeline().setStages(stages).fit(data)
      val path = Files.createTempDirectory("indexed-cms-pipeline-").resolve("model").toString
      model.write.overwrite().save(path)
      val result = PipelineModel.load(path).transform(data)
      assert(math.abs(result.first().getAs[Double](MetricConstants.AccuracyColumnName) - 1.0) < 1e-10,
        "CMS_LABEL_ENCODING_REGRESSION persisted pipeline")
    }
  }

  test("persisted TrainClassifier model and evaluator retain encoded label metadata") {
    regression {
      val data = index(frame(IntegerType, Seq[Any](24, 85)))
      val model = new TrainClassifier().setModel(lr()).setLabelCol("label")
        .setFeaturesCol("mlfeatures").setNumFeatures(32).fit(data)
      val root = Files.createTempDirectory("indexed-cms-trained-")
      model.write.overwrite().save(root.resolve("model").toString)
      cms(MetricConstants.AccuracySparkMetric).write.overwrite().save(root.resolve("cms").toString)
      val scored = TrainedClassifierModel.load(root.resolve("model").toString).transform(data)
      val actual = ComputeModelStatistics.load(root.resolve("cms").toString).transform(scored)
        .first().getAs[Double](MetricConstants.AccuracyColumnName)
      val expected = sparkAccuracy(scored)
      assert(math.abs(actual - expected) < 1e-10,
        s"CMS_LABEL_ENCODING_REGRESSION persisted TrainClassifier actual=$actual expected=$expected")
    }
  }

  test("identity indexed labels remain a valid control") {
    val data = index(frame(IntegerType, Seq[Any](0, 1)))
    assertAccuracy(lr().fit(data).transform(data))
  }

  test("copied real indexed metadata preserves the label convention") {
    regression {
      val data = index(frame(IntegerType, Seq[Any](24, 85)))
      val scored = lr().fit(data).transform(data)
      val copied = scored.withColumn("label", col("label").as("label", scored.schema("label").metadata))
      assertAccuracy(copied)
    }
  }

  test("stripped label metadata retains numeric classifier indices") {
    val data = index(frame(IntegerType, Seq[Any](24, 85)))
    val scored = lr().fit(data).transform(data)
    val strippedSchema = StructType(scored.schema.fields.map { field =>
      if (field.name == "label") field.copy(metadata = Metadata.empty) else field
    })
    val stripped = spark.createDataFrame(scored.rdd, strippedSchema)
    assert(stripped.schema("label").metadata == Metadata.empty)
    assertAccuracy(stripped)
  }

  test("MML-only ambiguous nonidentity metadata is rejected") {
    val data = index(frame(IntegerType, Seq[Any](24, 85)))
    val scored = lr().fit(data).transform(data)
    val metadata = new MetadataBuilder().withMetadata(scored.schema("label").metadata)
      .remove("ml_attr").build()
    val ambiguousSchema = StructType(scored.schema.fields.map { field =>
      if (field.name == "label") field.copy(metadata = metadata) else field
    })
    val ambiguous = spark.createDataFrame(scored.rdd, ambiguousSchema)
    assert(!ambiguous.schema("label").metadata.contains("ml_attr") &&
      ambiguous.schema("label").metadata.contains(SchemaConstants.MMLTag))
    val error = intercept[Exception] {
      cms(MetricConstants.AccuracySparkMetric).transform(ambiguous).collect()
    }
    assert(encodingCause(error) || causes(error).exists(t => t.isInstanceOf[IllegalArgumentException] &&
      Option(t.getMessage).exists(_.contains("ComputeModelStatistics"))),
      "Rejection did not come from ComputeModelStatistics: " + error)
  }

  variants.filter(_._1 != "multiclass").foreach { case (name, dataType, levels) =>
    test(s"raw $name TrainClassifier labels remain a valid control") {
      val data = frame(dataType, levels)
      val model = new TrainClassifier().setModel(lr()).setLabelCol("label")
        .setFeaturesCol("mlfeatures").setNumFeatures(32).fit(data)
      val scored = model.transform(data)
      val encoding = model.getLevels.zipWithIndex.toMap
      val rows = scored.select("label", "prediction").collect()
      val expected = rows.count(r => encoding(r(0)).toDouble == r.getDouble(1)).toDouble / rows.length
      val actual = cms(MetricConstants.AccuracySparkMetric).transform(scored)
      .first().getAs[Double](MetricConstants.AccuracyColumnName)
      assert(math.abs(actual - expected) < 1e-10)
    }
  }

  test("Spark StringIndexer without MML levels remains a valid control") {
    val data = frame(StringType, Seq[Any]("negative", "positive"))
      .withColumnRenamed("label", "raw")
    val indexed = new StringIndexer().setInputCol("raw").setOutputCol("label").fit(data).transform(data)
      .drop("raw")
    assertAccuracy(lr().fit(indexed).transform(indexed))
  }

  test("ValueIndexer persistence and IndexToValue preserve the original levels") {
    val data = frame(IntegerType, Seq[Any](24, 85))
    val model = new ValueIndexer().setInputCol("label").setOutputCol("label").fit(data)
    val path = Files.createTempDirectory("indexed-cms-indexer-").resolve("model").toString
    model.write.overwrite().save(path)
    val decoded = new IndexToValue().setInputCol("label").setOutputCol("decoded")
      .transform(ValueIndexerModel.load(path).transform(data))
    assert(decoded.schema("decoded").dataType == IntegerType)
    assert(decoded.select("decoded").collect().map(_.getInt(0)).sorted.sameElements(
      data.select("label").collect().map(_.getInt(0)).sorted))
  }

  test("unknown produced indices are rejected rather than silently counted") {
    val known = frame(IntegerType, Seq[Any](24, 85))
    val indexer = new ValueIndexer().setInputCol("label").setOutputCol("label").fit(known)
    val unseen = indexer.transform(frame(IntegerType, Seq[Any](99)))
      .withColumn("prediction", lit(0.0))
    val error = intercept[Exception] {
      cms(MetricConstants.AccuracySparkMetric).transform(unseen).collect()
    }
    assert(encodingCause(error) || causes(error).exists(t => t.isInstanceOf[IllegalArgumentException] &&
      Option(t.getMessage).exists(_.contains("ComputeModelStatistics"))),
      "Rejection did not come from ComputeModelStatistics: " + error)
  }
}
