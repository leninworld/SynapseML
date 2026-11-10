// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.featurize

import com.microsoft.azure.synapse.ml.core.schema.{CategoricalColumnInfo, CategoricalUtilities, SparkSchema}
import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import com.microsoft.azure.synapse.ml.core.test.fuzzing.{EstimatorFuzzing, TestObject, TransformerFuzzing}
import org.apache.spark.ml.Pipeline
import org.apache.spark.ml.classification.LogisticRegression
import org.apache.spark.ml.linalg.{SQLDataTypes, Vectors}
import org.apache.spark.ml.util.MLReadable
import org.apache.spark.sql.{DataFrame, Row}
import org.apache.spark.sql.types._

import scala.collection.immutable.Seq
import scala.util.Try

trait ValueIndexerUtilities extends TestBase {
  import spark.implicits._


  protected val schemaCases: Seq[(org.apache.spark.sql.types.DataType, Seq[Any])] = Seq(
    (IntegerType, Seq[Any](0, 1, 0, 1)),
    (LongType, Seq[Any](0L, 1L, 0L, 1L)),
    (DoubleType, Seq[Any](0.0, 1.0, 0.0, 1.0)),
    (StringType, Seq[Any]("a", "b", "a", "b")),
    (BooleanType, Seq[Any](false, true, false, true)))

  protected def schemaInput(dataType: org.apache.spark.sql.types.DataType, values: Seq[Any]): DataFrame = {
    val metadata = new MetadataBuilder().putString("schema-test", "preserve-input").build()
    val schema = StructType(Seq(
      StructField("label", dataType, nullable = false, metadata = metadata),
      StructField("features", SQLDataTypes.VectorType, nullable = false, metadata = metadata)))
    val rows = values.zipWithIndex.map { case (value, index) =>
      Row(value, Vectors.dense((index % 2).toDouble, 1.0))
    }
    spark.createDataFrame(spark.sparkContext.parallelize(rows), schema)
  }

  protected def indexValues(input: DataFrame, column: String): Seq[Double] =
    input.select(column).collect().map(row => row.get(0).asInstanceOf[Number].doubleValue()).toList

  protected def assertInputSchema(input: DataFrame, before: String): Unit = {
    assert(input.schema.json == before, "SCHEMA_INPUT_MUTATED: input schema must remain unchanged")
    val expected = DataType.fromJson(before).asInstanceOf[StructType]
    assert(input.schema.fields.map(_.dataType).sameElements(expected.fields.map(_.dataType)))
    assert(input.schema.fields.map(_.metadata.json).sameElements(expected.fields.map(_.metadata.json)))
  }

  /** sample dataframe */
  protected lazy val df: DataFrame = Seq[(Int, Long, Double, Boolean, String)](
    (-3, 24L, 0.32534, true, "piano"),
    (1, 5L, 5.67, false, "piano"),
    (-3, 5L, 0.32534, false, "guitar"))
    .toDF("int", "long", "double", "bool", "string")

  /** sample dataframe with Null values*/
  protected lazy val nullDF: DataFrame = Seq[(String, java.lang.Integer, java.lang.Double)](
    ("Alice", null, 44.3), //scalastyle:ignore null
    (null, 60, null), //scalastyle:ignore null
    ("Josh", 25, Double.NaN),
    ("Bob", 25, 77.7),
    ("Fred", 55, Double.NaN),
    ("Josh", 21, 33.3))
    .toDF("string", "int", "double")
}

/** Tests to validate the functionality of Train Classifier module. */
class VerifyIndexToValue extends ValueIndexerUtilities with TransformerFuzzing[IndexToValue] {
  val col = "string"
  private val newName = col + "_cat"
  private lazy val df2 = new ValueIndexer().setInputCol(col).setOutputCol(newName).fit(df).transform(df)
  private val testName = col + "_noncat"

  test("Test: Going to Categorical and Back") {
    val df1 = new IndexToValue().setInputCol(newName).setOutputCol(testName).transform(df2)
    df1.select(col, testName).collect.foreach(row => assert(row(0) == row(1), "two columns should be the same"))
  }


