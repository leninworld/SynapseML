# SynapseML #2817 — hosted Docker validation

Recorded results from GitHub Actions run 42600450683 (Linux x86_64).
Base source: 6c2699defcfb24d5bff5ba55f2252ba2d8cbea24
Tested patch SHA256: 2bb99a662739d119c7cb4c87ed0a940db03e5dea70e7746402c4c817a71e4ca3
Environment: Spark 3.5.0, Scala 2.12, JDK 11; native VW JNI 9.3.0.

PASS — Real native VW control; baseline regression failed as expected.
PASS — Root compile, Test/compile, Scala style and test style.
PASS — Both focused schema and case-sensitivity regression tests.
PASS — Full unfiltered VW suite: 116 tests, 0 failures, 0 skipped.
PASS — Pipeline test-coverage check: 1 test.
PASS — All six module codegen tasks; core/VW Python install; publishM2.
PASS — Black on baseline and candidate.
PASS — Installed generated Python API against branch-built JVM artifacts.
PASS — Scalar and array predictions preserve input schema, values and metadata.
PASS — Custom input, copy, save/load and both collision case modes.
PASS — Both owned jobs stopped; full critical evidence exported; no OOM events.

The first attempt failed on metadata preservation and remains recorded as failed.
This is a summary of retained test evidence, not a fresh terminal capture.
These checks do not establish live Fabric, Windows or nested-field metadata behavior.
