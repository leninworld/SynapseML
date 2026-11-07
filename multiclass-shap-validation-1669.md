# SynapseML #1669 — multiclass SHAP tutorial
Retained hosted Docker results; this summary is not a fresh terminal capture.
Base: d3f3af6bd3e4b7d191f51c9eb29f9aeffe788193
Tested patch SHA256:
c0e41f5cf050d3a134d9f7a3b45cf1f87f9bc2fc17ef165268ea94853061477b
Native: PASS — root compile/Test compile, Scala styles and Python Black checks.
Regression: PASS — both existing streaming/bulk multiclass SHAP tests.
Tutorial: PASS — all six authored code cells, 201 rows, five classes, Spark 3.5.0.
Behavior: class blocks, bias/raw-score reconstruction and per-class encoded/grouped importances.
Provenance: installed branch-built Python wheels and JVM jars verified in the actual kernel.
Website: PASS — all 41 tests, complete production build, tutorial/sidebar/Overview links.
Development: PASS — compiled npm start and actual canonical deep-route HTTP 200.
Native and website evidence uses identical frozen source hashes.
Limits: Databricks/Fabric were not run. The first website probe failed; corrected full website job passed.
Native: https://github.com/leninworld/SynapseML/actions/runs/42398326186/job/128480429777
Website: https://github.com/leninworld/SynapseML/actions/runs/42403682231
