// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.automl

import com.microsoft.azure.synapse.ml.core.serialize.ComplexParam
import com.microsoft.azure.synapse.ml.param.ByteArrayParam
import com.microsoft.azure.synapse.ml.train.{AutoTrainer, TrainedClassifierModel, TrainedRegressorModel}
import org.apache.spark.ml.{Estimator, Transformer}
import org.apache.spark.ml.param.{ParamMap, ParamPair, Params}
import org.apache.spark.sql.Dataset

import scala.util.control.NonFatal

/** Preserve custom two-argument fit implementations instead of bypassing their dispatch. */
private[automl] object FitDispatch {
  def usesInheritedParamMapFit(estimator: Estimator[_]): Boolean = {
    try {
      val methods = estimator.getClass.getMethods.filter { method =>
        method.getName == "fit" && method.getParameterCount == 2 &&
          method.getParameterTypes()(0) == classOf[Dataset[_]] &&
          method.getParameterTypes()(1) == classOf[ParamMap]
      }
      methods.nonEmpty && methods.forall(_.getDeclaringClass == classOf[Estimator[_]])
    } catch {
      case NonFatal(_) => false
    }
  }
}

/** A textual snapshot, never a serialized estimator, dataset, or native training buffer. */
private[automl] object BestModelParamsInfo {
  def render(estimator: Estimator[_], model: Transformer): String = {
    val sections = estimator match {
      case trainer: AutoTrainer[_] =>
        Seq(section("trainer", trainer), section("estimator", trainer.getModel))
      case _ => Seq(section("estimator", estimator))
    }
    val fitted = try {
      legacy(model).map("model: " + _).toSeq
    } catch {
      case NonFatal(_) => Seq("model: <unrenderable>")
    }
    (fitted ++ sections).mkString("; ")
  }

  private def section(name: String, owner: Params): String = {
    val values = owner.extractParamMap().filter(owner).toSeq.sortBy(_.param.name).map { pair =>
      s"${pair.param.name}: ${renderValue(pair)}"
    }.mkString(", ")
    s"$name ${owner.getClass.getSimpleName}(${owner.uid}): $values"
  }

  private def placeholder(value: Any): String = {
    Option(value).fold("<null>")(v => s"<${v.getClass.getSimpleName}>")
  }

  private def renderValue[T](pair: ParamPair[T]): String = {
    pair.param match {
      case _: ByteArrayParam => "<binary omitted>"
      case _: ComplexParam[_] => placeholder(pair.value)
      case _ =>
        pair.value match {
          case _: Array[Byte] => "<binary omitted>"
          case _: Params => placeholder(pair.value)
          case _ =>
            try {
              pair.param.jsonEncode(pair.value)
            } catch {
              case NonFatal(_) => "<unrenderable>"
            }
        }
    }
  }

  private def legacy(model: Transformer): Option[String] = {
    model match {
      case _: TrainedClassifierModel | _: TrainedRegressorModel =>
        Some(EvaluationUtils.modelParamsToString(model))
      case best: BestModel => legacy(best.getBestModel)
      case _ => None
    }
  }
}