  schemaCases.foreach { case (dataType, values) =>
    test(s"IndexToValue preserves overwritten ${dataType.typeName} input schema") {
      val input = schemaInput(dataType, values)
      val indexed = new ValueIndexer().setInputCol("label").setOutputCol("indexed").fit(input).transform(input)
      val before = indexed.schema.json
      val decoder = new IndexToValue().setInputCol("indexed").setOutputCol("indexed")
      val outputSchema = decoder.transformSchema(indexed.schema)
      assertInputSchema(indexed, before)
      assert(outputSchema("indexed").dataType == dataType)
      val output = decoder.transform(indexed)
      assert(output.select("indexed").collect().map(_(0)).toSeq == values)
      assertInputSchema(indexed, before)
    }

    test(s"IndexToValue preserves appended ${dataType.typeName} input schema") {
      val input = schemaInput(dataType, values)
      val indexed = new ValueIndexer().setInputCol("label").setOutputCol("indexed").fit(input).transform(input)
      val before = indexed.schema.json
      val decoder = new IndexToValue().setInputCol("indexed").setOutputCol("decoded")
      val outputSchema = decoder.transformSchema(indexed.schema)
      assertInputSchema(indexed, before)
      assert(outputSchema("decoded").dataType == dataType)
      assert(outputSchema.fieldNames.toSeq == indexed.columns.toSeq :+ "decoded")
      val output = decoder.transform(indexed)
      assert(output.select("decoded").collect().map(_(0)).toSeq == values)
      assertInputSchema(indexed, before)
    }

    test(s"IndexToValue Pipeline preserves ${dataType.typeName} input schema") {
      val input = schemaInput(dataType, values)
      val indexed = new ValueIndexer().setInputCol("label").setOutputCol("indexed").fit(input).transform(input)
      val before = indexed.schema.json
      val decoder = new IndexToValue().setInputCol("indexed").setOutputCol("indexed")
      val fitted = Try(new Pipeline().setStages(Array(decoder)).fit(indexed))
      assertInputSchema(indexed, before)
      val output = fitted.get.transform(indexed)
      assertInputSchema(indexed, before)
      assert(output.select("indexed").collect().map(_(0)).toSeq == values)
    }
  }

  override def testObjects(): scala.Seq[TestObject[IndexToValue]] = Seq(new TestObject(
    new IndexToValue().setInputCol(newName).setOutputCol(testName), df2
  ))

  override def reader: MLReadable[_] = IndexToValue
}

/** Tests to validate the functionality of Train Classifier module. */
//scalastyle:off null
class VerifyValueIndexer extends ValueIndexerUtilities with EstimatorFuzzing[ValueIndexer] {


  schemaCases.foreach { case (dataType, values) =>
    test(s"ValueIndexer preserves overwritten ${dataType.typeName} input schema") {
      val input = schemaInput(dataType, values)
      val before = input.schema.json
      val indexer = new ValueIndexer().setInputCol("label").setOutputCol("label")
      val outputSchema = indexer.transformSchema(input.schema)
      assertInputSchema(input, before)
      assert(outputSchema("label").dataType == DoubleType)
      assert(outputSchema.fieldNames.toSeq == input.columns.toSeq)
    }

    test(s"ValueIndexerModel preserves overwritten ${dataType.typeName} input schema") {
      val input = schemaInput(dataType, values)
      val before = input.schema.json
      val model = new ValueIndexer().setInputCol("label").setOutputCol("label").fit(input)
      assertInputSchema(input, before)
      val outputSchema = model.transformSchema(input.schema)
      assertInputSchema(input, before)
      assert(outputSchema("label").dataType == DoubleType)
      val indexed = model.transform(input)
      assert(indexValues(indexed, "label") == Seq(0.0, 1.0, 0.0, 1.0))
      assertInputSchema(input, before)
      assert(model.getLevels.toSeq == values.distinct)
      val copied = model.copy(org.apache.spark.ml.param.ParamMap.empty)
      assert(copied.transform(input).select("label").collect().toSeq == indexed.select("label").collect().toSeq)
      assertInputSchema(input, before)
    }

    test(s"ValueIndexer and model preserve appended ${dataType.typeName} input schema") {
      val input = schemaInput(dataType, values)
      val before = input.schema.json
      val indexer = new ValueIndexer().setInputCol("label").setOutputCol("indexed")
      val outputSchema = indexer.transformSchema(input.schema)
      assertInputSchema(input, before)
      assert(outputSchema.fieldNames.toSeq == input.columns.toSeq :+ "indexed")
      val model = indexer.fit(input)
      val modelSchema = model.transformSchema(input.schema)
      assertInputSchema(input, before)
      assert(modelSchema == outputSchema)
      assert(indexValues(model.transform(input), "indexed") ==
        Seq(0.0, 1.0, 0.0, 1.0))
      assertInputSchema(input, before)
    }

    test(s"ValueIndexer JVM Pipeline preserves ${dataType.typeName} input schema") {
      val input = schemaInput(dataType, values)
      val before = input.schema.json
      val indexer = new ValueIndexer().setInputCol("label").setOutputCol("label")
      val classifier = new LogisticRegression().setMaxIter(2).setRegParam(0.1)
      val fitted = Try(new Pipeline().setStages(Array(indexer, classifier)).fit(input))
      assertInputSchema(input, before)
      val output = fitted.get.transform(input)
      assertInputSchema(input, before)
      val predictions = output.select("prediction").collect()
      assert(predictions.length == values.length)
      assert(predictions.forall(row => Set(0.0, 1.0).contains(row.getDouble(0))))
    }
  }

