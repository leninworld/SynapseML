// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.vw

import com.microsoft.azure.synapse.ml.codegen.Wrappable
import com.microsoft.azure.synapse.ml.core.schema.DatasetExtensions._
import com.microsoft.azure.synapse.ml.logging.{FeatureNames, SynapseMLLogging}
import org.apache.spark.ml.attribute.{AttributeGroup, NominalAttribute}
import org.apache.spark.ml.classification.{ProbabilisticClassificationModel, ProbabilisticClassifier}
import org.apache.spark.ml.linalg.{Vector, Vectors}
import org.apache.spark.ml.param._
import org.apache.spark.ml.param.shared.HasWeightCol
import org.apache.spark.ml.util._
import org.apache.spark.ml.{ComplexParamsReadable, ComplexParamsWritable}
import org.apache.spark.sql._
import org.apache.spark.sql.functions.{col, udf}
import org.apache.spark.sql.types.{DoubleType, Metadata, StringType, StructType}
import org.vowpalwabbit.spark.VowpalWabbitExample

import scala.math.exp

/**
  * VowpalWabbit exposed as SparkML classifier.
  */
class VowpalWabbitClassifier(override val uid: String)
  extends ProbabilisticClassifier[Row, VowpalWabbitClassifier, VowpalWabbitClassificationModel]
    with VowpalWabbitBaseSpark
    with ComplexParamsWritable
    with SynapseMLLogging {
  logClass(FeatureNames.VowpalWabbit)

  override protected lazy val pyInternalWrapper = true

  def this() = this(Identifiable.randomUID("VowpalWabbitClassifier"))

  // to support Grid search we need to replicate the parameters here...
  val labelConversion = new BooleanParam(this, "labelConversion",
    "Convert 0/1 Spark ML style labels to -1/1 VW style labels. Defaults to false.")
  setDefault(labelConversion -> false)
  def getLabelConversion: Boolean = $(labelConversion)
  def setLabelConversion(value: Boolean): this.type = set(labelConversion, value)

  val numClasses = new IntParam(this, "numClasses",
    "Number of classes. Defaults to binary. Needs to match oaa/csoaa/multilabel_oaa/...")
  setDefault(numClasses -> 2)
  def getNumClasses: Int = $(numClasses)
  def setNumClasses(value: Int): this.type = set(numClasses, value)

  override protected def createLabelSetter(schema: StructType): (Row, VowpalWabbitExample) => Unit = {
    if (getNumClasses == 2)
      super.createLabelSetter(schema)
    else {
      val labelColIdx = schema.fieldIndex(getLabelCol)

      val weightGetter = getWeightGetter(schema)

      // for Predictors the label is always going to be a Double
      (row: Row, ex: VowpalWabbitExample) =>
        ex.setMulticlassLabel(weightGetter(row), row.getDouble(labelColIdx).toInt)
    }
  }

  override protected def train(dataset: Dataset[_]): VowpalWabbitClassificationModel = {
    logFit({
      val model = new VowpalWabbitClassificationModel(uid)
        .setFeaturesCol(getFeaturesCol)
        .setAdditionalFeatures(getAdditionalFeatures)
        .setPredictionCol(getPredictionCol)
        .setProbabilityCol(getProbabilityCol)
        .setRawPredictionCol(getRawPredictionCol)
        .setNumClassesModel(getNumClasses)

      val finalDataset = if (!getLabelConversion)
        dataset.toDF
      else {
        val inputLabelCol = dataset.withDerivativeCol("label")
        dataset
          .withColumnRenamed(getLabelCol, inputLabelCol)
          .withColumn(getLabelCol, col(inputLabelCol) * 2 - 1)
          .toDF
      }

      trainInternal(finalDataset, model)
    }, dataset.columns.length)
  }

  override def copy(extra: ParamMap): this.type = defaultCopy(extra)
}

object VowpalWabbitClassifier extends ComplexParamsReadable[VowpalWabbitClassifier]

