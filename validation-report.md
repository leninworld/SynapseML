SynapseML #874 — retained hosted Docker validation results
Base: 80191a61e743b8ba462b692d2ffa2e95ac2df91d
Exact tested patch SHA256:
db46a915737b56f1892a4566099d39e429295ea77214627ce519e5d9575fd543
Native/build/examples run: 43153624699; website run: 43158910593
Linux x86-64 | Java 11 | Spark 3.5.0 | Python 3.11.8
Python LightGBM 3.3.5 / lightgbmlib 3.3.510 (one tested pair)
PASS: root compile/test-compile/styles/Black, codegen, wheels/JARs, publishM2
PASS: 5 selected ScalaTest cases, including 14 dense+sparse score/leaf inputs
PASS: exact fixture regeneration and all 3 Python/Scala/PySpark guide examples
PASS: installed public model-text loader, null/unseen mapping and predictions
PASS: full website generation, 41 tests, production build, SSR/sidebar/Overview
PASS: actual development-page HTTP 200; final eight-file hashes and scope
Both website-run containers stopped, exit 0, no OOM
Earlier run failed overall in a private HTML checker; its passing native checks reused
No managed Databricks/Fabric, arbitrary-version or full upstream Azure CI claim
