# Copyright (C) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in project root for information.

import tempfile
import unittest
from pathlib import Path


class LightGBMTuningModelInfoTests(unittest.TestCase):
    def test_wrapped_training_only_setting_from_generated_python(self):
        from synapse.ml.core.init_spark import init_spark

        spark = init_spark()
        from synapse.ml.automl import TuneHyperparameters
        from synapse.ml.automl.HyperparamBuilder import (
            DiscreteHyperParam,
            HyperparamBuilder,
            RandomSpace,
        )
        from synapse.ml.automl.TuneHyperparametersModel import TuneHyperparametersModel
        from synapse.ml.lightgbm import LightGBMClassifier
        from synapse.ml.train import TrainClassifier

        data = spark.range(64).selectExpr(
            "cast(id % 8 - 4 as double) as x", "cast(id % 8 >= 4 as double) as label"
        ).coalesce(1)
        learner = LightGBMClassifier(
            numIterations=2,
            numTasks=1,
            numThreads=1,
            minDataInLeaf=1,
            verbosity=-1,
        )
        trainer = TrainClassifier(model=learner, labelCol="label", inputCols=["x"])
        search = (
            HyperparamBuilder()
            .addHyperparam(learner, learner.numLeaves, DiscreteHyperParam([7]))
            .build()
        )
        best = TuneHyperparameters(
            models=[trainer],
            paramSpace=RandomSpace(search).space(),
            evaluationMetric="accuracy",
            numFolds=2,
            numRuns=1,
            parallelism=1,
            seed=7,
        ).fit(data)
        loaded = None
        try:
            info = best.getBestModelInfo()
            self.assertIn("numLeaves: 7", info)
            self.assertEqual(best.getBestModelParamsInfo(), info)
            self.assertEqual(best.copy({}).getBestModelInfo(), info)
            with tempfile.TemporaryDirectory(prefix="lightgbm-info-") as root:
                path = str(Path(root) / "model")
                best.write().save(path)
                loaded = TuneHyperparametersModel.load(path)
                self.assertEqual(loaded.getBestModelInfo(), info)
                self.assertEqual(loaded.transform(data).count(), data.count())
        finally:
            try:
                if loaded is not None:
                    restored_native = loaded.getBestModel()._java_obj.getLastStage()
                    restored_native.getLightGBMBooster().freeNativeMemory()
            finally:
                native = best.getBestModel()._java_obj.getLastStage()
                native.getLightGBMBooster().freeNativeMemory()


if __name__ == "__main__":
    unittest.main()