class VowpalWabbitClassificationModel(override val uid: String)
  extends ProbabilisticClassificationModel[Row, VowpalWabbitClassificationModel]
    with VowpalWabbitBaseModelSpark
    with ComplexParamsWritable with Wrappable with SynapseMLLogging {
  logClass(FeatureNames.VowpalWabbit)

  def this() = this(Identifiable.randomUID("VowpalWabbitClassificationModel"))

  override protected lazy val pyInternalWrapper = true

  // need to name differently from numClasses
  val numClassesModel = new IntParam(this, "numClassesModel",
    "Number of classes.")

  def getNumClassesModel: Int = $(numClassesModel)
  def setNumClassesModel(value: Int): this.type = set(numClassesModel, value)

  def numClasses: Int = getNumClassesModel

  private def argumentValue(option: String): Option[String] = {
    val tokens = vwArgs.getArgs.trim.split("\\s+").flatMap(_.split("=", 2))
    tokens.sliding(2).collect {
      case Array(name, value) if name == option => value
    }.toSeq.lastOption
  }

  private def hasClassScores: Boolean =
    vw.getOutputPredictionType == "prediction_type_t::scalars"

  private def winnerIndexBase: Int = {
    // ECT labels stay one-based; its reduction does not use --indexing.
    if (argumentValue("--ect").isDefined) 1
    else if (argumentValue("--indexing").contains("0")) 0
    else 1
  }

  private def predictionMetadata: Metadata = {
    val extra = if (getNumClassesModel != 2 && !hasClassScores && winnerIndexBase == 1) 1 else 0
    NominalAttribute.defaultAttr.withNumValues(getNumClassesModel + extra).toMetadata()
  }

  private def outputMetadata(name: String): Metadata = {
    if (name == getPredictionCol) predictionMetadata
    else new AttributeGroup(name, getNumClassesModel).toMetadata()
  }

  override def transformSchema(schema: StructType): StructType = {
    val declared = super.transformSchema(schema)
    val winnerOnly = getNumClassesModel != 2 && !hasClassScores
    StructType(declared.fields.filterNot(field =>
      winnerOnly && getProbabilityCol.nonEmpty && field.name == getProbabilityCol).map { field =>
      if (getPredictionCol.nonEmpty && field.name == getPredictionCol) field.copy(metadata = predictionMetadata)
      else field
    })
  }

  /** Raw output is a vector with numClasses entries. Unlinked binary values are
    * [-margin, margin]; logistic-linked binary values are [1-p, p]. Other links
    * keep their existing sigmoid probability conversion. Multiclass scalars
    * retain native values (including scores in the existing probability column).
    * Winner-only reductions expose an indicator, not per-class margins, at the
    * native label minus its indexing base. Their prediction keeps the native
    * label and they do not produce a probability column.
    */
  override def transform(dataset: Dataset[_]): DataFrame = {
    logTransform[DataFrame]({
      val df = transformImplInternal(dataset)
      val native = col(vowpalWabbitPredictionCol)
      val scalar = native.getField("prediction").cast(DoubleType)
      val classes = getNumClassesModel
      val outputs: Seq[(String, Column)] = if (classes == 2) {
        val linked = argumentValue("--link").contains("logistic")
        val probabilityUdf = udf { (value: Double) =>
          val p = if (linked) value else 1.0 / (1.0 + exp(-value))
          Vectors.dense(1 - p, p)
        }
        val rawUdf = udf { (value: Double) =>
          if (linked) Vectors.dense(1 - value, value) else Vectors.dense(-value, value)
        }
        val probability = probabilityUdf(scalar)
        val predictionUdf = udf(probability2prediction _)
        Seq(getRawPredictionCol -> rawUdf(scalar), getProbabilityCol -> probability,
          getPredictionCol -> predictionUdf(probability))
      } else if (hasClassScores) {
        val vectorUdf = udf { (values: Seq[Float]) =>
          require(values.length == classes,
            s"VW returned ${values.length} scores; numClasses must match the native model ($classes)")
          Vectors.dense(values.toArray.map(_.toDouble))
        }
        val vector = vectorUdf(native.getField("predictions"))
        val argMaxUdf = udf { (values: Vector) => values.argmax.toDouble }
        Seq(getRawPredictionCol -> vector, getProbabilityCol -> vector, getPredictionCol -> argMaxUdf(vector))
      } else {
        val base = winnerIndexBase
        val indicatorUdf = udf { (label: Double) =>
          val index = label.toInt - base
          require(label == label.toInt.toDouble && index >= 0 && index < classes,
            s"VW label $label with indexing base $base is outside numClasses=$classes")
          Vectors.sparse(classes, Array(index), Array(1.0))
        }
        Seq(getRawPredictionCol -> indicatorUdf(scalar), getPredictionCol -> scalar)
      }
      outputs.foldLeft(df) { case (frame, (name, value)) =>
        if (name.isEmpty) frame else frame.withColumn(name, value.as(name, outputMetadata(name)))
      }
    }, dataset.columns.length)
  }

  override def copy(extra: ParamMap): this.type = defaultCopy(extra)

  override protected def raw2probabilityInPlace(rawPrediction: Vector): Vector =
  {
    throw new NotImplementedError("Not implemented")
  }

  override def predictRaw(features: Row): Vector =
  {
    throw new NotImplementedError("Not implemented")
  }
}

object VowpalWabbitClassificationModel extends ComplexParamsReadable[VowpalWabbitClassificationModel]
