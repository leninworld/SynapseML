# SynapseML #2856 — retained cleanup-validation results

Recorded results, faithfully summarized; this page is not fresh execution.
I4 run: 44156511453 — SUCCESS, installed-artifact lane only.
Diagnostic commit: 9604f9c5ebbb0e0dd489f7b7a44d7cb15f9eae1d
Source base: 2982f5a9b198fc3c35e601187887e18b57634faa
Changed Python test SHA-256: ce5391a0e64bd2c8175cde8234965ae15971bb652f3b635ea0204e2967a84141

Fix: register owned SparkSession.stop with addClassCleanup immediately after
creation; remove tearDownClass override. Borrowed contexts remain untouched.

Fresh root compile/test compile, codegen, wheel/JAR build and install: PASS.
ScalaStyle: 14 checks, zero errors. Black: 225 files unchanged.
Python: 9 handwritten methods + 23 successful subtests; 2 generated tests PASS.
Normal owned-context cleanup after the full pytest suite: VERIFIED.

Real-Spark setup-failure controls through stdlib unittest:
- Exact published-before code: reproduced the owned-context leak.
- Candidate provenance failure: owned context stopped.
- Candidate initial-fit failure: owned context stopped.
- Candidate borrowed-context failure: shared context preserved.
Each control intentionally raised one setup error and ran zero test methods.
These four controls are separate from the scientific test totals.
Failed setUpClass was not directly induced under pytest.

Installed wheel identities/module paths: verified for both Python consumers.
Explicit JVM class origins: verified in generated consumer.
Ten source hashes equal before/after; all owned containers stopped, no OOM.

All five production files, both Scala tests and both docs are unchanged from published head 9bf2b98823fbcf2baf4101e42a0136dbc251d5b2.
Earlier Scala and website evidence stays qualified to its actual scope/base.
Earlier failed runs remain failed. No full Azure Pipelines/live Azure claim.
allThirtyDrawsSucceeded remains unestablished. Warnings are retained.

Run: https://github.com/leninworld/SynapseML/actions/runs/44156511453
