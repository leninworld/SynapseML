// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.train

import com.microsoft.azure.synapse.ml.build.BuildInfo
import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import org.scalatest.Suite
import spray.json._
import spray.json.DefaultJsonProtocol._

/** Exact runtime test registration; paired with fresh JUnit, never a test-pass substitute. */
object TestInventory1850 {
  def main(args: Array[String]): Unit = {
    try {
      println("DATASET_ROOT_1850=" + BuildInfo.datasetDir)
      val ignored = scala.collection.mutable.Map.empty[String, Seq[String]]
      val names = args.map { name =>
        val suite = Class.forName(name).getDeclaredConstructor().newInstance().asInstanceOf[Suite]
        ignored(name) = suite.tags.filter { case (_, tags) => tags.contains("org.scalatest.Ignore") }.keys.toSeq.sorted
        name -> suite.testNames.toSeq.sorted
      }.toMap
      println("TEST_INVENTORY_1850=" + names.toJson.compactPrint)
      println("IGNORED_INVENTORY_1850=" + ignored.toMap.toJson.compactPrint)
    } finally {
      TestBase.stopSparkSession()
    }
  }
}
