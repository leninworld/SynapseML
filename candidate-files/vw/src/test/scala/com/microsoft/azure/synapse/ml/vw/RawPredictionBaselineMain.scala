// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.vw

// scalastyle:off method.length
// scalastyle:off cyclomatic.complexity

import com.microsoft.azure.synapse.ml.core.test.base.SparkSessionManagement
import org.apache.spark.ml.linalg.Vectors
import org.apache.spark.ml.param.ParamMap
import org.apache.spark.sql.DataFrame
import org.apache.spark.sql.functions.col
import org.vowpalwabbit.spark.VowpalWabbitNative
import spray.json._

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Paths, StandardOpenOption}

/** Private diagnostic main, not a submitted upstream test or product change. */
object RawPredictionBaselineMain extends SparkSessionManagement {
  private def strings(values: Seq[String]): JsArray = JsArray(values.map(JsString(_)).toVector)

  private def nativeModel(args: String, examples: Seq[String]): Array[Byte] = {
    val learner = new VowpalWabbitNative(args + " --quiet --holdout_off")
    try {
      (0 until 3).foreach { _ => examples.foreach(learner.learnFromString) }
      learner.getModel
    } finally {
      learner.close()
    }
  }

  private def nativeScores(): Vector[JsValue] = {
    Seq("--indexing 0", "--indexing 1", "").flatMap { indexing =>
      val offset = if (indexing.endsWith("0")) 0 else 1
      val examples = (0 until 3).map(i => s"${i + offset} |f x:${i + 1}")
      val args = s"--oaa 3 --loss_function logistic $indexing"
      val trained = nativeModel(args, examples)
      val empty = new VowpalWabbitNative(args + " --quiet")
      val tied = try { empty.getModel } finally { empty.close() }
      Seq("trained" -> trained, "tie" -> tied).flatMap { case (kind, bytes) =>
        Seq("|f x:1", "|f x:0", "|f x:nan").map { input =>
          val plain = new VowpalWabbitNative("--testonly --quiet", bytes)
          val scored = new VowpalWabbitNative("--testonly --quiet --scores", bytes)
          val probs = new VowpalWabbitNative("--testonly --quiet --probabilities", bytes)
          try {
            val winner = plain.predictFromString(input).asInstanceOf[java.lang.Integer].intValue()
            val scores = scored.predictFromString(input).asInstanceOf[Array[Float]]
            val probabilities = probs.predictFromString(input).asInstanceOf[Array[Float]]
            require(scores.length == 3 && probabilities.length == 3)
            var strictWinner = 0
            (1 until scores.length).foreach { i =>
              if (scores(i) > scores(strictWinner)) { strictWinner = i }
            }
            val sparkWinner = Vectors.dense(scores.map(_.toDouble)).argmax
            JsObject("indexing" -> JsString(indexing), "kind" -> JsString(kind),
              "input" -> JsString(input), "status" -> JsString("observed"),
              "nativeWinner" -> JsNumber(winner), "strictWinner" -> JsNumber(strictWinner + offset),
              "sparkWinner" -> JsNumber(sparkWinner + offset),
              "strictMatches" -> JsBoolean(strictWinner + offset == winner),
              "sparkMatches" -> JsBoolean(sparkWinner + offset == winner),
              "scores" -> strings(scores.toSeq.map(_.toString)),
              "probabilities" -> strings(probabilities.toSeq.map(_.toString)),
              "scoredType" -> JsString(scored.getOutputPredictionType),
              "arguments" -> JsString(scored.getArguments.getArgs))
          } catch {
            case error: Exception => JsObject("indexing" -> JsString(indexing),
              "kind" -> JsString(kind), "input" -> JsString(input),
              "status" -> JsString("error"), "error" -> JsString(error.toString.take(4096)))
          } finally {
            plain.close()
            scored.close()
            probs.close()
          }
        }
      }
    }.toVector
  }

  private def snapshot(name: String, model: VowpalWabbitClassificationModel,
                       data: DataFrame): JsObject = {
    val before = data.schema.json
    try {
      val declared = model.transformSchema(data.schema).json
      val actual = model.transform(data)
      val rows = actual.orderBy("id").toJSON.collect().toSeq
      require(data.schema.json == before, "Input schema mutated")
      JsObject("name" -> JsString(name), "status" -> JsString("observed"),
        "inputSchema" -> before.parseJson, "declaredSchema" -> declared.parseJson,
        "actualSchema" -> actual.schema.json.parseJson, "rows" -> strings(rows),
        "rawColumn" -> JsString(model.getRawPredictionCol),
        "probabilityColumn" -> JsString(model.getProbabilityCol),
        "predictionColumn" -> JsString(model.getPredictionCol),
        "nativeType" -> JsString(model.vw.getOutputPredictionType),
        "arguments" -> JsString(model.vwArgs.getArgs))
    } catch {
      case error: Exception =>
        require(data.schema.json == before, "Input schema mutated on failure")
        JsObject("name" -> JsString(name), "status" -> JsString("error"),
          "inputSchema" -> before.parseJson, "error" -> JsString(error.toString.take(4096)))
    }
  }

