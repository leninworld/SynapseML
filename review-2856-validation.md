# SynapseML #2856 — retained hosted-Docker results

Recorded results, faithfully summarized; this page is not fresh execution.
Source base: 2982f5a9b198fc3c35e601187887e18b57634faa
Tested source tree (2982 + ten postimages): 90b4ad07ffb0dbe2868369e988122c82cc63e1f3
Per-file SHA-256 values are listed in the review record.

I2 run 44128124454: Scala lanes PASS; overall run remains FAILED.
DML: 32 plural + 10 legacy cases passed, including eight feedback regressions.
Registration: 1 passed. Root fuzzing: 10 passed, one pre-existing ignore.
I3 run 44135619861: complete installed-artifact lane PASS.
Python: 9 methods + 23 successful subtests; 2 generated constructors passed.
Build/test compile: PASS. ScalaStyle: 14 checks, zero errors.
Black: PASS, 225 files unchanged. Fresh wheel/JAR build and install: PASS.
Wheel identity/module paths: verified in both Python invocations.
JVM class origins: verified in generated invocation. Source before/after: equal.

Verified API behavior on these exact source bytes:
- Real StringIndexer treatments rejected; numeric controls/confounders retained.
- GLR offsets fit in both nuisance roles without mutating caller parameters.
- Seven JVM statistics reject mixed routes on fitted/copied/reloaded models.
- Parallel plural MLP draws preserve the caller's layers array.

Limits: no full Azure Pipelines, live Azure/WASBS or hosted-notebook acceptance.
No native LightGBM auxiliary-fit proof; censorCol unsupported.
Auxiliary inputs remain covariates. Warnings and earlier failures retained.
allThirtyDrawsSucceeded remains unestablished; B30 case success is not that claim.

Runs: https://github.com/leninworld/SynapseML/actions/runs/44128124454
      https://github.com/leninworld/SynapseML/actions/runs/44135619861
