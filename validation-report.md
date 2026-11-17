# Multiple-treatment DML validation — validation results

Feature [#2328](https://github.com/microsoft/SynapseML/issues/2328); unchanged ten-file patch `e5a6704f193b343f59af2dfa2ba637548c85ce6bfb9c2c5e059e4f52b38ad3f0`.

## Current-base results

[Run44006383230](https://github.com/leninworld/SynapseML/actions/runs/44006383230), diagnostic head `f2ab80f0c9496068cd616d2147fd51678f58f041`, tested source `d17f57e314517e4b413cda3cdcd86e7ad15ddb04` / tree `20d1dd7de03f2b344bbe6f64ec76e8983e7f020c`. **This original workflow remains FAILED.** Corrected website [run 44019215363](https://github.com/leninworld/SynapseML/actions/runs/44019215363), head `e95e7327caf0a9700a2ea5bad456c75f3e8261df`, succeeded separately; the disclosed four-component composite has been reviewed and accepted by the contributor.

| Job | Audited result |
| --- | --- |
| Legacy | PASS: DML 10, registration 1, root FuzzingTest 10 executed + exact inherited ignore; compile/style. |
| DML | PASS: 24 new + 10 legacy cases; exact 15-suite inventory. Repeated legacy10 are not additional unique cases. |
| Installed | PASS: root compile/Testcompile/styles, fresh codegen/wheel/JAR, 7 handwritten  + 2 generated tests, actual fits/getters/copy/reload, Black 225 unchanged. |
| Website | Corrected PASS: 26 steps, 41 Node tests, 120 generated files, 59 notebooks converted NOT executed, production build/both routes. No cap raises. |

Actual project Scala 2.12.17, JDK 11.0.32.1, PySpark 3.5.0. Installed per-role wheel/JAR identities match their own rebuilt manifests; no old JAR reuse. Source postimages unchanged, complete export and owned stops verified; no observed OOM kills. Shared 3000s active/ 3600s cleanup preserved. Integrated feature commit `dffcd50978506158580d7effd7a3401b234f4cb9`, tree `e66280aeb4b7f5f57d39dd826cc01eb7139bbb7e`, preserves the exact ten tested postimages on parent `d17f57e3`. The new checkout was clean after committing; the previous checkout and index were preserved.

Current website lossless ZIP 9,798,858 bytes contains 2,026 files/35,636,655 bytes, all hashes verified; canonical/content/sidebar/header checks pass. Both containers stopped, no OOM; completion 574.718s from shared creation. No new browser/capture.

## Qualified retained evidence — 45578-built only

R5 run 43810147890/head 14cca4cb:69 non-DML cases/13 suites. R4 run 43794926147/head d4d00ef3:all 15 notebook cells, N=2000, B20/B30, full-N/ P10/ B10 calibrations and all 4 Python/Scala fences. Prior website run 43982346296/head 6f9acbde:2,024 files/35,564,280 bytes,9,775,308-byte lossless ZIP. No current-master notebook execution or quick-example execution inferred.

## Qualifications

Current DML/legacy 214/71 solver fallbacks; zero test reruns. `VerifyFabricRetryAfter` is discovery, not rerun. `allThirtyDrawsSucceeded` remains UNESTABLISHED. Installed logs retain unsupported generation messages, post-success Hadoop shutdown exceptions, pytest warnings (3 in the generated run) and compiler deprecation/feature warnings. Website npm warns about core-js allowScripts.

34 unique DML cases +15-suite inventory on d17f;69 non-DML cases retained from 45578—not 103 current-master passes. B30 uncertainty is coarse; tiny singular estimates are compatibility only. No identification/simultaneous coverage/EconML-equivalence claim.

No live Azure/WASBS/full Azure Pipelines acceptance. The separately linked Scala screenshot is from the earlier tested `45578` build, in the default dark theme. Wide code requires horizontal scrolling; complete examples are in the source. It is not a current-master screenshot. The long notebook image is not used. JS/ZIP/helpers/logs excluded from the ten-file feature PR. A public-safe review summary will be added under the actual numbered-PR directory in a separately reviewed follow-up; no private review transcript is published.
