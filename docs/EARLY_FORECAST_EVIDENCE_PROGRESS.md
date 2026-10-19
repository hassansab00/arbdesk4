# Early forecast evidence

Early-horizon accuracy is a separate problem from same-day noon and peak prediction. The corrected weather forecast retains its existing learning path; a poor target date does not establish that a replacement is better on unseen dates.

This change adds a future evening-before comparison to the original immutable checkpoint context. It changes only the raw input temperature centre while keeping the served width and probability calibration. The served distribution must reproduce from the saved centre and width before the comparison is accepted. Both probability ladders are recorded before the outcome. Missing metadata is missing evidence; old alternatives are never reconstructed and counted as forward calls.

The existing score refresh reports correction-helped and correction-hurt counts per city, log-loss differences, and recent daily cohorts. Only original server-receipted calls are compared, after the city's day ends and the venue outcome is available. This diagnostic neither promotes a model nor changes prediction weights. It is not an independently fitted weather model or evidence that forecasting accuracy has improved. Day-ahead still uses its existing pre-midnight record and has no new paired comparison.

Recent daily cohorts are shown alongside the longer-term city ranking. Known settlements and pending checkpoint receipts are separate; pending day-ahead coverage remains explicitly unmeasured. This prevents a recent poor date from being hidden inside a month of aggregate performance.

Early reports display the city's current station MOS fit and width-validation evidence. WXPredict peak-phase adjustments are attached only to supported same-day horizons and the combined report; they no longer appear as if they calibrated early calls.

The remaining Live Weather date selection also uses the shared city-local forecast view. It displays source failures explicitly and describes cooling as a trend rather than a confirmed daily maximum.

Validation: regression suites, unchanged served-call fixtures, Celsius/Fahrenheit ladders, missing and mismatched alternatives, original-call selection, and no scoring before local day-end. Production capture must additionally be checked on the next real evening checkpoint; a successful deployment alone does not establish that evidence was captured.