  /** test CategoricalMap for different underlying types */
  test("Test: Convert the regular column into categorical") {
    for (col <- df.columns) {
      val newName = col + "_cat"
      val df2      = new ValueIndexer().setInputCol(col).setOutputCol(newName).fit(df).transform(df)

      assert(!SparkSchema.isCategorical(df2, col), "Check for non-categorical columns")
      assert(SparkSchema.isCategorical(df2, newName), "Check for categorical columns")

      val info = new CategoricalColumnInfo(df2, newName)

      assert(info.isCategorical, "the column is supposed to be categorical")
      assert(info.isMML, "wrong metadata style in categorical column")
      assert(!info.isOrdinal, "wrong ordinal style in categorical column")
      assert(info.dataType == df.schema(col).dataType, "categorical data type is not correct")
    }
  }

  test("Test: String categorical levels") {
    val col = "string"
    val trueLevels = df.select("string").collect().map(_(0).toString).distinct.sorted

    val newName = col + "_cat"
    val df2 = new ValueIndexer().setInputCol(col).setOutputCol(newName).fit(df).transform(df)

    val map = CategoricalUtilities.getMap[String](df2.schema(newName).metadata)

    val levels = map.levels.sorted

    (trueLevels zip levels).foreach {
      case (a, b) => assert(a == b, "categorical levels are not the same")
    }
  }

  ignore("test with null or missing values, going to categorical and back") {
    for (col <- nullDF.columns) {
      val newName = col + "_cat"
      val df = new ValueIndexer().setInputCol(col).setOutputCol(newName).fit(nullDF).transform(nullDF)

      assert(!SparkSchema.isCategorical(df, col), "Check for non-categorical columns")
      assert(SparkSchema.isCategorical(df, newName), "Check for categorical columns")

      val info = new CategoricalColumnInfo(df, newName)

      assert(info.isCategorical, "the column is supposed to be categorical")
      assert(info.isMML, "wrong metadata style in categorical column")
      assert(!info.isOrdinal, "wrong ordinal style in categorical column")
      assert(info.dataType == nullDF.schema(col).dataType, "categorical data type is not correct")

      val testName = col + "_noncat"
      val df1 = new IndexToValue().setInputCol(newName).setOutputCol(testName).transform(df)
      def validationFunc: Row => Boolean =
        if (col == "double") {
          row: Row => {
            // Double NaNs are treated similar to nulls, as missing values (consistent with Spark)
            val actualValue = if (row(0).asInstanceOf[Double].isNaN) null else row(0)
            val expectedValue = row(1)
            actualValue == expectedValue
          }
        } else {
          row: Row => row(0) == row(1)
        }
      val results = df1.select(col, testName).collect
      results.foreach(row => assert(validationFunc(row), "two columns should be the same"))
    }
  }

  val inputCol = "text"

  override def testObjects(): Seq[TestObject[ValueIndexer]] = List(new TestObject[ValueIndexer](
    new ValueIndexer().setInputCol(df.columns.head).setOutputCol("foo"), df))

  override def reader: MLReadable[_] = ValueIndexer
  override def modelReader: MLReadable[_] = ValueIndexerModel
}
