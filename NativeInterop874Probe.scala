// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.lightgbm.split6

import com.microsoft.azure.synapse.ml.core.test.base.TestBase
import com.microsoft.azure.synapse.ml.lightgbm.booster.LightGBMBooster
import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Paths}
import org.apache.spark.ml.linalg.Vectors

/** Private measurement harness, not an authored upstream regression suite. */
class NativeInterop874Probe extends TestBase {
  test("Python-trained text model scores and leaves match on real native dense and sparse inputs") {
    val directory = Paths.get("/runtime/interop874")
    val model = new String(Files.readAllBytes(directory.resolve("python-model.txt")), StandardCharsets.UTF_8)
    assert(model.contains("pandas_categorical:"))
    val booster = new LightGBMBooster(model)
    val boosterLocation = classOf[LightGBMBooster].getProtectionDomain.getCodeSource.getLocation.toString
    val nativeLocation = classOf[com.microsoft.ml.lightgbm.lightgbmlib]
      .getProtectionDomain.getCodeSource.getLocation.toString
    assert(boosterLocation.startsWith("file:/runtime/source/lightgbm/target/"))
    assert(nativeLocation.endsWith("/lightgbmlib-3.3.510.jar"))
    Files.write(directory.resolve("jvm-class-locations.txt"),
      (boosterLocation + "\n" + nativeLocation + "\n").getBytes(StandardCharsets.UTF_8))
    val lines = new String(Files.readAllBytes(directory.resolve("expected.tsv")), StandardCharsets.UTF_8)
      .trim.split("\n")
    assert(lines.length == 14)
    lines.foreach { line =>
      val fields = line.split("\t", -1)
      assert(fields.length > 7)
      val values = fields.slice(1, 5).map(value => if (value == "nan") Double.NaN else value.toDouble)
      val raw = fields(5).toDouble
      val normal = fields(6).toDouble
      val leaves = fields.drop(7).map(_.toDouble)
      val indices = values.indices.filter(index => values(index) != 0.0).toArray
      val vectors = Seq(Vectors.dense(values), Vectors.sparse(values.length, indices, indices.map(index => values(index))))
      vectors.foreach { vector =>
        val actualRaw = booster.score(vector, raw = true, classification = false, disableShapeCheck = false)
        val actualNormal = booster.score(vector, raw = false, classification = false, disableShapeCheck = false)
        assert(actualRaw.length == 1 && actualNormal.length == 1, fields(0))
        assert(math.abs(actualRaw.head - raw) <= 1e-9, fields(0) + " raw score")
        assert(math.abs(actualNormal.head - normal) <= 1e-9, fields(0) + " normal score")
        assert(booster.predictLeaf(vector).sameElements(leaves), fields(0) + " leaf indices")
      }
    }
    Files.write(directory.resolve("scala-parity-passed.txt"),
      "14 rows, dense and sparse, raw/normal scores and exact leaves\n".getBytes(StandardCharsets.UTF_8))
  }
}
