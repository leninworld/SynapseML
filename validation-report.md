SynapseML: nested Search schema preflight — verified hosted Docker results

Source: 45578f08e6bacf8475de0e92f1717e761d40f795
Tested patch SHA256: 6260ea07426f772344e2468e93753ff400fc545a0acd3b206a675bd9778e1c9f
Run: https://github.com/leninworld/SynapseML/actions/runs/43521130247

PASS — Baseline: 7 accepted cases and 3 expected assertion failures (10 total).
PASS — Candidate: all 10 schema-preflight cases; all 71 existing offline Search cases.
PASS — Full JSON output parser suite: 4 cases, including the merged nullability regression.
PASS — CI suite registration: 1 case. Candidate ScalaTest total: 86; no failures/skips.
PASS — Root compile, test compilation, both Scala style checks and shipping Black checks.
PASS — Codegen; branch-built core/cognitive wheels and JARs; publishM2; classpath identity.
PASS — Fresh installed Python/Spark: accept equal/prefix shapes; reject struct/array extras.
PASS — Both owned containers stopped: Running=false, Restarting=false, PID=0, ExitCode=0.

Linux x86-64; Java 11.0.32.1; Python 3.11.8; Spark 3.5.0.
Compile Scala 2.12.17; actual Spark runtime Scala 2.12.18.
Live Azure writer/round-trip/service acceptance/notebook: NOT RUN.
Related to #477 only; the original wishlist is not completed. Attempt 1 remains failed.