  def main(args: Array[String]): Unit = {
    require(sys.env.contains("VALIDATION_LANE") && args.length == 1, "Owned Docker only")
    val output = Paths.get(args(0))
    require(output.isAbsolute && output.startsWith(Paths.get("/runtime")), "Evidence path")
    val session = getSession("RawPredictionBaseline643", numCores = Some(2))
    try {
      import session.implicits._
      val binary = Seq((0L, 0.0, Vectors.dense(1.0, 0.0)),
        (1L, 1.0, Vectors.dense(0.0, 1.0))).toDF("id", "label", "features").coalesce(1)
      val multi = Seq((0L, 0.0, Vectors.dense(1.0, 0.0)),
        (1L, 1.0, Vectors.dense(0.0, 1.0)),
        (2L, 2.0, Vectors.dense(1.0, 1.0))).toDF("id", "label", "features").coalesce(1)
      val records = scala.collection.mutable.ArrayBuffer.empty[JsValue]
      def inspect(name: String, model: VowpalWabbitClassificationModel, data: DataFrame): Unit = {
        records += snapshot(name, model, data)
        records += snapshot(name + "-custom", model.copy(ParamMap.empty)
          .setRawPredictionCol("rawCustom").setProbabilityCol("probCustom")
          .setPredictionCol("predCustom"), data)
        records += snapshot(name + "-no-raw", model.copy(ParamMap.empty).setRawPredictionCol(""), data)
        records += snapshot(name + "-no-probability", model.copy(ParamMap.empty).setProbabilityCol(""), data)
        records += snapshot(name + "-no-prediction", model.copy(ParamMap.empty).setPredictionCol(""), data)
        val saved = "/runtime/saved-" + sys.env.getOrElse("VALIDATION_SOURCE_KIND", "baseline") + "-" + name
        model.write.save(saved)
        records += snapshot(name + "-reloaded", VowpalWabbitClassificationModel.load(saved), data)
      }
      Seq("unlinked" -> "", "linked-space" -> " --link logistic",
        "linked-equals" -> " --link=logistic").foreach { case (name, link) =>
        val model = new VowpalWabbitClassifier().setLabelConversion(true)
          .setUseBarrierExecutionMode(false).setNumPasses(2)
          .setPassThroughArgs("--quiet --holdout_off --loss_function logistic" + link).fit(binary)
        inspect(name, model, binary)
      }
      Seq("zero" -> "--indexing 0", "one" -> "--indexing 1", "unspecified" -> "").foreach {
        case (name, indexing) =>
          val data = if (name == "zero") multi else multi.withColumn("label", col("label") + 1)
          val model = new VowpalWabbitClassifier().setNumClasses(3).setNumPasses(2)
            .setUseBarrierExecutionMode(false)
            .setPassThroughArgs(s"--oaa 3 --quiet --holdout_off --loss_function logistic $indexing").fit(data)
          inspect("oaa-" + name, model, data)
          inspect("scores-" + name, model.copy(ParamMap.empty).setTestArgs("--scores"), data)
          inspect("probabilities-" + name, model.copy(ParamMap.empty).setTestArgs("--probabilities"), data)
      }
      Seq("csoaa" -> Seq("1:0 2:1 3:1 |f x:1", "1:1 2:0 3:1 |f x:2"),
        "ect" -> Seq("1 |f x:1", "2 |f x:2", "3 |f x:3")).foreach { case (mode, examples) =>
        val bytes = nativeModel(s"--$mode 3", examples)
        val model = new VowpalWabbitClassificationModel().setNumClassesModel(3)
          .setModel(bytes).setAdditionalFeatures(Array.empty[String])
        inspect(mode + "-native-model", model, multi)
      }
      val native = nativeScores()
      val result = JsObject("kind" -> JsString(sys.env.getOrElse("VALIDATION_SOURCE_KIND", "unmodified-master-baseline-only")),
        "source" -> JsString("36dfb77160ec99ea2bf43c7fae5decaf63ee7396"),
        "modes" -> JsArray(records.toVector), "native" -> JsArray(native))
      Files.write(output, result.prettyPrint.getBytes(StandardCharsets.UTF_8), StandardOpenOption.CREATE_NEW)
      require(records.exists {
        case obj: JsObject => obj.fields.get("name").contains(JsString("unlinked")) &&
          obj.fields.get("status").contains(JsString("observed"))
        case _ => false
      }, "No real binary model observation")
      println("RAW643_BASELINE_RECORDED " + output)
    } finally {
      session.stop()
    }
  }
}
