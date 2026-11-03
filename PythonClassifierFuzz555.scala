// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.train

import com.microsoft.azure.synapse.ml.codegen.{CodegenConfig, PyTestGen}
import com.microsoft.azure.synapse.ml.codegen.CodegenConfigProtocol._
import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import spray.json._

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Paths}

/** Diagnostic-only invocation of the unchanged upstream test generator for this affected stage. */
object PythonClassifierFuzz555 {
  def main(args: Array[String]): Unit = {
    require(args.length == 1)
    val conf = new String(Files.readAllBytes(Paths.get(args(0))), StandardCharsets.UTF_8)
      .parseJson.convertTo[CodegenConfig]
    try {
      val suite = new VerifyTrainClassifier()
      require(suite.testObjects().length == 3, "INCOMPLETE_CLASSIFIER_FUZZ_OBJECTS")
      suite.makePyTestFile(conf)
      PyTestGen.makeInitFiles(conf)
    } finally {
      TestBase.stopSparkSession()
    }
  }
}
