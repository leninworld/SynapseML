// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.train

import org.apache.spark.ml.PipelineModel
import org.apache.spark.ml.classification.{LogisticRegression, LogisticRegressionModel, OneVsRestModel}
import org.apache.spark.ml.linalg.Vector
import org.apache.spark.sql.SparkSession

import java.nio.file.{Files, Paths}
import scala.collection.mutable.ArrayBuffer

/** Diagnostic-only producer/consumer. Never included in the contribution PR. */
object LegacyClassifier555 {
  def main(args: Array[String]): Unit = {
    require(args.length == 2)
    val mode = args(0)
    val root = Paths.get(args(1))
    Files.createDirectories(root)
    val spark = SparkSession.builder().master("local[2]").appName("Classifier555Compatibility")
      .config("spark.sql.shuffle.partitions", "2").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.synapseml.legacy.allowUnsafeJavaDeserialization", "true")
    try {
      val binary = spark.createDataFrame(Seq(
        (0.0, -2.0, 1.0, 1.0), (0.0, -1.0, 2.0, 1.0), (0.0, 0.0, 3.0, 1.0),
        (1.0, -1.0, 1.0, 9.0), (1.0, 1.0, 2.0, 1.0), (1.0, 2.0, 3.0, 1.0)))
        .toDF("Label", "x", "abel", "weight")
      val multi = spark.createDataFrame(Seq(
        ("a", -2.0, 1.0), ("a", -1.0, 2.0), ("a", 0.0, 1.0),
        ("b", -1.0, 8.0), ("b", 1.0, 1.0), ("c", 0.0, 1.0), ("c", 2.0, 3.0)))
        .toDF("target", "x", "weight")
      def lr: LogisticRegression = new LogisticRegression().setMaxIter(8).setRegParam(0.2)
      def stage: TrainClassifier = new TrainClassifier().setLabelCol("Label").setReindexLabel(false)
        .setModel(lr.setLabelCol("Label").setWeightCol("weight"))
      val records = ArrayBuffer.empty[(String, String, String)]
      def observe(name: String)(body: => Unit): Unit = {
        try {
          body
          records += ((name, "SUCCESS", "completed"))
        } catch {
          case e: Exception =>
            val chain = Iterator.iterate(Option(e: Throwable))(_.flatMap(cause => Option(cause.getCause)))
              .take(8).takeWhile(_.isDefined).map(_.get).map { cause =>
                cause.getClass.getName + ": " + Option(cause.getMessage).getOrElse("").take(600)
              }.mkString("\n")
            records += ((name, "ERROR", chain.take(6000)))
        }
      }
      def coefficients(model: TrainedClassifierModel): Array[Array[Double]] = model.getLastStage match {
        case value: LogisticRegressionModel => Array(value.coefficients.toArray :+ value.intercept)
        case value: OneVsRestModel => value.models.map { member =>
          val logistic = member.asInstanceOf[LogisticRegressionModel]
          logistic.coefficients.toArray :+ logistic.intercept
        }
      }
      def assertEqual(a: TrainedClassifierModel, b: TrainedClassifierModel): Unit = {
        val left = coefficients(a)
        val right = coefficients(b)
        require(left.length == right.length)
        require(left.zip(right).forall { case (x, y) =>
          x.length == y.length && x.zip(y).forall { case (u, v) => u == v || math.abs(u - v) < 1e-8 }
        }, "PERSISTED_MASTER_COEFFICIENT_PARITY")
      }
      if (mode == "produce") {
        require(!stage.hasParam("ignoredCols"), "NOT_UNMODIFIED_MASTER")
        val estimator = stage
        val model = estimator.fit(binary)
        estimator.write.save(root.resolve("estimator").toString)
        model.write.save(root.resolve("model").toString)
        model.getModel.transform(binary).select("x", "prediction").orderBy("x")
          .coalesce(1).write.json(root.resolve("master-predictions").toString)
        observe("inherited-custom-label") {
          new TrainClassifier().setModel(lr.setLabelCol("Label")).fit(binary)
        }
        observe("inherited-default-label") {
          new TrainClassifier().setModel(lr).fit(binary.withColumnRenamed("Label", "label"))
        }
        observe("score-without-weight") {
          model.transform(binary.drop("Label", "weight")).collect()
        }
        observe("weighted-automatic-ovr") {
          new TrainClassifier().setLabelCol("target")
            .setModel(lr.setLabelCol("target").setWeightCol("weight")).fit(multi)
        }
        observe("exact-substring-feature") {
          val output = model.getModel.stages.head.asInstanceOf[PipelineModel].transform(binary)
          val vector = output.filter(output("x") === -1.0).filter(output("Label") === 0.0)
            .select(model.getFeaturesCol).first().getAs[Vector](0)
          require(vector.toArray.contains(2.0), "SUBSTRING_FEATURE_WAS_EXCLUDED")
        }
        Seq(false, true).foreach { reindex =>
          observe("shared-binary-" + reindex) {
            val shared = new TrainClassifier().setLabelCol("Label").setReindexLabel(reindex)
              .setInputCols(Array("x", "abel"))
              .setModel(lr.setLabelCol("Label").setWeightCol("Label")).fit(binary.drop("weight"))
            shared.write.save(root.resolve("shared-binary-" + reindex).toString)
          }
        }
        observe("shared-multiclass") {
          val shared = new TrainClassifier().setLabelCol("target")
            .setModel(lr.setLabelCol("target").setWeightCol("target")).fit(multi.drop("weight"))
          shared.write.save(root.resolve("shared-multiclass").toString)
        }
      } else {
        require(mode == "consume")
        val loaded = TrainedClassifierModel.load(root.resolve("model").toString)
        val oldPredictions = spark.read.json(root.resolve("master-predictions").toString)
        val newPredictions = loaded.getModel.transform(binary).select("x", "prediction")
        require(oldPredictions.exceptAll(newPredictions).count() == 0)
        require(newPredictions.exceptAll(oldPredictions).count() == 0)
        observe("old-model-still-requires-weight") {
          loaded.transform(binary.drop("weight")).collect()
        }
        val estimator = TrainClassifier.load(root.resolve("estimator").toString)
        require(!estimator.isDefined(estimator.getParam("ignoredCols")))
        val refitted = estimator.fit(binary)
        require(refitted.transform(binary.drop("Label", "weight")).count() == binary.count())
        Seq(false, true).foreach { reindex =>
          val old = TrainedClassifierModel.load(root.resolve("shared-binary-" + reindex).toString)
          val candidate = new TrainClassifier().setLabelCol("Label").setReindexLabel(reindex)
            .setInputCols(Array("x", "abel"))
            .setModel(lr.setLabelCol("Label").setWeightCol("Label")).fit(binary.drop("weight"))
          assertEqual(old, candidate)
        }
        val masterShared = spark.read.json(root.resolve("produce-outcomes").toString)
          .filter(org.apache.spark.sql.functions.col("case") === "shared-multiclass")
          .select("status", "detail").first()
        if (masterShared.getString(0) == "SUCCESS") {
          require(Files.exists(root.resolve("shared-multiclass")))
          val old = TrainedClassifierModel.load(root.resolve("shared-multiclass").toString)
          val candidate = new TrainClassifier().setLabelCol("target")
            .setModel(lr.setLabelCol("target").setWeightCol("target")).fit(multi.drop("weight"))
          assertEqual(old, candidate)
          records += (("multiclass-shared-weight-parity", "SUCCESS", "actual master model coefficient parity"))
        } else {
          require(masterShared.getString(0) == "ERROR")
          records += (("multiclass-shared-weight-parity", "NOT_COMPARED_MASTER_FAILED", masterShared.getString(1)))
        }
        records += (("old-estimator-refit", "SUCCESS", "ignoredCols undefined; fit and scoring completed"))
        records += (("binary-shared-weight-parity", "SUCCESS", "both reindex settings match actual master models"))
      }
      spark.createDataFrame(records.toSeq).toDF("case", "status", "detail")
        .coalesce(1).write.json(root.resolve(mode + "-outcomes").toString)
      println("LEGACY_CLASSIFIER_555_" + mode.toUpperCase + "_COMPLETE")
    } finally {
      spark.stop()
    }
  }
}
