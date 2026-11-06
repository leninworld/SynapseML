# Copyright (C) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in project root for information.

import tempfile
import unittest
from pathlib import Path


class TuningModelInfoTests(unittest.TestCase):
    def test_generated_model_info_getter_copy_and_persistence(self):
        from synapse.ml.core.init_spark import init_spark

        spark = init_spark()
        from pyspark.ml.classification import LogisticRegression
        from pyspark.ml.linalg import Vectors
        from synapse.ml.automl import TuneHyperparameters
        from synapse.ml.automl.HyperparamBuilder import (
            DiscreteHyperParam,
            HyperparamBuilder,
            RandomSpace,
        )
        from synapse.ml.automl.TuneHyperparametersModel import TuneHyperparametersModel

        data = spark.createDataFrame(
            [
                (float(value > 0), Vectors.dense(float(value)))
                for value in range(-16, 17)
                if value
            ],
            ["label", "features"],
        ).coalesce(1)
        learner = LogisticRegression()
        search = (
            HyperparamBuilder()
            .addHyperparam(learner, learner.maxIter, DiscreteHyperParam([4]))
            .build()
        )
        best = TuneHyperparameters(
            models=[learner],
            paramSpace=RandomSpace(search).space(),
            evaluationMetric="accuracy",
            numFolds=2,
            numRuns=1,
            parallelism=1,
            seed=7,
        ).fit(data)
        info = best.getBestModelInfo()

        self.assertIn("maxIter: 4", info)
        self.assertEqual(best.getBestModelParamsInfo(), info)
        self.assertEqual(best.copy({}).getBestModelInfo(), info)
        with tempfile.TemporaryDirectory(prefix="model-info-") as root:
            path = str(Path(root) / "model")
            best.write().save(path)
            loaded = TuneHyperparametersModel.load(path)
            self.assertEqual(loaded.getBestModelInfo(), info)
            self.assertEqual(loaded.getBestModelParamsInfo(), info)
            self.assertEqual(loaded.transform(data).count(), data.count())


if __name__ == "__main__":
    unittest.main()
