# Review of the external improvement plan of 4 Oct 2026

**The source.** Hassan supplied an improvement plan for the predictive engine, prepared outside
this repository on 4 Oct: "ArbDesk4 predictive engine: Claude Code improvement handoff". It was
not committed.

**How it was checked.** Each claim was rechecked against main, the live database and the
Supabase gateway log on 4 Oct before anything was changed. Where a figure below was not
remeasured, it says so.

## Does it align with the direction already taken?
**Yes, on every principle:**
- shadow before serving;
- matched cases;
- one call per city, day and checkpoint;
- date-clustered intervals;
- pre-registered gates;
- no lookahead;
- operational repairs kept apart from model experiments;
- 60% as a research target, not a promise.

These are the rules of `docs/FORECAST_EVALUATION_CONTRACT.md` (fec-v1) and of the Challenger A
and C pre-registrations.

**Where the plan's evidence is out of date or incomplete:**
- Its checkpoint counts include second captures. The cause is now found and fixed (P0.1 below).
- PR #294 is merged. rd3 has been in forward shadow since 3 Oct 16:36Z, scored by
  `tools/fec_s10_forward.py` (#296); the first look is on 25 Oct.
- One cause of the intraday weakness was not in the plan: the floor and the station label read
  the NWS five-minute feed. It is fixed in #297.

## Item by item

| Item | Status, 4 Oct | Evidence |
|---|---|---|
| **P0.1** Count one incumbent call per checkpoint | **Done in this PR.** `v_checkpoint_calls.first_call` (the first capture by `decided_at`, the id breaking a tie) keeps every versioned row. The scoreboard, `v_prediction_hindsight` and its summary count first calls only. The tick no longer writes a checkpoint that any version already holds. | **Cause.** The tick's 75-minute window overlaps the next hourly tick by 15 minutes. `engine_version` is the commit, and the nightly bots commit to main, so the second tick held nothing under its own version and wrote the checkpoint again an hour late.<br>**Size.** 81 second captures out of 2,498 settled calls (25 Sep – 3 Oct); 19 named a different top bucket.<br>**Proof.** REPEATABLE READ: the old columns were identical, and exactly the 81 rows left the panel.<br>**After applying, as anon.** Each checkpoint, days counted before → now (hits in brackets):<br>• 2 h before peak: 443 → 410 (152 hits)<br>• 1 h before peak: 433 → 409 (191 hits)<br>• 1 h after peak: 436 → 415 (299 hits)<br>• noon: 410 → 408<br>• evening before: 381 → 380<br>• day-ahead: 893, unchanged<br>**Test.** `frozen-calls.cjs` shows a second capture stays in the record and is not scored. |
| **P0.2** Make the "Was it right?" filters and provenance clear | **Mostly done in this PR.** The selector says it picks the detail table, and a line above the table gives the selected moment's own record, saying that the headline is the day-ahead call. Each row shows the scheduled local time and the actual capture time (UTC), with the engine version on hover. A call not yet graded reads "pending", never "miss". | **Not done:** the city's timezone name on each row. The table shows the priced centre. The raw forecast is shown beside it on /predictive's forward table (audit repair 3), not in this panel. |
| **P0.3** Repair the forecast write failure | **Root cause found and fixed in this PR.** A write refused with PGRST303 gets one retry, as reads have since #292. Any other 401 still fails at once. | The gateway log for 1 Oct 03:49:31Z shows the `weather_forecast_models` upsert answered 401 with `PostgREST; error=PGRST303`, while a POST with the same key got 201 in the same millisecond. PostgREST refuses those claims before any statement runs, so nothing was written; the upsert is also idempotent on `city_key,model,run_at,for_date`. In the last 24 h: 5 such refusals, all GETs, all retried. `ingest_forecasts` already logs `partial`, with the cities, when a run fails (#292). Issue times: `v_forecast_issued` (P2.6); not rechecked here. |
| **P1.1** Diagnose the intraday update | **Done, research only** (`docs/P11_INTRADAY_DIAGNOSIS_2026-10-04.md`). Two causes: the floor from NWS readings (fixed, #297), and the switch at local midnight to an uncorrected lead-0 centre with a narrower width. | 27 Sep – 3 Oct, matched rows, date-clustered intervals: switching to the same-day centre and width costs 0.110 – 0.297 log loss before the peak, mostly from the width; the floor helps everywhere. The candidate (day-ahead centre and width plus the floor) needs a pre-registered forward test. **Pre-registered and in shadow from 4 Oct:** `docs/P11_DA_FLOOR_PREREG.md`, `scripts/variant_shadow.py`; first look about 25 Oct. |
| **P1.2** Restrained corrections; widths matched to the served centre | **Aligned; no change.** The station-width challenger (P3.9 part 3) is in forward shadow. Its gate (at least 7 dates) is a prerequisite, not sufficient evidence. | The plan's figures (128 city-days, 91% vs 81% coverage) were not remeasured here. |
| **P1.3** S10 freshness and challengers | **Done.** rd2 (decision-time observations) was rejected. rd3 (day-before model maxima, bias-corrected) was accepted on the holdout and has been in forward shadow since 3 Oct. | `docs/CHALLENGER_A_PREREG.md`, `docs/CHALLENGER_C_PREREG.md`, #293 – #296. The first forward look is from 25 Oct, by the pre-registered rule. |
| **P1.4** Calibration by horizon | **Aligned; no change.** P3.6 keeps the identity transform when a fitted one scores worse on held-out dates. | P3.6 row of `PLAN_PROGRESS.md`. |
| **P2.1** Compare all seven entry times on executable prices | **Not started**, after P0 and P1 as the plan itself orders. | Each checkpoint stores the top of book (bid, ask, last), not depth. A fill-realistic comparison needs depth or a stated fill assumption. |
| **P2.2** One versioned prediction contract | **Part 1 (4 Oct):** `v_prediction_contract` and `model_registry`, read-only (`docs/P22_PREDICTION_CONTRACT.md`). Parts 2 (the page) and 3 (decisions, evaluation, refits as candidates) follow. Before that: **Not started.** `prediction_checkpoints` already records `engine_version`, `model_path`, inputs and the full ladder. S10 and its challengers are recorded separately in `s10_shadow_checkpoints`. | |

## Found while checking (not in the plan)
- **#297.** From 5 Sep, the pricing floor and the station label every model trains on took the
  maximum over every reading, which includes the NWS five-minute feed. Both now take the
  settlement feed's maximum.
  - The US label now differs from the settlement on 0 of 36 city-days of 30 Sep – 2 Oct; it was
    22.
  - The cache was repaired: `data/repairs/2026-10-04-settlement-max`.
- **The morning reading in `v_city_day_features` is chosen arbitrarily** among same-hour ties.
  253 of 402 US city-days have tied readings with different temperatures. Not changed.
