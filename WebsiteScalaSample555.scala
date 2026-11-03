// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.train

object WebsiteScalaSample555 {
  def main(args: Array[String]): Unit = {
    val spark = org.apache.spark.sql.SparkSession.builder().master("local[2]")
      .appName("TrainClassifierWebsiteSample").getOrCreate()
    try {
      import com.microsoft.azure.synapse.ml.stages.ClassBalancer
      import com.microsoft.azure.synapse.ml.train.TrainClassifier
      import org.apache.spark.ml.classification.LogisticRegression
      
      val weightedData = spark.createDataFrame(Seq(
        (0.0, -2.0, "a"), (0.0, 0.0, "b"), (1.0, 1.0, "c")))
        .toDF("outcome", "x", "tracking")
      val weighted = new ClassBalancer().setInputCol("outcome").fit(weightedData).transform(weightedData)
      val learner = new LogisticRegression().setLabelCol("outcome").setWeightCol("weight")
        .setMaxIter(8).setRegParam(0.2)
      val classifier = new TrainClassifier().setModel(learner).setIgnoredCols(Array("tracking"))
      val weightedModel = classifier.fit(weighted)
      val scoring = weighted.drop("outcome", "weight", "tracking")
      assert(weightedModel.transform(scoring).count() == weightedData.count())
    } finally {
      spark.stop()
    }
  }
}
