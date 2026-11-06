// Copyright (C) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License. See LICENSE in project root for information.

package com.microsoft.azure.synapse.ml.automl;

import java.util.UUID;
import org.apache.spark.ml.param.ParamMap;
import org.apache.spark.sql.Dataset;

public class TestableJavaOverridingModelInfoClassifier extends TestableModelInfoClassifier {
    public TestableJavaOverridingModelInfoClassifier(String uid) {
        super(uid);
    }

    public TestableJavaOverridingModelInfoClassifier() {
        this("javaModelInfo_" + UUID.randomUUID().toString());
    }

    @Override
    public TestableModelInfoClassificationModel fit(Dataset<?> dataset, ParamMap params) {
        return super.fit(dataset, params);
    }
}
