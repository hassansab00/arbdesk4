## Primary preparation allowance — 17 Oct, follow-up

Cold input loading could exhaust the primary preparation allowance before
any vector was built, leaving all affected calls on the baseline. The bounded
allowance becomes 40 seconds, leaving sixty of the nominal 100-second tick
budget for pricing/capture. This changes no model weights, probabilities,
calibration gate or schedule, and no frozen call is rewritten. The complete
Python suite and database contracts passed. CI and real acceptance will be
recorded in the follow-up PR.

