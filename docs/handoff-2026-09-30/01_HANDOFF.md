# Handoff — 30 Sep 2026, ~09:00Z

Written for a new session because Hassan asked for one. It opens with his three questions, answered from the live database and the code. Then it lists what to fix, in order, with the root cause, the files and the acceptance test for each.

**Read in this order:**

1. `CLAUDE.md`: Hassan's standing rules. They override everything, including this folder.
2. `00_START_HERE.md`: how to start the new session.
3. **This file.**
4. `02_FIX_SPECS.md`: the work, specified.
5. `03_CHECKS_DUE.md`: the dated checks.
6. `04_VERIFY_QUERIES.sql`: every number here, reproducible.
7. `docs/PLAN_PROGRESS.md`: the per-step record. The audit table at its top covers 29–30 Sep.
8. `docs/AD4_IMPROVEMENT_PLAN.md`: the plan (v2 to v2.4).

`main` was at **`ac51db2`** (#288) when this was written, with one open PR, #289, which adds this folder. Every number below names its source. Where something was not measured, this file says so.

---

## 1. Hassan's three questions, answered

### 1.1 "Are we running in circles, inventing tasks?"

Partly, yes. 70 PRs were merged between 27 Sep 13:05Z (#219) and 30 Sep 08:28Z (#288) (GitHub PR list). Grouped by what they did:

| What | PRs | Did it change a published price? |
|---|---|---|
| Getting the database under its cap (P1.6: archive, prunes, readers moved to the repo) | #252, #254–#259, #261, #263–#277 | No |
| Research studies: the model against the market, S10, stations, latency (P3.10, P3.9 research) | #221, #228, #240, #242–#247, #249, #250 | No. Every one found that the market is ahead or that there is no edge after costs |
| Fixes from Hassan's 29 Sep audit (crashes, silent failures, scoring) | #279, #280, #282–#285, #287, #288 | No. They fix crashes, make failures visible and correct the scores |
| The decision engine and S10 in shadow (P5.12) | #219, #220, #222, #224, #226, #227, #232–#235 | No. Shadow only; learning is off |
| Things that change what the prediction or the card shows | #225 (card shows the priced centre), #229 (station-correction rows stopped being dropped), #241 (P3.9 learns from the venue), #278/#281 (current prediction between runs), #283 (/predictive shows the priced centre) | Yes, these five |
| Check-ins and handoffs | #223, #236, #238, #251, #260, #286 | No |

**The honest reading:**
- Most of the effort went into keeping the platform alive and into measuring. The database was at 688 MB on 28 Sep; on 30 Sep `pg_database_size` read 529 MB at ~08:20Z and `storage_pressure()` read 537.6 MB (107.5% of the 500 MB tier) at ~09:00Z. Several jobs had been crashing.
- It did not go into the two things Hassan sees on /predictive: US cities missing from the hit/miss view, and the prediction being worse than the market.
- The US-cities problem was measured, written down and **not fixed** (§1.3). That is the failure behind "I told you a hundred times".

### 1.2 "Why is the predictive section so off and inaccurate? What did you do?"

**Measured, day-ahead.** Source: `v_city_hit_history`, the last pricing before each city's local day began, with the market's favourite at the same moment, on the same days. The "Same days" columns count only days both the model and the market priced.

| Settled days | Unit | Days | Our top pick right | Same days: ours / market's | Brier ours / market's | Priced-centre miss |
|---|---|---|---|---|---|---|
| 13–22 Sep | °C | 294 | 31.3% | 31.4% / 43.8% (283 days) | 0.813 / 0.653 | not recorded before 23 Sep |
| 13–22 Sep | °F | 89 | 27.0% | 27.6% / 47.1% (87 days) | 0.785 / 0.669 | not recorded |
| 23–29 Sep | °C | 257 | 38.5% | 38.5% / 49.8% | 0.757 / 0.643 | 0.97 °C (raw forecast 1.14) |
| 23–29 Sep | °F | 66 | 31.8% | 31.8% / 48.5% | 0.791 / 0.665 | 1.07 °C (raw forecast 1.17) |

**What this says:**
- The prediction did **not** get worse; it got somewhat better in both units.
- It has **never** been as good as the market, in either unit, in any week measured.
- The corrections now in the price (P3.9 station correction, P2.9 MOS blend) bring the priced centre closer than the raw public forecast: 0.97 against 1.14 °C, and 1.07 against 1.17 °C. That is not enough to catch the market.

**Why it trails the market.** Measured on the venue's own record: `docs/MODEL_VS_MARKET_2026-09-28.md`, 8,406 city-days, 30 Dec 2025 to 26 Sep 2026, the pricing at local midnight.
- **Worse ladder.** Log loss is 1.460 for the model against 1.291 for the market. The model's top pick is right 40.7% of the time, the market's 46.8%.
- **The centre is the problem, not the width.**
  - The model's median bucket is on average 0.800 buckets from the winner; the market's is 0.671.
  - The model's median bucket is the winner 40.4% of the time; the market's 47.1%.
  - The width is roughly right: realised/stated variance is 0.909 for the model and 0.786 for the market.
- **The model adds almost nothing to the market's price.** A walk-forward, fitted each month on the months before, learned a weight on the model that fell to 0.02 at midnight and 0.00 at 08:00 by September.
  - The engine is built from the same public forecasts the market already sees.
  - The station-specific learning (P2.9, P3.9) has so far been too small to change that.

**What "so off" looks like on the page:**
- **Until 29 Sep 23:46Z,** the forward table and the hit/miss error columns showed the raw public forecast, not the centre the ladder was priced on.
  - Measured on 257 days: 71 differ by 1 °C or more.
  - Fixed by #283 (migration `20260929234616`). Days before 23 Sep have no recorded centre and show "not recorded".
- **The width when the forecasts disagree.** On such days the engine's ladder can be too narrow around one model.
  - Hassan's San Francisco case of 24 Sep: 81 °F favoured at 2c while NWS said 73.4 °F; the engine put 0.1% on the bucket the market favoured.
  - The engine has a disagreement multiplier (`v_forecast_divergence`, `scripts/probability_engine.py` ~L645 and ~L1410). It is **not applied** on the post-processed path, nor when the width is "measured": the reason `measured_width_kept:…_not_applied_to_sigma` records that.
  - How many of today's ladders take each path was not measured in this session. Count the reasons first.
  - Widths that grow with the spread between the tournament's forecasts exist only in shadow (P3.8).

### 1.3 "Why aren't US cities visible in the hit/miss table?"

There are two separate causes. Both are real and both are unfixed.

**(a) Every US day reaches the hit/miss table a full day late, since 25 Sep. This is a regression.**

*The measurement* (`fact_band_outcome.captured_at` against each city's local day end, 22–29 Sep):
- **Before 25 Sep,** US days were banked a median of 4.4–6.2 h after their day ended.
- **From 25 Sep,** a median of 24.0 h, every day.
- Celsius days take 10–13 h.
- On 30 Sep at 08:4xZ, as the browser role (`anon`), every US city's latest row is 28 Sep. 35 of the 37 Celsius cities' latest is 29 Sep; mexico_city and panama_city are on 28 Sep.

*The cause:*
- The table reads `v_city_hit_history`, built on `fact_band_outcome`.
- `scripts/databank.py` writes `fact_band_outcome` only in `pipeline_daily`.
- Since 25 Sep, n8n's P6.1 clock dispatches `pipeline_daily` at **04:36Z** (`n8n/P6.1_clock.template.json`, `pipeline_daily.yml "hours_utc": [4]`). Before that it ran around 09:24–11:14Z (the banked times above).
- A US local day ends 04:00–07:00Z, and the venue confirms it later still. So every US day misses that morning's run and waits for the next one.

*The confirmation is late too, not only the banking.* Measured later on 30 Sep:
- `markets.resolution_verified_at` for US markets came a median of 22.8–24.1 h after the local day ended (25–28 Sep). C markets took 10.3–13.0 h.
- The tick's P4.7 step verified only the first one or two US markets per day, at 10:36Z, 11:36Z or 13:36Z.
- The rest were verified by the next 04:4xZ daily run.
- The tick step is starved. Its `P4.7_confirm_recent` rows for 29 Sep 06:36Z to 30 Sep 08:36Z (15 runs):
  - 4 were skipped with a budget under 1 s;
  - the others reached 2–34 of about 150 candidates each (123–156 "unreached");
  - evidence captured was 0–8 per run.

So a fix must both **confirm** the US ladders and **bank** them. See `02_FIX_SPECS.md` F1.

*Why it was never fixed:*
- P4.7 (26 Sep, 6e314cb) found exactly this; the plan (v2.2) says "The Hit and Miss table, sorted newest first, therefore shows only C cities on its latest date".
- The P4.7 fix moved only the intraday **checkpoint** record into the hourly tick (`scripts/confirm_recent.py`).
- Its own progress note says: "The day-ahead record (`fact_band_outcome`, databank) still banks US days on the next daily run". Nobody acted on that line. The P4.7 row said its acceptance was never re-checked; this PR marks it not accepted.

**(b) "Hit rate, per city, per lead" shows 8 of 48 cities.**
- `web/app/predictive/page.tsx` renders `(scoreQ.data ?? []).slice(0, 120)` of `v_prediction_scorecard_all`, which is fetched with no `order`.
- As `anon` on 30 Sep, the view returns 797 active-city rows: 555 °C, 242 °F.
- The first 120 cover 6 °C cities and 2 of 11 US cities; the other 40 cities are cut with no warning.
- Which 8 appear depends on the row order Postgres happens to return.

**Not a cause:** the data itself. As `anon`, all 11 US cities have 14–15 settled days in `v_city_hit_history` and a row in `v_city_hit_summary`.

A third, smaller item is in the same section. "Actual against predicted" lists only the 8 cities with the largest bias (`accuracy.byCity.slice(0, 8)`). That is by design but not labelled as such.

---

## 2. Fix these first, in this order

The full specifications are in **`02_FIX_SPECS.md`**: root cause, evidence, design, files and lines, tests, acceptance, and the queries to prove each one. In short:

- **F1. US days in the hit/miss table the same day.**
  - Confirm recently ended US ladders with a real time budget, in a run that already exists.
  - Then bank them with databank's own logic and refresh the page cache.
  - The tick cannot do it (starved, §1.3a). `pipeline_intraday` (00:36, 04:36, 08:36, 12:36, 16:36, 20:36Z) is the natural home.
- **F2. Every city in "Hit rate, per city, per lead".** Order the query, show every row or group by city, and never slice silently.
- **F3. Then the prediction itself**, where the evidence points: the forecast tournament for °F (P3.8 part 2), the width that grows with disagreement, and S10 after the peak. Nothing here is proven to beat the market day-ahead.

---

## 3. Scheduled checks — do these by hand in the new session

Step by step in **`03_CHECKS_DUE.md`**:
- **1 Oct 05:30Z:** the books archive proof (P1.6 step 3.2) and the night's archive.
- **5 Oct 08:52Z:** the first weekly weather-model refit after the fix, and the city-clusters refit.

Every reminder this old session had is deleted, so nothing will act beside the new session:
- `trig_01T2ZhvBugbgzJQMgmmb22jp` (1 Oct);
- `trig_01Fq6KAS11o5WihcBfE34nvm` (5 Oct);
- `trig_017xLmFFuXrcJK75tLRbiDpC` (merge #289).

---

## 4. Waiting on Hassan (nothing below is built)

1. **One retry on HTTP 401 in `common.rest()`.**
   - Six refusals since 29 Sep 12:00Z. Each was the first request of a new process at :36, and three of them failed tick runs.
   - Facts are in audit row 7. The retry was proposed, not built.
2. **The edge gate's anchor (`tradeability_yes`, origin Hassan).**
   - Today `far_from_forecast` measures from `forecast_max_c − bias_applied_c`.
   - On the settled YES edge rows of 23–29 Sep, the winner sits more than 4 bands from that anchor on 18 day-ahead and 6 same-day rows.
   - The ladder's peak, `greatest(centre_c, observed_floor_c)`, gives 12 and 6 but lets 118 more losing rows through (of 23,189).
   - Numbers are in audit row 3.
3. **Getting under 450 MB (P1.6).** The structural step is moving closed markets (bands, verdicts and what other tables keep for them) to the repository, as phase 2 did for weather. The mapping is in PLAN_PROGRESS P1.6, step 3.4. `storage_pressure()` read 537.6 MB, 107.5% of the 500 MB tier, at ~09:00Z on 30 Sep.
4. **The verdict ledger's size** (`resolution_verdicts`, 19 MB, of which 9 MB is the primary key): see the same step 3.4 notes.

---

## 5. What is live now

- **Pricing** (`scripts/probability_engine.py`, in `pipeline_intraday`): four-hourly at :36 (00:36 … 20:36Z).
  - The centre stacks the public forecast with the P2.9 MOS blend and the P3.9 station correction.
  - Each ladder's reasons name the layers actually applied; they were not re-read on 30 Sep.
  - The ladder is `compute_band_probabilities` with the P3.1 floor atom on the same day.
  - P4.9 publishes the current prediction between runs.
- **Tick** (hourly at :36): stations, checkpoints, S10 shadow, `confirm_recent` (P4.7, 06–17Z), the engine's decisions (shadow), exits and trade prints.
  - S10's model is `rd1:2026-09-25:f5372ebb05` from the 08:36Z tick on 30 Sep (7 rows in `s10_shadow_checkpoints`); before that it was `389620c0d9`.
- **`pipeline_daily`** at 04:36Z: forecasts, settlement, databank, derived, and the nightly learners (MOS, station correction, tournament, strategy_learn). The archive runs at 02:36Z.
- **`strategy_learning` is off** (`settings`, read 30 Sep), so every learned strategy value returns its prior.
  - The market-anchor weight w is 0 in every scope until the forward gate passes: at least 20 forward days, so 40 days in all (#284).
  - The engine therefore trades nothing; its decisions are recorded in shadow.
- **Old desks s1 and s3–s9 are retired** (#262). The engine's ledgers have no trades.
- **Repair 5, checked on the 08:36Z run, 30 Sep.**
  - `signal_engine`: ok, no `earned_weights_error`, 0 strategies earning.
  - `paper_exits`: ok, positions 6 = skipped 6 (3 resolved, 3 sell preview not filled), 0 queued, 0 errors.
  - The edge log for a 400 on `v_signal_mark` was **not** checked: `query_logs` refused the `edge_logs` table name.
- **Actions minutes:** 2,926 of 3,000 scheduled a month on 28 Sep (#236). **Not re-measured since.**

---

## 6. Rules and traps learned the hard way

- **Both suites before every push.** `PYTHONPATH=scripts python -m pytest tests/ -q` (3,149 passed, 9 skipped on 30 Sep) and `npm ci --prefix tests/database --ignore-scripts && npm test --prefix tests/database` (20 PASS). Web changes also need `tsc`, `test:routes` and `next build`.
- **Check a changed view as `anon`** in the same `execute_sql` call: `begin; set local role anon; …; rollback;`.
- **Prove a replaced view with EXCEPT ALL both ways in one snapshot.**
- **Bulk updates of the six research tables** need `set local arbdesk.skip_capture = on` in the same transaction.
- **Look up column names before querying**; guesses cost time. Real names include:
  - `ingest_log.rows_written` and `detail`;
  - `strategy_params.fitted_at`;
  - `s10_shadow_checkpoints.decided_at`;
  - `fact_band_outcome.captured_at`.
- **Shell:** a variable set before `&` lives only in that background job. Use absolute paths for logs.
- **Large SQL results** are saved to a tool-results file; parse it with `json.loads(raw)['result']`.
- **Generated files** (`web/lib/provenance.ts`, `web/lib/sqlOwner.ts`, `sql/ad4_98_ui_health.sql`): run `tools/gen_provenance.py` and `tools/gen_sql_owner.py`; never hand-edit them.
- **`apply_migration` assigns its own version.** Verify the live statement's md5 against the file.
- **The mistake to avoid repeating:** a progress note that says "X still does not work" is an open bug, not a footnote. P4.7's note said exactly what was still broken, and it was left for four days.

---

## 7. Where things are

| Thing | Where |
|---|---|
| The hit/miss table | `web/app/predictive/page.tsx` (section "Hit and miss, day by day"); `sql/ad4_85_city_hit_history.sql` (`v_city_hit_history`, `v_city_hit_summary`, `mv_city_hit_history`) |
| Day-ahead record writer | `scripts/databank.py` (`fact_band_outcome`), step in `.github/workflows/pipeline_daily.yml` |
| US same-morning confirmation | `scripts/confirm_recent.py` (P4.7), inside the tick |
| Schedules | `n8n/P6.1_clock.template.json` (the CLOCK table); `tests/test_github_actions.py` (budget) |
| Pricing engine | `scripts/probability_engine.py` |
| Forecast tournament | `scripts/hit_tournament.py`; `derived_hit_summary`, `derived_hit_recipe` |
| S10 remaining-day model | `scripts/remaining_day.py`, `scripts/s10_shadow.py`, `data/models/remaining_day/current.json`, `tools/fit_remaining_day.py` |
| Model vs market study | `docs/MODEL_VS_MARKET_2026-09-28.md`, `tools/market_vs_model.py` |
| S10 replays | `docs/S10_REPLAY_2026-09-26.md`, `docs/S10_REPLAY_2026-09-30.md`, `scripts/backtest/replay_checkpoints.py` |
| Audit of 29 Sep and its repairs | the table at the top of `docs/PLAN_PROGRESS.md` |
