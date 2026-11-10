SynapseML categorical schema preservation — retained validation results

Run: https://github.com/leninworld/SynapseML/actions/runs/42772446328
Hosted Linux Docker; Spark 3.5.0; JDK 11; generated Python 3.11 package.
Source: 37948d7d03ef0fa7f1aa24c54c2dd4d8a29c8dc0
Patch SHA256: c3f1a92fed6fa3c227d306acdf61c1476603a5ea72739ba58238aaa382978d77

Baseline schema checks: 25 expected mutation failures; 10 append controls pass.
Fixed schema checks: all 35 new cases pass across five data types.
Original JVM roster: 323 pass, 1 known NaN failure, 2 ignored.
Fixed JVM total: 358 pass, the same 1 NaN failure, the same 2 ignored.
Python baseline and fixed: 6 pass each; the same known Long cast failure each.
Generated wrappers/wheel and loaded JVM JAR identities verified in both roles.
Six-module code generation, compilation, style, packaging and Black completed.
Real public Python pipelines and integer/string save-load checks pass.

All four jobs succeeded in 14m33s. All five owned containers stopped; no OOM.
765 evidence files (9,248,829 bytes) decoded and checked by length and SHA256.
This is a summary of retained results, not a new terminal run. Not all tests green.
Affected suites only; no live Fabric, Databricks or Windows verification.
