# WXPredict: the build document

**Status: v2.2, the final build document, 5 Oct 2026 ~17:30Z. Approved by Hassan 5 Oct (D0), with his decisions D1, D3, D4, D5 and D8 recorded in section 10.**
- v2.1 added wave A, the Actions revamp, which Hassan approved.
- v2.2 adds wave P (section 3.7): the paper desks, which Hassan reported on 5 Oct are not visible or working.

Nothing beyond phase 1 is built until he approves this document.

This document governs two things: the WXPredict build, and the completion of every other open item on the platform.
- **Section 3** is the register of every open item, measured on 5 Oct.
- **Section 5** puts all the work in one order.
- **Section 7** is the full test that ends the build.

A new session resumes from this document and from `docs/PLAN_PROGRESS.md`.

## How to use it

Every step has the same five parts: **what**, **how**, **verification**, **checkpoint** (what must be true before the next step starts) and **evidence** (where the result is written).

**Gates** (G1 ... G5) are Hassan's decisions. Nothing crosses a gate without his words in chat, quoted in `PLAN_PROGRESS`.

The **status table** at the end is updated in every PR that moves a step.

---

## 0. The rules this build follows

These come from CLAUDE.md and Hassan's messages, restated so nothing is lost.

| rule | what it means here |
|---|---|
| Numbers are measured | Every number in a PR, a doc or chat comes from a query, a test or a run, and says which. Anything not measured is written "not measured". |
| Nothing is assumed | Read the code and query the live data first. A doc or an earlier session's claim is a lead, not a fact. |
| Don't break what works | Before a live view is replaced: equivalence in one REPEATABLE READ snapshot, `EXCEPT ALL` both ways. A changed browser view is read as `anon`. After a change, check the next real run. |
| Never delete data | Rows leave Postgres only through the archive. Nothing here deletes. |
| Shadow is free, capital is Hassan's (Rule 6) | WXPredict may run in shadow without asking. Serving it, or anything touching the portfolio account, is Hassan's decision (G5). |
| Actions minutes are a hard budget (Rule 7) | 3,000 a month, CI included; October was at 941 billed by 5 Oct 16:04Z (R1). No new scheduled workflow (only P6.1 may add one). Every change to a scheduled job's runtime is measured before merge and written into `MEASURED_MINUTES`. |
| Adaptive never means unbounded (Rule 11) | Every learned parameter has a prior, hard bounds, a minimum sample, a maximum change per nightly update, and a version on every decision that used it. Nothing is evaluated on data it learned from. |
| Blind tests stay blind | rd3, `da_floor:v1` and `sd_corr:v1` are pre-registered. Their scores are not read before their first look (about 25 Oct; `docs/CHALLENGER_C_PREREG.md`, `docs/P11_DA_FLOOR_PREREG.md`, `docs/SD_CORR_PREREG.md`). WXPredict is compared with the market and the served engine, never with them, until they are unblinded. |
| The sealed test stays sealed | 1 Sep - 4 Oct 2026, and every listed day after it, is WXPredict's final test (section 4). No model output on those days is scored or looked at before G4. |
| Both suites before every push | `PYTHONPATH=scripts python -m pytest tests/ -q`; `npm ci --prefix tests/database --ignore-scripts && npm test --prefix tests/database`. For web changes also: `cd web && ./node_modules/.bin/tsc --noEmit && npm run test:routes && ./node_modules/.bin/next build`. |
| Generated files are regenerated | `python3 tools/gen_provenance.py && python3 tools/gen_sql_owner.py`. They are never edited by hand. |
| Few pushes | One validated push beats three speculative ones. Progress notes ride the work's PR. |
| Merging | Merge when CI is green on the PR's head and Hassan has said to merge. Codex's review is read and every finding is answered, but it does not hold a merge Hassan has ordered. |
| Secrets | No key value is ever printed. The Supabase secret key never goes in `NEXT_PUBLIC_*` or an n8n Config node. Email workflows stay disabled. |
| Focus | Only this document's work: WXPredict and the register's items (section 3), in section 5's order. Anything new that is noticed goes into the register, measured, and is not worked on out of order. |

---

## 1. Why: the engine today (measured 5 Oct, from the live data)

The edge check covered 2,315 calls, 24 Sep - 4 Oct, 48 cities.

1. **The market beats the engine at every checkpoint, on every day** (log loss behind by +0.42 to +1.19 per day).
   - When the two top picks disagree, the market is right 621 times and the engine 217.
   - Blending the engine into the market on paper gives the engine a weight of 0: it adds nothing the market does not know.
   - S10's rd1 trails the market by +0.373.
2. **After the peak, the engine ignores what it knows.** On 248 post-peak calls where the observed maximum was already inside the winning bucket:
   - the engine gave that bucket 0.621 on average and the market 0.903;
   - the engine's width stays about ±1.22 °C from morning to after the peak.
3. **The morning throws away the better forecast.** At local midnight the engine drops the evening-before station-corrected forecast for the raw public one. Mean absolute error of the centre, 28 Sep - 3 Oct: 0.99 °C (the raw forecast used) against 0.77 °C (the corrected one dropped).
4. **The nightly learning never reaches the price.**
   - The station correction and MOS are on for the day before only (`min_lead` 1).
   - The fitted station width is off.
   - The calibration fit (T = 1.141, over-confident) is not applied.
   - No fitted model is promoted, and `strategy_learning` is off.
5. **US near-impossible misses.**
   - On 64 of 555 US calls the engine gave the winning bucket less than 1%; the market did so twice.
   - The likely cause is the floor read from the NWS five-minute feed. That was fixed 4 Oct (#297).
   - Re-measured on the first graded day after the fix: 3 of 66 (R13). Not yet confirmed.
6. **Too many half-built models.** The engine, S10 (rd1/rd3), `da_floor`, `sd_corr`, MOS, the weather model, the trajectory, the hit tournament and the post-processing fit are each partly wired, and nothing combines them.

**WXPredict is the one model that replaces this patchwork as the same-day predictor.** The old paths keep running untouched until Hassan decides otherwise (G5). Nothing is retired or deleted by this build.

### Hassan's requirements, and where each is met and checked

| requirement (Hassan, 5 Oct) | design | verified in |
|---|---|---|
| Beat the market | Model A (weather) fused with the market (C), judged on the venue's winner against the market's own price at the same instant | phase 3.4, phase 4.1 |
| The market is a datapoint, and must be fresh | The market's price at the decision is an input. Training uses the venue's hourly snapshot at the decision instant. Serving reads the live price at the tick (phase 2) | phase 1 (the table), phase 2.1-2.3 |
| City-specific | City is a learned categorical input; per-city error and peak-hour history; per-city calibration checked | phase 3.2 ablation B, phase 3.5 |
| Market activity by time and date | Local hour, weekday, day of year; the market's moves over 1/3/6 h; bucket-hours that moved (an activity proxy); volume once a history exists | phase 3.2 ablation E, phase 2.4 |
| All weather parameters | Every field the station reports, plus the hourly forecast's temperature, dew point, cloud, sunshine, wind, rain and pressure, and the seven models | phase 3.2 ablations |
| Adapts to rises and falls | 1 h / 3 h changes, observed minus forecast now and 3 h ago, the running maximum and its age, the remaining forecast curve | phase 3.2, phase 3.6 (post-peak check) |
| Predicts the max from all weather permutations | One gradient-boosted model over all inputs together, so interactions are learned, not hand-coded | phase 3.2 |
| Reflected in the current UI | `/predictive` (the lineup and a WXPredict scoreboard), `v_prediction_contract`, the model registry | phase 5.4, phase 5.5 |
| Paper trades visible and working (5 Oct) | The page opens on a desk with trades, shows every desk's trades, and says why a desk is idle. The eight retired ledgers are retired. WXPredict trades its own shadow desk once it has a measured edge | wave P, phase 5.6 |

---

## 2. Where we are (5 Oct ~16:30Z)

### 2.1 Phase 1 is built: [#314](https://github.com/hassansab00/arbdesk4/pull/314), open

- **Head** `b80b2a7`: CI green, mergeable, `docs/WXPREDICT.md` describes the data.
- **Sources refreshed** (all committed):
  - **Market record:** to the events of 7 Oct, 10,325 events and 6,473,670 hourly prices. All 9,977 old events and 107,215 old series are unchanged, except the 143 that closed since.
  - **Station reports:** 50 settlement stations, every routine and special report with every field, 902,238 rows, 1 Jun 2025 - 5 Oct 2026.
  - **Station days:** 123,452, back to 2020.
  - **Hourly forecast record:** extended to 4 Oct. The 10,368 hours are new; the 2,304 overlapping hours matched exactly.
- **Table:** 686,111 rows; 9,586 listed events and 11,855 unlisted station days, 48 cities, 15 Jul 2025 - 4 Oct 2026. Two builds are byte-identical; sha256 `097799a77116...` is in `table_meta.json`.
- **Leak rules, each enforced by a test:**
  - a report counts from valid + 20 min (IEM measured: median about 11-12 min, the slowest of 75 took 15.6 min);
  - a forecast hour counts from H - 17 h (P2.9's assumption, not verified run by run);
  - the market is the hour's own snapshot (every price point is stamped 0-59 s past the hour, so decisions are at hh:01);
  - a past day counts only once whole.
- **Label:** the venue's winner. The station's maximum agrees on 9,410 of 9,586 listed days. `label_unit` takes the venue's side on the 176 that disagree; 118 of them are Shenzhen's, Mar - Aug.
- **Codex review:** 9 findings over four rounds.
  - Fixed and resolved: 7. One of them, the forecast offset, was checked against the API and not a bug; a guard was added anyway.
  - Open on `b80b2a7`: 2 (step 1.1).

### 2.2 First look, exploratory only (not a verdict, not pre-registered)

- **Setup:** one month, July 2026, on an earlier version of the table. A was fitted on everything before 30 Jun; the fusion was fitted on June's out-of-sample predictions.
- **Result:** log loss on the winning bucket, at decisions where the whole ladder was priced:

| decisions | rows | market | A alone | market recalibrated | fused |
|---|---|---|---|---|---|
| D-1 | 11,437 | 1.4294 | 1.7713 | 1.4264 | 1.4258 |
| D 00-05 | 8,563 | 1.3266 | 1.5629 | 1.3269 | 1.3232 |
| D 06-09 | 5,746 | 1.2844 | 1.5378 | 1.2890 | 1.2865 |
| D 10-12 | 4,284 | 1.1466 | 1.4543 | 1.1459 | 1.1444 |
| D 13-15 | 4,314 | 0.7539 | 1.1350 | 0.7506 | 0.7487 |
| D 16-18 | 4,305 | 0.1916 | 0.5226 | 0.1858 | 0.1845 |
| D 19-23 | 7,173 | 0.0080 | 0.2451 | 0.0033 | 0.0033 |

**What it says:**
- The weather model alone is far behind the market at every hour.
- Fused, it is ahead by only 0.001-0.005, and most of that comes from sharpening the market itself. The market is slightly under-confident; the 28 Sep study found the same, a ≈ 1.11-1.15.
- Earlier studies agree. The neighbour stations added nothing over the recalibrated market (`docs/NEIGHBOUR_STATIONS_2026-09-28.md`). The market moves half-way to a new reading in a median 11.2 min (`docs/MARKET_REACTION_2026-09-28.md`).

**The likely structural reason: the market sees the same-day model runs, and our history does not have them.**
- Our honest forecast record holds only runs started at least 24 h before each hour.
- Open-Meteo's single-run archive (`single-runs-api.open-meteo.com`), which would give the same-day runs historically, is **refused by this environment's network policy (403)**. Hassan can allow it under the environment's Network access, Allowed domains.
- The database holds the fresh current forecast's daily maximum every 3 h since 1 Sep only (`weather_forecasts`, `open_meteo_forecast`), which is about 5 weeks.

**Honest expectation:** WXPredict can be made better than the raw market on log loss; the first look suggests by a small margin. Whether it beats the market by a margin that matters is exactly what phase 4 measures. This document does not promise it.

### 2.3 Work in progress, not committed

- **Exploratory model code** (`tools/wxpredict/dataset.py`, `model.py`, `walk_forward.py`):
  - left in the working tree, excluded from git locally (`.git/info/exclude`);
  - copied to the session scratchpad as a backup;
  - written properly, with tests, in step 3.1.
- **Exploratory runs:** stopped at 15:53Z. **No score on 1 Sep - 4 Oct was computed**, so the sealed test is intact.
- **A check-in** on #314 is scheduled for 16:41Z (`trig_01QEquaiTSG2WfrKJCfaMeZT`).

### 2.4 Constraints measured this session

| constraint | measured |
|---|---|
| Open-Meteo previous-runs from this sandbox | answers one request at a time: 429 "too many concurrent requests" on back-to-back calls, 200 with 4 s between |
| Open-Meteo through pg_net, 48 at once | 9 of 48 answered, the rest 429 |
| IEM | quick; one request carries all 50 stations (~1-2 s for 10 days) |
| Polymarket gamma and CLOB | reachable; the price-history fetch for 4,995 buckets took 240 s |
| Table build (pure Python) | ~7 min |
| Weather-model fit (scikit-learn, 25 classes) | 179-404 s a month uncontended, on 260,000-630,000 rows |
| Python suite | ~2.5 min locally; the CI pytest job took 3.5-5.0 min on #314's three pushes (database contracts locally: not timed) |
| The tick | 45 s budget; ~1.01 billed min a run; no numpy or scikit-learn in its runtime (`requirements.runtime.txt` has `requests` only) |
| The production site and the database from this sandbox | **refused**: `arbdesk4-flame.vercel.app` and `jittmxhzgqpifitwupss.supabase.co` both answered 403 to CONNECT (the agent proxy's status, 5 Oct ~16:50Z). Production is read through the Vercel tool (`web_fetch_vercel_url`, which returns the HTML or JSON but runs no JavaScript) and the Supabase tool. A page is rendered here only on a local build, with recorded responses (wave P.3). |

---

## 3. Every open item (the register)

Each row gives an item's state, **measured on 5 Oct 2026 unless it says "per the record"**: the record is a `PLAN_PROGRESS` row that has not been re-measured.
- **Wave** says where it is done (section 5).
- **"Hassan"** means it waits on his decision (section 9).
- **"Fold"** means WXPredict replaces it.
- **"Close"** means verified done, and only the record needs correcting.

### 3.1 Budget and storage: these come first

| id | item | state | what remains | wave |
|---|---|---|---|---|
| R1 | **Actions minutes, October** (Rule 7) | 941 billed minutes from 1 Oct to 5 Oct 16:04Z, over 283 runs (jobs API, each job rounded up). **CI: 419** (tests 331, web 88). **Scheduled: 522** (pipeline_daily 153, tick 113, intraday 105, forecasts 71 before it left the clock on 4 Oct, archive 69, other 11). The cadence since 4 Oct costs about 91 a day at the per-run costs measured 1-5 Oct (tick 24 × 1, intraday 4 × 4, pipeline_daily ~37, archive ~14). That projects to about **3,300 by 31 Oct with no CI at all, over the 3,000 budget**. | **Hassan, 5 Oct: revamp the Actions first** (wave A, section 3.6), keeping quality and improving the engines' accuracy. The overage margin is still his to decide (D1). The build's own CI is capped: one push per step, about 25 pushes in total, which is about 125-150 CI minutes, measured as it goes. | A, Hassan |
| R2 | **Database size** (P1.6) | **575 MB, 114.9% of the 500 MB tier** (`pg_database_size`, 5 Oct). P1.6's record says phases 1-2 done and 3.1-3.3 live. | Measure the largest tables; finish P1.6 phase 3 through the archive (export, verify, commit, prune: never delete); get under the cap with headroom. WXPredict's shadow table must fit the plan. **5 Oct ~18:30Z (wave 0.2): 606.7 MB (606,702,739 bytes; 578.6 MiB), 121.3% of the tier.** Largest: `trades_observed` 67.8 MB, `book_snapshots` 58.8, `weather_forecasts` 47.4, `band_probabilities` 38.6, `weather_observations` 33.5, `resolution_verdicts` 24.6, `bands` 21.0, `weather_forecast_models` 19.6, `edges` 18.2, `derived_model_forecast` 17.8 (`pg_total_relation_size`). **6 Oct 13:37Z: 602,647,699 bytes (574.7 MiB); measured cuts and the Free plan's read-only rule in section 3.8; D9.** | 2 |

### 3.2 Jobs warning in the last 48 h (`ingest_log`, 5 Oct ~16:00Z)

| id | job | not ok in 48 h | latest reason | what remains | wave |
|---|---|---|---|---|---|
| R3 | `P2.9_honest_record` (the nightly forecast record WXPredict trains and serves on) | 2 of 2 partial | 5 Oct: 12 cities unreached (Open-Meteo refusals). The committed file is whole to 3 Oct, but 4 Oct has 40 of 48 cities. | It re-asks the last 10 days nightly, so check that 4 Oct fills tonight. Add the paced retry that `ingest_forecasts` got in #311 (one request at a time, measured pause). WXPredict's live inputs need every city every night. | 2 |
| R4 | `P2.10_ensemble_record` | 2 of 2 partial | unreached cities (ECMWF / GFS ensembles) | The same pacing; measure. | 2 |
| R5 | `ingest_forecasts` | 3 of 5 partial | model rows uneven (Météo-France 114, the others 228-266) | Read why per model; the #311 retry covers current runs; measure tonight. | 2 |
| R6 | `P4.7_confirm_recent` | 12 of 47 not ok (partial) | the tick's 25 s budget: due 10, asked 7, unreached 3. One 5 Oct 08:36Z row is missing: killed by its 40 s timeout, per the record. | Size the budget to the due queue, or carry the rest to the next run; log the exit code. | 2 |
| R7 | `P0.2_market_discovery` | 8 of 8 attention | 2 of 96 slugs not found: Zhengzhou, 5 and 6 Oct | Check whether the venue lists Zhengzhou under another slug. If it is delisted, record it as retired the normal way. | 2 |
| R8 | `P0.4_trade_history` | 3 of 48 attention | data-api 429 on one batch | Back off and resume (it already keeps a cursor); measure. | 2 |
| R9 | `P4.1_health_watchdog` | 8 of 8 attention | it sums the warnings above | Becomes meaningful with P6.3 (R14). | 2 |
| R10 | `edge_engine` | 3 attention | the against-market gate blocks the engine where the market is better (109 bands on 4 Oct). This is the gate working. | Nothing; it is "informational" under P6.3. | n/a |
| R11 | **Statement timeouts** (P6.5) | 49 `canceling statement due to statement timeout` in the 24 h to 5 Oct ~16:00Z (postgres log) | the queries are not named in that log | Name them from the API gateway log, then fix them the P6.5 way (stored rows behind views). Acceptance: 0 in 24 h. | 2 |

### 3.3 The served engine's defects (section 1)

| id | defect | state (5 Oct) | decision | wave |
|---|---|---|---|---|
| R12 | Post-peak collapse; morning centre; learning not reaching the price; too many partial models (defects 2, 3, 4, 6) | as in section 1 | **Fold**: WXPredict is the one same-day model. The old paths keep running untouched until G5. No interim patch to the engine unless Hassan asks for one (section 9, D4). | 3-6 |
| R13 | US near-impossible misses (defect 5) | **One graded day after the 4 Oct fix**: 3 of 66 US calls gave the winner under 1% (market 0). Before the fix: 71 of 617 (market 2), 10 dates (`v_checkpoint_outcome`). | Re-measure at 7 graded days after the fix; each remaining case is traced to its input. WXPredict's US check is in section 4. | 0 → 2 |

### 3.4 Plan steps not finished (from `PLAN_PROGRESS`; stale lines re-checked where possible)

Only one PR is open on GitHub (#314). **Every "PR open" in the record is stale.**

| id | step | record says | measured now | what remains | wave |
|---|---|---|---|---|---|
| R14 | P6.3 watchdog | todo | `P4.1_health_watchdog` runs, and every run is "attention" | The plan's spec: alert on the age of each job's last `ok` against a per-job SLA, and separate warnings from information. **Email stays disabled** (Hassan's standing rule), so it speaks on the board only. `log_run` raises if its own write fails. | 2 |
| R15 | P6.5 slow views | first cut, PR open | PR merged (not open); 49 timeouts in 24 h | R11 | 2 |
| R16 | P0.3 retire the paper desks | doing; Hassan must start `retire_desks.yml` | `retire_desks.yml` ran on 23 Sep (success). **0 open paper positions** (`paper_trades`): s1 107 closed (−$825.17), s3 41 (−$96.97), s4 49 (+$121.51), s12 1 ($0.00) | Verify each desk's state, then close the step in the record. **Closed 5 Oct (wave 0.1):** the 4 desks of 23 Sep are `retired` (15:38:59-15:39:00Z) with `archived_at`; 0 open positions on all 20 accounts. | 0 |
| R17 | P8.2 retire the eight old ledgers once flat | "not yet" | **they are flat now**: 0 open positions; and on 5 Oct, 0 live orders and 0 open plans | Retire them through `paper_desk_retire`, with the export first. This is covered by Hassan's 29 Sep "Retire all of s1, s3-s9", but confirm in section 9, D5. `retire_desks.py` as it stands would retire every desk (section 3.7). | P |
| R18 | P1.2 UI writes behind auth | doing | CLAUDE.md: every browser write goes through the operator route; sign-in off by Hassan | Verify the RPC revoke is live (anon cannot call a write RPC), then close. **Closed 5 Oct (wave 0.1):** 13 of 13 write RPCs not executable by `anon` or `authenticated`; A2 = 2 (the allowlist). | 0 |
| R19 | P1.3, P1.5 archiver floors and the push guard | doing; "the next archive run must log ok" | every `archive_*` job ok on 2 of 2 runs in 48 h (research, resolution, trades included) | Check the specific acceptance in each row, then close. **Closed 5 Oct (wave 0.1):** every `archive_*` job `ok` on 7 of 7 nights 29 Sep - 5 Oct; the prune runs after the commit (run 37256039634). | 0 |
| R20 | P1.7 mirror | live; 63 of 75 tables (per the record, 28 Sep) | `mirror_to_repo` ok on 2 of 2 runs | Re-count the coverage; decide on the 12 tables not mirrored (each named). **Re-counted 5 Oct (wave 0.2):** 107 live tables; 92 mirrored, 15 not, each named with its reason in `mirror_to_repo.NOT_MIRRORED`; 0 unaccounted. | 2 |
| R21 | P2.2 station agreement view | doing | not re-measured | Verify `sql/ad4_82` is applied, then close or finish. **Closed 5 Oct (wave 0.1):** merged #110 (23 Sep); `ad4_82` live; A4 99.0% (target 97%), 8 below, 6 above, 53 rows. | 0 |
| R22 | P2.8 every forecast model | live; acceptance ≥5 models with lead 1 for every date of the last 7 days | the committed record has 7 models × 48 cities through 3 Oct, and 4 Oct has 280 of 336 rows (R3) | Close once R3 holds for 7 nights. **Re-measured 5 Oct (wave 0.2), committed `models_daily.csv.gz` at lead 1:** 7 models x 48 cities on every date 26 Sep - 3 Oct (336 cells); 4 Oct 280 (40 cities). | 2 |
| R23 | P2.10 / P3.10 test (the ensemble record's first 30 days) | waits for 30 days of record | first night 29 Sep → about 29 Oct | Run as planned. | dated |
| R24 | P3.4 forward-only fits | live; applied 0 | trajectory 1,152 cells, applied 0; post-processing 399 cells, applied 2 (27 days) | **Fold**: WXPredict learns these inside one model. The fits keep running for the record. | n/a |
| R25 | P3.6 calibration | doing | `calibration_map`: T fitted 1.264, not applied (`applies: false`), because validation got worse (log loss 1.6822 → 1.6901). 16 settlement dates; the full refit needs 30. Next weekly update 6 Oct. | **Fold** for WXPredict (its own calibration, section 5 phase 3.5). The engine's map runs on as recorded. | n/a |
| R26 | P3.8 hit tournament part 2 | todo | v1 in shadow, nightly ok | **Fold**: WXPredict's scoreboard (phase 5.4) is the per-city learning loop. | n/a |
| R27 | P3.9 the width served | off; on when the forward score's lower bound is above 0 | `station_width_pricing` enabled false | **Fold** (WXPredict's width is learned). The engine's flag stays as is. | n/a |
| R28 | P4.3 repoint `v_city_hit_history` | waits for a week of checkpoints | 12 graded dates (24 Sep - 5 Oct) | Repoint with the equivalence check, or decide not to; this is the hit/miss page. | 2 |
| R29 | P4.5 restore the archived verdicts | pending (10,690) | `restore_verdicts` ok on 2 of 2 runs | Count the restored rows against 10,690, then close. **Closed 5 Oct (wave 0.1):** 7,355 + 679 + 2,656 = 10,690 verdicts from the three archive files are in `resolution_verdicts` (29,847 in all). The late-proof catch-up: not verified. | 0 |
| R30 | P4.6 part 2 (public-forecast hit rate and reliability bins per checkpoint) | not done | not re-measured | **Fold** into the WXPredict scoreboard (phase 5.4). | 5 |
| R31 | P6.2 n8n under 2,000 executions | doing | not re-measured | Count this month's executions; close or finish. **Measured 5 Oct (wave 0.2):** 184 executions 1 Oct 00:00Z - 5 Oct 17:26Z on the whole n8n instance (API count), about 1,200 a month at that pace; about 118 of them the four scheduled AD4 workflows, the rest Stratify/Steelwyre webhooks. | 2 |
| R32 | P6.6 docs clean-up | todo | stale lines found in `PLAN_PROGRESS` today (R15-R19, R29) | Wave 0 corrects the record. The wider clean-up comes at the end (section 7). | 0, 7 |
| R33 | P7.2-P7.9 S10 (rd1/rd3): shadow, portfolio, board, chain | 7.6-7.9 todo | rd1 and S10 run in shadow; **rd3 is blind until its first look, about 25 Oct** (trigger `trig_019QK37oqwVbAMcVWYnRQCVF`) | **Fold**: WXPredict generalises the remaining-day model. S10 keeps running in shadow and rd3's first look runs as pre-registered. After it, Hassan decides whether S10 is retired (section 9, D6). P7.7 (portfolio) is Hassan's (capital). | dated, Hassan |
| R34 | `da_floor:v1`, `sd_corr:v1` | blind, in shadow | first looks about 25 Oct | Run as pre-registered. Not read before then. | dated |
| R35 | P5.5-P5.12 part 2s, P8.3, P8.4 (the trading machinery: solver, timing, fills, learning, rails, the decision log, suite acceptance, conflict rules) | partial | trading only; nothing about forecasting | **Not in this build.** Listed so nothing is lost. They touch capital and trading, so they are Hassan's to schedule after G5 (section 9, D7). | Hassan |
| R36 | `settlement_verified` false | the gate for auto-settlement; `docs/settlement_verification.md` unmeasured | not changed | Hassan's (it pays out); listed only. | Hassan |

### 3.5 This session's own items

| id | item | state | wave |
|---|---|---|---|
| R37 | #314: two Codex findings on `b80b2a7` (the whole-day rule; `decision_local` at hh:01) | verified real (89 station days fail the rule, 1 Jun 2025 - 4 Oct 2026; re-measured 6 Oct: 81 with a gap and 8 with no report, of 24,550 station days, 24,542 of them with a row; the "24,518" first written here was not reproduced); fix designed (phase 1.1) | 1 |
| R38 | Exploratory model code (`tools/wxpredict/dataset.py`, `model.py`, `walk_forward.py`), outside git | locally excluded, backed up in the scratchpad | 3 (rewritten with tests) |
| R39 | `single-runs-api.open-meteo.com` refused by the network policy | 403 measured | Hassan (D3) |
| R40 | F3-A, dated checks, F4 (the 30 Sep handoff's items) | **F3-A:** `fec-v1` exists; WXPredict's contract (section 4) extends it. **Dated check B** (1 Oct archive) done 3 Oct per the record. **F4:** the 401s fixed (#292) and minutes decided (#302); the rest are R1-R11 above. **5 Oct (wave 0.1):** the tick logged 112 `ok` and 1 `attention`, 0 errors, 1 Oct - 5 Oct 16:36Z (`ingest_log`), so the 401s have not come back; `docs/FORECAST_EVALUATION_CONTRACT.md` (fec-v1) is on main. Dated check B: per the record. | 0 (record) |

### 3.6 The Actions revamp (Hassan, 5 Oct: "revamp the useless inaccurate actions and workflows ... as long as we maintain quality, don't cut corners, and improve the accuracy of the engines")

**What each scheduled workflow costs.** Measured 5 Oct: step times from each workflow's latest successful run (jobs API), schedule from `public.clock_schedule`.

| workflow | when (UTC) | cost | what its output feeds |
|---|---|---|---|
| CI `tests.yml` | every push to a PR | ~5 billed min a run (pytest ~148 s + database contracts ~105 s, median of 20 runs, 4 Oct); **331 billed in 1-5 Oct (69 runs)** | quality. It also runs on docs-only and data-only pushes. |
| CI `web.yml` | PR pushes touching `web/**` | 88 billed in 1-5 Oct (44 runs) | quality |
| `pipeline_daily` | 04:00 | **36.0 min a run** (run 37264229685) | mixed, see below |
| `archive_observations` | 02:00 | 14.7 min | the archive before every prune (the never-delete rule); the forecast record WXPredict trains on; the mirror |
| `pipeline_intraday` | 02, 08, 14, 20 | 3.3 min a run | the engine's prices (behind the market every day, section 1), the paper desks on them, the edges, the confirmations, the page refresh |
| `tick` | hourly | 0.7 min (billed 1) | checkpoint capture, the blind tests, WXPredict's shadow later |
| `observations`, `paper_trade_log` | 05:00 | about 1 each | observations, the trade log |
| `weather_model` | Mondays 08:00 | about 1 | the old desk weather model. **It feeds the engine's prices** (`probability_engine.py` and `tick.py` read it), so it stays until G5. |

`pipeline_daily`'s steps, the longest first (seconds):

| step | s | does its output reach a price or a page? (5 Oct) |
|---|---|---|
| Ingest forecasts | 627 | Yes: the current runs feed the day-ahead station correction and MOS (both on). Its archive passes wrote 0 rows on 7 nights (per the record), so most of the time is probably waiting on Open-Meteo. **Not measured.** |
| The honest station model | 297 | yes (MOS, on) |
| Venue-confirmed settlement sweep | 260 | yes (settlement and evidence) |
| The hit tournament | 185 | **no**: shadow only |
| Fit the intraday trajectory | 177 | **no**: 0 of 1,152 cells applied |
| Authoritative weather outcome evidence | 138 | yes (truth labels) |
| Derived recompute | 107 | yes |
| Freeze settled city-days | 94 | yes (scoring) |
| Measure forecast skill | 54 | not verified (a page may read it) |
| Replay yesterday's engine decisions | 41 | not verified |
| Station correction per model | 31 | yes (on) |
| Re-fit calibration map | 25 | **no**: `applies: false` |
| Promote or shadow fitted models | 23 | **no**: 0 promoted ever |
| Learn strategy parameters | 22 | **no**: `strategy_learning` off |

**Workflows nothing schedules:** `backtest`, `live_weather`, `verify_resolution_source`, `paper_fill`, `retire_desks`, `restore_edge_marks`, `forecasts` (off the clock since 4 Oct).
- **`paper_fill.yml` is dispatched by the web app** (`web/app/api`), so it is in use.
- Whether n8n dispatches any of the others is **not measured**.

**What the revamp must not do** (Hassan's conditions):
- remove, skip or weaken a test;
- delete data;
- change a served price without the evidence and the gate that any price change needs.

**What improves accuracy** is WXPredict (phases 2-5). The revamp moves minutes from things nothing uses to the build, and never makes a served price worse.

#### A.1 result: every step classified (measured 5 Oct, 17:00-18:30Z)

**Sources.** Step times: the jobs API, `pipeline_daily` runs 36522348364, 36669569909, 36815869294, 36965258009, 37097080037, 37177477832 and 37264229685 (29 Sep - 5 Oct); `pipeline_intraday` runs 37256039636, 37284638004 and 37326006256 (5 Oct); `archive_observations` run 37256039634. The tick's own time: `ingest_log` job `tick`, `detail.seconds` and `detail.engine.seconds`, 24 runs 4 Oct 17:36Z - 5 Oct 16:36Z. Live state: the Supabase tool. Readers: a code search of `scripts/`, `web/`, `n8n/`, `sql/` and `supabase/migrations`, and `pg_depend` on the live database. Dispatchers: `clock_schedule`, `cron.job`, the n8n API and the web app's routes.

**Who starts what.** The clock is pg_cron `ad4_clock` in Supabase (`20260926130000`), at :36. The n8n workflow "AD4 P6.1 - Clock" is inactive. n8n "AD4 P2.1 - Relearn" (active, webhook only) can dispatch `pipeline_daily`, `weather_model`, `pipeline_intraday`, `observations`, `backtest`, `archive_observations` and `forecasts` when a Run button on the site asks; it logged `P2.1_relearn` twice, last on 26 Sep. The web app also dispatches `pipeline_intraday` (`/api/paper-run`) and `paper_fill` (`/api/paper-cycle`) directly.

**`pipeline_daily`: 24-37 billed a night, mean 31.0 (7 nights).** Seconds are the median of the 7 nights, with 5 Oct beside it.

| step | s (5 Oct) | class | evidence |
|---|---|---|---|
| Ingest forecasts | 284 (627) | keep, **trim (A.4)** | Feeds the station correction and MOS, both `enabled` (settings, live). 5 Oct was its first night as the whole ingest: pass 1 logged at 04:46:36Z, about 608 s after the step began, 1,949 rows, 3 chunks unreached; pass 2 took 18 s for 150 rows. Where pass 1's time goes: not measured yet. |
| Weather outcome evidence | 138 | keep | the truth labels |
| Venue-confirmed settlement sweep | 271 | keep | settlement and evidence (901 s on 29 and 30 Sep, its budget) |
| Measure forecast skill | 45 | **keep** | The engine prices on the newest `derived_forecast_skill` row per city and lead, with no switch and no age limit (`probability_engine.py:1126-1134`): bias, width and whether a band may trade. `prune_forecasts` refuses to prune when skill is older than what it would delete. |
| Promote or shadow the fitted models | 20 | **stop (fold)** | The price reads only `v_model_promoted` (`state = 'promoted'`, `probability_engine.py:485`). Live: 392 rows, 0 promoted (197 stale, 195 shadow). Nothing can be promoted while `TRAINS_ONLY_ON_ADVANCE_INFORMATION = False` (`weather_model.py:282`, `model_promotion.py:280`). The pages that show a city's promotion state (`v_model_disagreement`: `CityCards.tsx`, `Reasoning.tsx`) would keep showing the last night's state. |
| Rebuild the early record | 5 | keep | |
| Freeze settled city-days | 95 | keep | scoring |
| Re-fit calibration map | 22 | **stop (fold)** | The price applies the map only when `applies` is true (`probability_engine.py:124-127`). Live: `applies` false, T 1.141. **The fit sets `applies` itself when its own validation passes** (`calibration.py:434`), so stopping it also stops an automatic path to the price. WXPredict calibrates itself (R25, phase 3.5). |
| Learn strategy parameters | 9 | **stop (fold)** | Every loader returns before reading `strategy_params` while `strategy_learning.enabled` is false (`learned.py`, `city_clusters.py:262`, `market_anchor.py:384`, `belief.py:255`). Live: false. s2 and s10 do not read the table. |
| Fit forecast post-processing | 15 | keep | 2 of 399 cells applied (live), so it reaches the price |
| Fit the intraday trajectory | 153 | **stop (fold)** | The price reads `v_city_trajectory_now` where `trajectory_applied` (`probability_engine.py:739-746`). Live: 0 of 1,176 cells applied. **A cell is applied by the fit's own walk-forward gate, with no switch** (`trajectory.py`, `walk_forward.gate`), so stopping it also stops an automatic path to the price. |
| Derived recompute | 89 | keep | |
| Station correction per model | 31 | keep | `station_correction_pricing` enabled |
| The honest station model | 293 (297) | keep, **trim (wave 2)** | `station_mos_pricing` enabled. 23-26 s on 3 nights, 293-313 s on 4; on 1 and 4 Oct it hit its 5-minute timeout and failed the job (its Open-Meteo current-run reads). |
| Score the station width forward | 15 | keep | the evidence `station_width_pricing` (off) waits on |
| Replay yesterday's engine decisions | 42 | **keep (Hassan may decide)** | It writes one `ingest_log` row and no program reads it, so by A.3's rule it could stop. It is the nightly live acceptance check of the decision engine (P5.12), and stopping a check is the corner Hassan said not to cut. |
| Refresh observation trust | 4 | keep | |
| The hit tournament | 162 | **stop (fold)** | Writes `derived_hit_*` only. Readers: `v_data_freshness` and the mirror; RLS keeps them from the browser; the engine does not read them (`hit_tournament.py`). |
| Strategy lifecycle, meta-allocator, queued backtests | 3, 3, 1 | keep | The meta-allocator touches the portfolio account: not this build's to change (Rule 6). |
| Refresh the stored page rows | 14 | keep | |

**What stopping a step also needs.** All five are rows in `clock_expected_jobs` (`measure_skill` too), so a stopped step would read `missing` on every run and turn the watchdog to attention (`20260930001000`: "A step switched off on purpose must have its rows removed here"). Their tables have freshness limits in `ad4_39_freshness.sql` (48 h, 30 h, 200 h), so the docs page would call them stale. A.3 handles both.

**The five stops together:** 366 s a night on the medians (432 s on 5 Oct), about 6-7 billed minutes a night. Estimated; measured after.

**`pipeline_intraday`: 200-228 s, 4 billed a run (3 runs).** Every step keeps a price, the edges, the board or the paper desks. Seconds (median of 3): model forecast 9, band probabilities 40, edges 32, evidence capture 7, paper exits 3, **strategy signals 53**, paper plans 2, paper fill 1, paper settlement 0, confirmations 2, bank ladders 24, page rows 18. The paper-desk steps (exits, signals, plans, fill, settlement) are about 60 s of a run: about one billed minute in four (A.5, D8).

**`tick`: one billed minute a run.** `tick.py` took 20.6-34.4 s; the engine strategies' decisions (the desks of s2, s10, s11, s12) took 2.6-9.6 s of it. Removing them would not change the billed minute (D8).

**`archive_observations`: 885 s, 15 billed (5 Oct).** Every step keeps the archive (the never-delete rule), the mirror or the training record. Longest: export 250 s and the honest training record 244 s; the ensemble record 92 s. Keep; the two records' Open-Meteo waits are wave 2.B.

**`weather_model` (Mondays): 1 billed (5 Oct, 55 s).** Keep: it feeds the engine's model forecast.

**Workflows the clock does not start (A.6).** `paper_fill` (web app), `backtest` and `forecasts` (n8n Relearn, a Run button), `retire_desks` (P.2 needs it), `restore_edge_marks` (a one-off, ran twice on 29 Sep), `live_weather` (last run 5 Sep, no dispatcher found), `verify_resolution_source` (never run, no dispatcher found). A workflow nobody starts bills nothing, so A.6 saves 0 minutes: it is housekeeping only.

**A.6 (6 Oct): the list.** The step's rule disables each workflow that neither the clock, the web app nor n8n starts. Re-checked against the repository:
- **Started by something, so kept:**
  - `paper_fill` by the web app (`web/app/api/paper-cycle/route.ts`);
  - `backtest` by n8n's Relearn and a `repository_dispatch`;
  - `forecasts` by n8n's Relearn (its own header: started by hand since 4 Oct).
- **Started by nothing, so the rule disables them:**
  - `retire_desks`: P.2's tool, already used for D5's retirements;
  - `restore_edge_marks`: a recovery tool, pinned by `tests/test_restore_edge_marks.py`;
  - `live_weather`: its own file says it is "kept as a manual run: it is the fallback when n8n is down";
  - `verify_resolution_source`: the improvement plan says "Keep `backtest.yml` and `verify_resolution_source`", and `docs/settlement_verification.md` calls it the one-button check.

**A.6 done 6 Oct: two turned off in the repository; `retire_desks` and `verify_resolution_source` kept on.** Hassan, 6 Oct, asked what replaces each, then: "do what's necessary as long as nothing's broken". This session cannot press GitHub's "Disable workflow" (no tool for it; Actions writes return 403), so each of the two jobs is `if: false` in its file, with the reason and how to turn it back on. A dispatch skips the job and bills nothing. What does each job now:
- `live_weather`: the hourly tick's NWS monitor step (`tick.yml`, `ingest_nws_monitor`) writes `live_weather`; it logged `P1.2_nws_monitor` 84 times in the 7 days to 6 Oct 14:36Z.
- `restore_edge_marks`: its two recovery runs were on 29 Sep, and the cause is fixed (`20260929220000` freezes the marks before the prune).
- `retire_desks` stays on: it is the only way to retire a desk, since the service key lives only in the repository's secrets.
- `verify_resolution_source` stays on (Codex on #322). It has never run, but it is the only test of the weather.gov timeseries parser (`settlement.fetch_resolution_source_reading`), which `settlement_verified` depends on (`docs/settlement_verification.md`; D7, R36). pipeline_daily's nightly trust refresh compares our stored station maxima with the venue's verdicts. It does not exercise that parser, so it is not a replacement.

`tests/test_github_actions.py` (`TURNED_OFF_WORKFLOWS`) pins exactly these two, and `tools/gen_provenance.py` now describes a turned-off workflow as "never - turned off (wave A.6)".

**CI (A.2), measured.** October to 5 Oct 16:43Z: `tests.yml` 72 runs, 347 billed (53 pull request, 19 push to main before #313); `web.yml` 44 runs, 88 billed. Replaying the path rules over the 72 test runs with local git (each PR run's diff from its merge base, each push's own commit):
- **(b) docs only:** 2 PR runs (10 billed) and 2 pushes (11 billed) touched nothing a test reads. The suite reads 8 files under `docs/` (traced, below); not `PLAN_PROGRESS.md`, this document or the root `*.md`.
- **(a) the contracts:** 12 of the 51 code PR runs touched nothing the contracts read. **But since #302 the contracts run beside pytest, and pytest is the longer leg** (job 111872816946: the paired step 211 s; setup-node 6 s and `npm ci` 1 s). Skipping them saves about 7 s a run, which rarely changes a billed minute. The ~105 s this step assumed is the job before #302.
- **What the suites read, traced:** pytest with an audit hook (3,429 passed, 155 s locally) opened 1,675 repo files, 8 of them in `docs/`. The contracts with an `fs` hook read 299 paths, all under `supabase/migrations`, `sql`, `tests/database` and `data/repairs/2026-09-29-day-features`.

### 3.7 The paper desks: not visible, not trading

Hassan, 5 Oct: "the paper trades aren't even visible or functional in arbdesk4 ... they haven't been for days, so we need to fix this too".

Measured 5 Oct, 16:45-17:10Z, from the live database (Supabase tool) and the production API (Vercel tool).

**What works:**
- `GET /api/paper-desk?resource=accounts` on `arbdesk4-flame.vercel.app` answers 200 with 16 desks.
- The repository's trade log, `/paper-trades/index.json`, answers 200 with 198 trades (generated 1 Oct 05:36Z).
- `anon` reads all 198 rows of `paper_trades`.
- Every paper job ran `ok` in the 48 h to 5 Oct ~16:50Z: `paper_plans` 10 runs, `paper_worker` 10, `paper_exits` 10, `paper_settlement` 12, `signal_engine` 10.

The pipe is not broken. What is wrong is what the page shows and what reaches the pipe:

| id | item | measured | what is done | wave |
|---|---|---|---|---|
| R41 | **The page opens on an empty desk** | `page.tsx` picks the first "live" desk (automatic and not paused). All 15 shadow desks are live, and the counts that would break the tie are absent on this path (the route reads `paper_accounts`; `route.ts` says why). The first desk in the API's answer on 5 Oct was "Shadow: s8_two_bucket_cover": 0 trades, 0 orders, 0 plans. Nine of the 16 desks have never traded. Nine desks share the same `created_at` (24 Sep 20:30:32.309906), so which one opens is not even fixed. The 198 trades sit on s1 (43), s3 (41), s4 (43), s12 (1) and the archived "Wide edge, all US" (70). No view shows every desk's trades together. | P.3 | P |
| R42 | **The page says "Running" for desks that can never trade** | `deskState` (`PaperDeskControl.tsx`) reads the desk's mode, pause, policy and cash, never its strategy. The eight desks of s1 and s3-s9 (strategies `enabled` false since 29 Sep, migration `20260929080000`) all show "Running on its own. Eligible proposals are queued and filled". For the seven desks whose strategies are switched on, nothing says why they have not traded. | P.4 | P |
| R43 | **No paper trade has opened since 29 Sep 22:36Z** (s12_no, Seattle, closed 30 Sep, net −$0.0037) | **Cause 1:** on 29 Sep Hassan retired s1 and s3-s9 ("Retire all of s1, s3-s9", P8.2 step 5). Their last decisions were at 04:37Z. They made 197 of the 198 trades in Postgres (the 70 on the archived desk included). **Cause 2:** since 30 Sep 00:00Z the switched-on strategies made 11,591 decisions and **0 BUY**: s10 ×3 1,461 each (1,420 NONE and 41 WAIT, the last WAIT on 1 Oct 14:36Z); s11 ×2 1,461 each, all NONE; s12 1,461 (1 HOLD, the rest NONE); s2 2,825 NONE. Reasons, s10_winner over 7 days: `own_rule_none` 1,386, `no_ladder` 334 (all 48 cities), `own_rule_wait` 60, `no_trade_band` 12, `against_market` 1. Reasons over 48 h: s11_lock `no_trade_band` 506; s11_ladder `no_trade_band` 471, `nothing_tradeable` 32, `against_market` 3; s12 `no_trade_band` 478, `nothing_tradeable` 25, `against_market` 3; s2 `no_signal` 851. They decide on the engine's prices (`prediction_source`: `prediction_checkpoints` or `s10_shadow_checkpoints`), which the market beats every day (section 1). A strategy that finds no edge at those prices doing nothing is the decision engine working as designed. | P.5, then 5.6 | P, 5 |
| R44 | **Is each NONE right?** | **Checked 6 Oct (P.5, `docs/P5_EACH_NONE_2026-10-06.md`): yes, by the engine's own rules.** Over 7 days (5,364 decisions of s11_ladder, s11_lock and s12 on 1,788 checkpoints, every one on the market prior w = 0), every decision with no edge on its side was NONE. s11_ladder's 16 YES edges are all under h (best λ-Kelly 0.00063); no s11_lock book exists (cheapest cover 1.0079). s12's 255 NO edges: 87 only on dead buckets, 18 gated, 118 under h, 2 stopped by the robust sizing (α 0.25), 1 traded (Seattle, lost $0.0037). s10's `no_ladder`: 320 of 334 are `d1_eve`, which S10's model never prices (by design); 7 are Madrid after its peak (no fit for local hour 18, a real gap); 7 had too few readings. s2: on each decision's own book (`book_snapshots` as of its time), no basket was under its payout (cheapest YES 1.0026). Three named questions for Hassan, no rule changed. | P.5 | P |
| R45 | **Trades resume on an edge, not on a looser rule** | Loosening a threshold so that trades appear would bet on prices measured worse than the market (section 1). That is the corner Hassan said not to cut. | WXPredict gets its own shadow desk once G4 shows an edge (5.6, Rule 6: shadow is free). The old engine strategies run on as D8 decides. | 5 |

**R17 moves to wave P.** `tools/retire_desks.py retire` retires **every** desk in `paper_accounts`, and then switches off s1-s9. It was built for P0.3 on 23 Sep, before the 24 Sep shadow desks existed. Run as it is today, it would also retire the seven desks of switched-on strategies and the Portfolio desk. Step P.2 therefore adds a targeted mode first.

All eight ledgers pass `paper_desk_retire`'s refusal checks today (5 Oct): 0 positions with shares, 0 queued or working orders, and 0 plans pending approval or queued.

**P.1, re-measured 5 Oct 17:58-17:59Z** (Supabase tool; the production API through the Vercel tool). Nothing has changed in substance since the measurements above; the counts grew with time only.
- **Decisions since 30 Sep 00:00Z: 11,645, 0 BUY.** s10 x3: 1,429 NONE and 41 WAIT each; s11 x2: 1,470 NONE each; s12: 1,469 NONE and 1 HOLD; s2: 2,825 NONE. The last WAIT or HOLD: 1 Oct 14:36Z.
- **Trades:** 198; the last opened 29 Sep 22:36:49Z; 0 open positions on 20 accounts. Orders: filled 186, expired 23, partial 13, rejected 4. Plans: filled 185, blocked 1,011, expired 78, partial 13, rejected 4.
- **Reasons, 48 h:** s10 x3 `own_rule_none` 402-410, `no_ladder` 96 each, `no_trade_band` 8 (growth, winner); s11_lock `no_trade_band` 506; s11_ladder `no_trade_band` 471, `nothing_tradeable` 32, `against_market` 3; s12 `no_trade_band` 478, `nothing_tradeable` 25, `against_market` 3; s2 `no_signal` 851. **7 days:** s10_winner `own_rule_none` 1,384, `no_ladder` 334, `own_rule_wait` 60, `no_trade_band` 12, `against_market` 1.
- **Paper jobs, 48 h, all `ok`:** `paper_plans` 10, `paper_worker` 10, `paper_exits` 10, `paper_settlement` 12, `signal_engine` 10, `P2.2_paper_maintenance` 8, `export_paper_trades` 4.
- **The API:** `GET /api/paper-desk?resource=accounts` 200, 16 desks; the first is still "Shadow: s8_two_bucket_cover" (0 trades).



### 3.8 Storage (2.A), measured 6 Oct: the archive cycle alone cannot reach the checkpoint

**Where it stands.**
- `pg_database_size` read 602,647,699 bytes at 13:37Z and 13:47Z. That is 574.7 MiB, 114.9% of the tier in `storage_pressure()`'s unit (MiB).
- **The project is on Supabase's Free plan** (`get_organization`: plan `free`). Supabase's docs ("Understanding Database and Disk Size", Free Plan behavior) say: "Free Plan projects enter read-only mode when your database size exceeds 500 MB".
  - The database has been over 500 since at least 20 Sep (591 MB, in `tests/test_every_pruned_table_is_reclaimed.py`).
  - It still accepts writes: the 13:36Z tick wrote trades and decisions.
  - Why it is not read-only yet is not measured. Supabase's billing FAQ describes a notice and a grace period before restrictions.
- **The checkpoint is under 90% of the tier.** P1.6's acceptance reads it as `storage_pressure()` under 450, which is 450 MiB.
  - The gap now is **124.7 MiB** (130.8 MB).
  - If Supabase counts the 500 MB in decimal, the gap to 450 MB is 152.6 MB. Which unit Supabase uses is not measured.
- **It grows.** The readings were 542.2 MiB on 30 Sep, 558.9 on 3 Oct, 578.6 on 5 Oct ~18:30Z and 574.7 on 6 Oct 13:37Z.
  - That is about 5 MiB a day over 6 days.
  - The readings were taken at different times of day, and `trades_observed` swings by about a day of prints between prunes.
  - Over the last 7 days, the tables that are never pruned added rows worth up to 9.2 MB a day (rows × the table's bytes per row). That is an upper bound, since some of them rewrite rows.
- **Reclaim already works.** After each prune the archive asks for a VACUUM FULL (`request_reclaim`), so the size is live rows and their indexes. Every large index was scanned on 6 Oct, so dropping indexes is not a free cut.

**What each cut gives.** Rows were counted 6 Oct ~13:45Z. MB is the rows' share of their table's measured size, weighted by `pg_column_size`.

*Group A: the archive cycle only, and every reader's window is still met.* Each cut needs a migration to lower its prune function's floor, plus a before-and-after proof on its readers.

| cut | rows | MB | readers |
|---|---|---|---|
| `decisions`: keep 30 → 3 days, floor 14 → 2 (while the database is over its high-water mark the archive keeps the floor) | 33,056 of 36,763 at 2 days | 11.1 | The longest reader needs 1 day: `engine_replay_live.py` (yesterday) and the paper-desk API (24 h). The order and exit functions read the decision they are handed, minutes old. `v_decision_prediction` reads every row, but no code reads it. |
| `band_probabilities`: keep and floor 30 → 18 days, by the market's date | 24,785 of 139,275 | 7.0 | `station_width_score.py` reads every price of the markets resolved in the last 14 days, priced from 3 days before; a market 18 days past its date is 4 days beyond that. Longer readers use rows the prune keeps. |

Group A in total: **18.1 MB**. This is the first PR (migration `20261006170000`): decisions keep 3 days with a floor of 2, and band probabilities keep 18 days.

`weather_forecast_models` moved to group B when it was built. `ingest_forecasts.first_held_date` takes the later of the two forecast tables' oldest days, and nothing older is fetched. A 7-day keep would shrink the forecast ingest's catch-up window from about 30 days to 7 for both tables. The ingest must first keep a window per table.

*Group B: a reader sees less, or needs a code change first.*

| cut | rows | MB | what changes |
|---|---|---|---|
| `derived_city_correlation`: keep the newest row per pair | 60,939 of 62,314 | 8.8 | `signal_engine` reads the newest row per pair, which stays. The plan-approval RPC's correlation warning (`sql/ad4_rpc.sql:480`) reads the 20 newest rows of any computation, so its warning can change. The table is mirrored, not archived, so this needs a new archive dataset and a reclaim job. |
| `derived_model_forecast`: keep 7 days | 9,711 of 32,126 | 5.5 | Never pruned today. Model promotion read every row, and it is stopped (A.3). `v_model_forecast_skill` reads every row for the analytics page, which would show 7 days unless it reads the mirror. Needs a new archive dataset. |
| `book_snapshots`: the closing books older than 3 days | 53,402 of 86,706 | 25.9 | Kept forever by design, and about 1,500 more a day. Two backtest readers read these rows, and both need a change first. (1) The book reader (`book_history.as_of`) assumes the last book of every band-day stays in the database; its proof (`tools/p16_step32_proof.py`) must be re-run. (2) The regime's book age (`regime.py` `_latest_book_age_minutes`) reads `book_snapshots` directly at `observed_at <= as_of`, so on a replayed date it would find no book and silently drop its `stale_book` downgrade (Codex, #321). |
| `weather_forecast_models`: 30 → 7 days | 57,700 of 94,309 | 11.9 | `ingest_forecasts.first_held_date` is the later of the two tables' oldest days, and the catch-up never fetches below it. So a 7-day keep would shrink the forecast backfill's window to 7 days; the ingest first needs a window per table. Its other readers are covered: `station_correction.py` reads the archive, and the hit forecasts are frozen. |
| `weather_forecasts`: 30 → 14 days | 31,994 of 97,748 | 15.4 | `recompute_correlation` reads 180 days straight from Postgres, with no archive, into `derived_city_correlation`, which s2's signal path reads. A shorter keep changes s2's inputs (D4, D8) unless that function first reads the archive. |

Group B in total: **67.5 MB**.

**A and B together: 85.6 MB.** That is 45 MB short of the gap in MiB terms and 67 MB short in decimal, before growth.

*Group C: beyond the archive cycle.*
- **`trades_observed`.** It was 60.0 MB at 13:37Z, with 52,003 prints in the last 24 h.
  - Its readers need 24 h (`v_band_volume`, `v_city_volume`).
  - The nightly prune keeps everything from the previous UTC midnight, so the table holds 27-51 h of prints.
  - Holding less needs a prune more often than nightly (a schedule, so Rule 7) or fewer bytes per print. Its indexes are 33.7 MB of the 60, the dedupe index alone 18.7 MB. What either would save is not measured.
- **The Pro plan.**
  - $25 a month (Supabase docs, "Your monthly invoice"), with an 8 GB disk.
  - Its $10 compute credit covers one project on Nano or Micro compute. This project's compute size was not checked.
  - It removes the 500 MB limit. It is spending, so it is Hassan's decision (D9).

**Recommendation: the Pro plan (D9).**
- The limit is the root cause: the database grows about 5 MiB a day, and WXPredict's shadow table is still to come.
- Without Pro, groups A and B are several PRs, each with its own proof. Two of them change what s2 or a page reads, and together they still leave the gap open.
- Group A changes nothing any reader sees, so it can go ahead either way if Hassan wants the room.

---

## 4. The evaluation contract: frozen before any model is judged

This becomes `docs/WXPREDICT_PREREG.md` in step 3.0. It is committed before any fold of the development run is scored.

- **Rows scored.** Listed events (`source = venue`) whose whole ladder was priced at the decision (`mkt_complete`); every decision hour of D and D-1.
- **Truth.** The venue's winning bucket only.
- **Comparators:**
  - **the market:** its snapshot at the same instant, floored at 0.0005 and normalised;
  - **the market recalibrated:** a power per hour group, fitted on earlier months only;
  - **the served engine**, on the days it has graded checkpoints (`v_checkpoint_outcome`), at its own checkpoints only.
- **Primary metric.** Log loss on the winning bucket (fec-v1: probabilities floored at 1e-6). For each date, the mean per-row difference market minus WXPredict; positive means WXPredict is better.
- **Interval.** Whole dates resampled: `tools/fec_same_day.cluster_boot`, seed 11, 1,000 resamples, 90% interval.
- **Secondary metrics:** top-1 hit rate with a Wilson interval, Brier score, a reliability table (10 bins), per city, per hour group, C vs F.

**Splits:**
- **Development:** test months Jan - Aug 2026. Every model choice (features, form, hyper-parameters, fusion form) is made here only.
- **Sealed test:** 1 Sep - 4 Oct 2026, plus every listed day added before G4. It is scored once, after G3, with everything frozen.
- **Walk-forward inside each split.**
  - Month M's weather model is fitted only on rows whose day ended before M began (the day before M's first day is left out).
  - The fusion and calibration for month M are fitted only on earlier months' out-of-sample rows.

**"Better than the market"** means all three of the following on the sealed test:
- (a) the 90% interval of the per-date mean gain over the market is above 0 overall;
- (b) no hour group's interval lies wholly below 0;
- (c) the gain over the *recalibrated* market is reported beside it. That is the part that is weather, not market sharpening.

**Named checks**, reported whatever the verdict:
- **Post-peak** (the engine's defect 2): D rows from 2 h after the city's typical peak where the running maximum already sits in the winning bucket. WXPredict's mean probability on the winner against the market's and the engine's (0.621).
- **Morning centre** (defect 3): mean absolute error of WXPredict's expected maximum at 09:00, against the engine's raw centre (0.99) and the corrected one (0.77) on the same days.
- **US** (defect 5): the number of US rows where WXPredict gave the winner under 1%, against the market's count.
- **Per city:** gain and interval for every city with 20 or more scored dates.

**What a pass does not mean.** A log-loss gain is not a trading edge: costs, spreads and fees are not in it. Trading on WXPredict is outside this build and is Hassan's (Rule 6).

---

## 5. The build, in order

**The order.** Every item in section 3 has a place in it. A gate is Hassan's decision.

```
Wave A   the Actions revamp (section 3.6)                   (first: minutes limit everything)
Wave 0   put the record straight; decisions D1-D8 asked     (read-only + one docs PR)
Wave P   the paper desks: seen, honest, explained (3.7)     (while wave A waits on its nights)
Phase 1  finish #314                                        -> G1 merge
Wave 2   the operations WXPredict depends on (R2-R11, R13-R15, R20, R22, R28, R31)
Phase 2  the market and the inputs, fresh at the decision   -> G2
Phase 3  the model, development months only                 -> G3
Phase 4  the sealed test, once                              -> G4
Phase 5  shadow, the nightly cycle, the board               -> G5
Section 7  full testing                                     -> "complete"
Dated (run on their dates, inside whatever wave is current):
         rd3 / da_floor / sd_corr first looks (~25 Oct)
         the P3.10 ensemble test (~29 Oct)
```

**Minutes discipline across all of it (R1):**
- one push per step, validated locally first;
- docs-only changes ride the next code push;
- every PR states its CI minutes;
- the running total is kept in the status table (section 10).

### Wave A: the Actions revamp (first: minutes limit everything else)

Hassan, 5 Oct: revamp the useless and inaccurate workflows, "as long as we maintain quality, don't cut corners, and improve the accuracy of the engines".

**Rules for every change in this wave:**
- (1) It is **reversible**: a step or workflow is disabled with a comment naming this document, never deleted, and the code and its data stay.
- (2) Its billed minutes are **measured before and after** (jobs API, at least 3 runs each).
- (3) **No test is removed, skipped or weakened**, and CI still runs every test whenever anything a test reads changes.
- (4) **No served price changes**. Each change carries the proof that nothing priced reads what it stops.
- (5) Any schedule change goes through **P6.1**, the only step allowed to change the clock, with the reason written into `SCHEDULED_MINUTE_BUDGET` / `MEASURED_MINUTES`.

**A.1 Inventory and dependency map (read-only)**

- **What:** every workflow, and every step of the four scheduled ones, gets its cost, what it writes, and who reads it.
- **How:**
  - the jobs API over 7 runs;
  - a code search of `scripts/`, `web/` and `n8n/`;
  - database view dependencies (`pg_depend`);
  - `clock_schedule`;
  - the web app's dispatches;
  - n8n's workflows, read through its API.
- **Checkpoint:** each step is classified, with evidence, as one of:
  - **keep** (it feeds a price, a page, the archive or a blind test);
  - **trim** (needed, but slower than it must be);
  - **stop** (its output reaches nothing);
  - **fold** (WXPredict replaces it at G5).

  The table goes into section 3.6.

**A.2 CI, with the same coverage**

- **What:**
  - **(a)** the database contracts run when anything they read changes: `supabase/migrations/`, `sql/`, `tests/database/`, the package files or the workflow itself. They are skipped otherwise (~105 s of a ~5-minute job).
  - **(b)** a push touching only files that no test reads skips pytest.
- **How:** both lists are generated by a script and held by a test.
  - The test fails if a test file starts reading a path on the skip list.
  - It also fails if a migration can change without the contracts running.
- **Verification:** replay the path filter over October's 69 test runs: how many would have run, and that every run touching code or SQL still runs (count written down). Then the next 10 PR runs measured.
- **Checkpoint:** CI minutes per code push unchanged or lower, and 0 code-or-SQL pushes skipped.

**A.3 Stop the fits whose output reaches nothing**

- **What:** in `pipeline_daily`:
  - the hit tournament (185 s);
  - the trajectory fit (177 s);
  - the calibration refit (25 s);
  - model promotion (23 s);
  - strategy learning (22 s);
  - plus skill measurement (54 s) and the engine replay (41 s) **only if A.1 shows nothing reads them**.

  Together that is about 527 s a night, about 270 billed minutes a month (estimated from one run; measured after).
- **Proof for each, before stopping:**
  - the setting that keeps its output out of the price (`applies: false`, `strategy_learning` off, 0 promoted, 0 of 1,152 applied, shadow only), read live;
  - the code path that reads it (named);
  - the pages that read it (view dependencies).

  Its last output stays in the database and the archive.
- **Verification:** the next 3 intraday runs' priced ladders carry no version from a stopped fit (`priced_from`), and their prices are what the code would have priced with the fits running. Both paths read only the flags shown above.
- **Checkpoint:** 3 nights of `pipeline_daily` green, shorter by the measured amount, and every page that read one of these outputs still renders (read as `anon`).

**A.4 Trim the forecast ingest (627 s)**

- **What:** measure where its 10.5 minutes go (each pass's time, rows written, Open-Meteo refusals), then trim the waiting, not the coverage.
- **Verification:** the same rows written (count per city and model) in less time, over 3 nights.
- **Checkpoint:** row counts equal or higher, and minutes lower, both measured.

**A.5 The intraday cadence (option B) and the paper desks**

- **What:** every 12 h instead of every 6 h, through P6.1. Stopping the paper desks from trading on the engine's prices is Hassan's call (D8).
- **Verification:**
  - the day-ahead hit record's call-to-midnight distance, before and after (the arithmetic of migration `20261004170000`);
  - the board's freshness limits (decisions 8 h, edges 12 h) still met, or each limit changed with Hassan's word;
  - 3 days of runs.
- **Checkpoint:** minutes measured, the day-ahead record whole, and the board inside its limits.

**A.6 Workflows nothing dispatches**

- **What:** disable (not delete) each manual-only workflow that A.1 shows nothing dispatches: not the clock, not the web app, not n8n. `paper_fill.yml` is in use (the web app).
- **Checkpoint:** the list, with its evidence, in section 3.6.

**A.7 Measure the result**

- **What:** 7 days of billed minutes after A.2-A.6; the October and November projection, CI included.
- **Checkpoint:** the projection under 3,000, written into `MEASURED_MINUTES`. If it is not under, the gap goes to Hassan with the options.

**Evidence:**
- one PR per change (A.2, A.3, A.4, A.5, A.6), each with its before and after;
- section 3.6 updated;
- the status table.

### Wave 0: put the record straight (read-only, then one docs PR)

**0.1 Verify and close the stale steps**

- **What:** R16 (P0.3), R18 (P1.2), R19 (P1.3/P1.5), R21 (P2.2), R29 (P4.5), R40.
- **How:** each step's own acceptance, as written in its `PLAN_PROGRESS` row, checked against the live system. One query or log read per item, quoted.
- **Verification:** the measured answer, written beside the step.
- **Checkpoint:** every one of them is either closed with evidence, or moved to wave 2 with what remains.

**0.2 Re-measure what the register marks "not re-measured"**

- **What:** R20 (mirror coverage), R31 (n8n executions), R22 (models per day), the biggest tables for R2.
- **Checkpoint:** each number is in the register.

**0.3 Correct `PLAN_PROGRESS`**

- **What:** every "PR open" or "pending merge" line that GitHub contradicts is corrected to what is measured. This is the first part of R32. Nothing else changes.
- **Checkpoint:** the doc diff contains only status corrections, each with its evidence.

**0.4 Put the open decisions (section 9) to Hassan**

- Wave A goes first (Hassan's D1). A wave that adds scheduled minutes waits until A.7's measurement shows room for it, or until Hassan allows overage.

**Evidence:** one docs PR. It carries no code, so it can ride phase 1.1's push and avoid a CI run of its own.

### Wave P: the paper desks, seen, honest and explained (section 3.7)

**When:** after wave 0, while wave A's steps wait on their nights. It touches no workflow's schedule, so it cannot disturb wave A's measurements. Its pushes count against the build's CI budget (R1).

**What it does not do:**
- loosen any strategy's rule to make trades appear (R45);
- touch the Portfolio desk (Rule 6);
- delete a row.

**P.1 Re-measure at the start (read-only)**

- **What:** re-run section 3.7's measurements:
  - desks, trades, orders and plans per desk;
  - decisions per strategy and action since 30 Sep;
  - reason codes per strategy over 48 h and 7 days;
  - the paper jobs in `ingest_log`;
  - the API's answer.
- **Checkpoint:** the numbers are in section 3.7, dated. Anything that has changed since 5 Oct is explained before P.2 starts.

**P.2 Retire the eight flat ledgers (R17, after D5)**

- **What:** retire the desks of s1, s3, s4, s5, s6, s7, s8 and s9. Nothing else.
- **How:**
  1. `tools/retire_desks.py` gains `--strategies s1_buy_low_sell_signal,...`. With it, `retire` touches only the desks whose `strategy_id` is in the list **and** whose strategy is `enabled` false. A listed strategy that is switched on is refused, by name.
  2. The export stage is unchanged: every desk's rows go to `data/archive/paper_desks/<date>/`, are verified and are pushed. `retire` refuses until they are on `origin/main`.
  3. The step that switches strategies off (`set_strategies_enabled`) is skipped in this mode: these eight have been off since 29 Sep.
  4. `retire_desks.yml` takes the list as an input, beside `confirm: RETIRE`.
  5. Run once, by hand. About 1-2 billed minutes; the measured number goes into `MEASURED_MINUTES`.
- **Tests** (`tests/test_retire_desks.py`, before the run):
  - only the listed desks are retired;
  - a listed strategy that is switched on is refused, and nothing is retired;
  - with no list, today's behaviour is unchanged (the existing tests still pass);
  - the strategy switch is not called in this mode.
- **Verification (live), in one before/after query:**
  - each of the 8 desks has `status` `retired`, `archived_at` set and its reason on record;
  - the other 8 desks are untouched;
  - `paper_trades`, `paper_orders`, `paper_trade_plans`, `paper_activity` and `paper_positions` have the same row count per desk before and after: nothing was deleted;
  - `anon` still reads all 198 trades;
  - the API lists 8 desks: Portfolio, s2, s10 ×3, s11 ×2 and s12;
  - the 8 retired desks appear in the page's archived list as "Kept as history".
- **Checkpoint:** all of the above, quoted in the PR.

**P.3 The page opens on a desk with something to show, and shows every desk's trades (R41)**

- **What:**
  1. The route's `accounts` answer gains, per desk:
     - its trade count;
     - its last trade time;
     - its open positions;
     - its strategy's state (switched on, or retired, and when).
  2. These are computed in the route from `paper_trades`, `paper_positions` and `strategies` with the service key. They are **not** computed from `v_paper_desk_activity`, the view whose use emptied the desk list once and was reverted blind (`route.ts`). P.3 finds out why that happened before choosing (the view read as `anon` and as the service role, and the route's answer with it), and writes the reason down.
  3. The page opens on the desk with the most recent trade. Ties are broken by trade count, then by name, so the choice is fixed.
  4. An **"All desks"** choice shows `PaperTradeHistory` with no account filter (it supports this already) and a one-line summary per desk: desk, strategy state, trades, net P&L, last trade. Archived and retired desks are included and marked.
- **Tests:**
  - `test:routes` cases for the new fields, in both sign-in modes;
  - a route test that a failing count query still returns the desk list, with no counts (the list is never emptied for a nicety again);
  - `tsc` and `next build`.
- **Render test:** this sandbox cannot reach the site or the database (section 2.4), so the page is rendered on a local `next start`.
  - Playwright (`/opt/pw-browsers/chromium`) serves every `/api/paper-desk` and Supabase request from responses recorded that day through the Vercel and Supabase tools.
  - It asserts that the page opens on a desk with trades.
  - It asserts that "All desks" shows 198 trades, and a net P&L equal to the SQL sum.
  - It takes a screenshot, which is attached to the PR.
- **Verification (live):**
  - after the deploy, the API's answer is read through the Vercel tool, and the counts equal SQL for every desk;
  - Hassan looks at the page. His word is the acceptance.
- **Checkpoint:** all of the above.

**P.4 The status line tells the truth (R42)**

- **What:** `deskState` learns two more cases.
  - **Retired, or switched off.** The desk's strategy is retired or `enabled` false: "Strategy retired on <date>: this desk never trades again" (or "switched off"). It is never "Running".
  - **Running, with nothing to buy.** The strategy is switched on but has no trade in 24 h: "Running. <N> decisions in the last 24 h, 0 buys. Most often: <reason in plain words> (<count>)".
- **How:**
  - The counts come through the paper-desk route (service key), from `decisions`.
  - Each reason code's plain words are taken from the code that emits it (`decision_engine.py`, the strategy modules, s2's rule), quoted in the PR. They are not written from memory. `own_rule_none` and `no_ladder` are read in their source first.
- **Tests:**
  - a unit test of `deskState` for every case, the old ones included;
  - the route test for the counts;
  - the render test of P.3 extended to a retired desk and to a running desk with no buys.
- **Verification:** the page's counts equal a SQL count over the same 24 h, on one desk, written down.
- **Checkpoint:** all of the above, with the screenshot.

**P.5 Is each NONE right? (R43, R44)**

- **What:** an independent check, not a replay.
  - For the last 7 days of s11_ladder, s11_lock and s12_no decisions, read the inputs that `engine_replay_live.py` reads:
    - the ladder and the book from the `prediction_checkpoints` row that each decision names (`checkpoint_id`);
    - the ledgers from the tick's `engine.inputs` detail.
  - Compute the posterior with the same `ladder_posterior`.
  - Count the bands where the posterior minus the ask minus the worst fee (`engine_orders.worst_fee_per_share`) is above 0, and where it clears the no-trade band `h`.
  - **If none clears it,** NONE is right, and the numbers say so.
  - **If some do,** trace each one to the rule that stopped it: the against-market gate, `h`, a rail, timing or the minimum order. Report them.
  - **Any change to a rule is Hassan's decision.**
- **And:**
  - trace s10's `no_ladder`: which checkpoints, which cities, and whether a ladder was listed and priced at that moment (`markets`, `bands`, the book). A real gap becomes a fix step with its own test. An expected case (e.g. d1_eve before the venue lists the day) becomes a plain-words reason in P.4;
  - the same for s2's `no_signal`: the smallest ladder ask sum in the period against $1 plus fees.
- **Verification:** the queries and their answers in the PR. Every number is re-runnable.
- **Checkpoint:** each reason code is either confirmed by its inputs, or has a fix step, or is a named question for Hassan.

**Evidence for wave P:**
- one PR for P.2's code (the run follows its merge);
- one PR for P.3 with P.4 (web, route and tests);
- P.5's report in the second PR, or in its own docs PR if it finishes first;
- section 3.7 and the status table updated in each.

**Gate:** D5 before P.2 runs. Hassan's look at the page for P.3 and P.4. Nothing about capital.

### Phase 1: finish (PR #314)

**1.1 The two open review findings**

- **What:**
  - **(a) One rule for a "whole day."** A station day is whole when its reports leave no gap over 3 h, counting from local midnight to the first report and from the last report to the next midnight.
    - Measured on the station days of 1 Jun 2025 - 4 Oct 2026: 89 fail. 81 have a gap over 3 h, and 8 have no report at all. (Re-measured 6 Oct: 24,550 station days, 24,542 with a row; the 24,518 first written here was not reproduced.)
    - The rule is used everywhere a day's maximum is read: the label, the unlisted station days, yesterday's maximum, climatology and the forecast's past error.
    - Today the code checks only the station's newest report, so an old outage day passes.
  - **(b) `decision_local` stamped at the decision instant (hh:01).** Today it shows hh:00.
- **How:**
  - `common.max_gap_h(times, d0, d1)` with `WHOLE_DAY_MAX_GAP_H = 3`.
  - `station_daily.csv.gz` gains `max_gap_h`, regenerated from the cached responses. The old file is compared row by row: only the new column may differ.
  - The builder reads it, and `decision_local` comes from `t`.
- **Verification:**
  - tests for a day with a 14 h outage (refused), a day with one missed report (kept), a fall-back day, and today's partial day (refused);
  - `decision_local` parses back to `decision_utc`, row by row, on the whole table;
  - the rebuilt table's row counts are written down: venue labels kept, station days dropped;
  - two builds byte-identical; both suites green.
- **Checkpoint:** every review thread answered and resolved, and CI green on the new head.
- **Evidence:** the commit message, the PR thread replies, and `table_meta.json`.

**G1 (Hassan): merge #314.**

### Wave 2: the operations WXPredict depends on

Each item is one small PR, or a batch of related ones, with its own measured before and after. **In order:**

**2.A Storage (R2)**

- **What:** get under the 500 MB tier with headroom, by the archive cycle only.
- **How:** P1.6 phase 3's remaining steps: name the largest tables (sizes measured), then export, verify, commit and prune. Every pruned row stays readable from the archive.
- **Verification:**
  - `pg_database_size` before and after;
  - the archive's exported = expected = deleted counts per dataset;
  - a sample of pruned rows read back from the repository.
- **Checkpoint:** under 90% of the tier, and the nightly archive keeps it there for 3 nights.

**2.B The nightly forecast record and its siblings (R3, R4, R5, R22)**

- **What:** every city, every night.
- **How:** the paced retry pattern from #311 (requests one at a time, a measured pause, a bounded second pass inside the step's deadline) for `honest_record` and the ensemble record.
- **Verification:** unit tests like #311's; then 3 nights with 48 of 48 cities, read from `ingest_log` and the committed files.
- **Checkpoint:** 3 consecutive whole nights, and the step's minutes measured into `MEASURED_MINUTES`.

**2.C The tick's confirmations (R6)**

- **What:** no killed run, no growing queue.
- **Verification:** 48 h with no killed run and `pending_after` not growing.

**2.D Discovery, trade prints and the watchdog (R7, R8, R9, R14)**

- **What:**
  - Zhengzhou's slugs explained;
  - a back-off on the 429s;
  - the P6.3 watchdog (the age of each job's last ok against an SLA, on the board, no email).
- **Verification:** each job's next 24 h. The watchdog is tested by stopping a job's ok in a fixture (`tests/database` contract).

**2.E Timeouts (R11, R15)**

- **What:** name the 49 queries, then fix them.
- **Verification:** each changed view proven equal (REPEATABLE READ, `EXCEPT ALL` both ways) and read as `anon`; 0 timeouts in 24 h.

**2.F The remaining record items (R20, R28, R31, R13)**

- **What:**
  - mirror coverage;
  - the hit-history repoint (with its equivalence check);
  - n8n executions;
  - the US re-measure at 7 graded days after the fix.
- The flat ledgers (R17) moved to wave P.2.

**Gate:** wave 2 needs no Hassan gate beyond D1, for minutes. Phase 2 starts when 2.A and 2.B have passed their checkpoints. 2.C-2.F may overlap phase 2.

### Wave F: the seasonal focus (Hassan, 6 Oct)

Hassan, 6 Oct: a "Seasonal Focus 10" of the cities most predictable in their current local season, chosen by research and recorded before its outcomes. It is applied across Predictive, with a daily status per city and evidence on each card, and judged against the full universe. "Prioritise these cities without losing comparison data ... Preserve the existing separation between serving models, evaluation models and blinded challengers." **Seasonal membership never moves a probability.**

**F.1 Choose and record the set (done 6 Oct)**
- **What:** `tools/focus/season_predictability.py` ranks the 48 active cities by the bias-corrected day-ahead forecast's bucket hit rate over 6 Oct - 30 Nov of past years. It uses data before 2026-09-01 only and reads no blinded challenger.
- **The record:** `docs/FOCUS_PREREG.md`, the study's JSON (sha256 `b0250963...`), and the append-only database row `focus_sets` `seasonal-2026-10-06` (migration `20261006150000`). The set is evaluated from 8 Oct.
- **Checkpoint:** the three agree (`tests/test_focus_set.py`); the row cannot be changed (`tests/database/focus-set.cjs`); no script reads the set.

**F.2 A daily status per city**
- **What:** each active city, each day and checkpoint, gets one of four statuses and the reason in words:
  - **Candidate:** fresh, complete data; suitable weather; enough model evidence at that checkpoint;
  - **Watch:** the forecasts disagree, cloud clearance is uncertain, or the winds are changing;
  - **Insufficient evidence:** too little settled history at this checkpoint;
  - **Unavailable:** a market, the station's data or a usable forecast is missing.
- **How:** one view over what the platform already records (freshness, the forecasts' spread, settled counts per checkpoint). Each threshold is stated, versioned, and has a prior and bounds (Rule 11). Cloud and wind inputs are used only where they are recorded; what is not recorded is said, not guessed.
- **Checkpoint:** the status of every active city today, each reason readable; a contract test per status.

**F.3 One city filter for all of Predictive**
- **What:** "All active cities · Seasonal Focus 10 · Custom selection" controls the city cards, the forward predictions, the historical accuracy, "Who called what" and "Was it right?". Every total is recomputed from the visible rows.
- **How:** per-city rows where today only an all-city summary exists (`v_prediction_hindsight_summary` has no city). The page holds one selection and passes it to each section.
- **Checkpoint:** totals for "All" equal today's on the same rows; a filtered total equals the sum of its cities (unit tests).

**F.4 Evidence on each city card**
- **What:**
  - the predicted bucket and probability;
  - the hit rate at the selected checkpoint, with its settled city-days;
  - the temperature error and the calibration gap;
  - the market against the model on matching days;
  - the executable price, the existing net edge, and data freshness.
- Day ahead, noon and pre-peak are kept apart; post-peak is labelled separately.

**F.5 "Was it right?": the focus against the universe**
- **What:** both groups over the same dates, checkpoint and predictor version, from 8 Oct, as `docs/FOCUS_PREREG.md` fixes. The formal reading is on 1 Dec.

**F.2-F.5 built 6 Oct (this PR): one selection, a status, the record on each card, the focus against all**
- **The status rule was measured, not chosen.** `tools/focus/status_conditions.py` tested Hassan's three Watch conditions on 19,227 city-days before the sealed test (14 Jul 2025 - 30 Aug 2026), by a rule fixed in its docstring: a condition counts when the day-ahead hit rate on its top-fifth days is lower, with the 98.75% interval (Bonferroni over 4) of the difference below zero, date-clustered bootstrap. Output `data/eval/focus/status_conditions_2026-10-06.json` (sha256 `ee4d3f10...`); two runs give the same bytes.
  - The seven models' lead-1 maxima span 4.8 C or more: 25.2% against 33.4%, difference -8.2 pp [-10.0, -5.9]. **Adopted.**
  - Cloud near half cover (uncertain clearance): 33.4% against 31.3%. Not adopted (the wrong direction).
  - Pressure change since the day before (a front): 31.3% against 31.9%. Not adopted.
  - Wind speed change since the day before: 30.3% against 32.1%, interval [-3.9, +0.5]. Not adopted.
  - The November-transition flag of F.1's study held back no city, so no seasonal reason is shown.
- **`v_city_status`** (migration `20261006180000`): each active city's local today and tomorrow, at the seven moments; the worst state wins, every reason is listed. Thresholds are the append-only `city_status_rules` row `status-v1`, named on every status row: models span 4.8 C (at least 4 models), 10 settled days at the moment, station report within 3 h (over 7 days 1 of 12,157 IEM report gaps exceeded 3 h), a forecast run within 24 h, an open market. Read live on 6 Oct for 7 Oct, day-ahead: 35 candidate, 12 watch (forecasts disagree), 1 unavailable (no market). Nothing that prices reads it.
- **One selection for the page:** All active cities · Seasonal Focus 10 · Custom selection, and the moment. It drives the city cards, the forward table, "Was it right?", "Who called what" and the accuracy panels. Totals are added up from the visible rows (`web/lib/focus.ts`). The panel still reads the database's own total and says so when the rows do not add up to it. Checked 6 Oct: the summary's filters, applied to the rows, equal `v_prediction_hindsight_summary` at all seven moments.
- **Each card:** its status and reason; its record at the moment over 30 days (hit rate with N and a Wilson interval, calibration gap, temperature error, market against model on the same days); day ahead, noon and pre-peak side by side, after the peak labelled apart; the station report's age and the forecast run beside the pricing times.
- **"Was it right?"** shows the Seasonal Focus 10 against the 48 cities active when it was recorded and against the other 38, over 8 Oct - 30 Nov only, on the rows `docs/FOCUS_PREREG.md` fixes (same date, moment and engine version in both groups), with the mean probability on the winner and the market's top-1 on the same rows. The 48 are frozen in the append-only `focus_set_universes`, copied from the study JSON whose sha256 `focus_sets` records, so a later retirement or activation cannot move a city's record in or out (Codex on #327). `v_prediction_hindsight` gains `prob_on_winner`, appended; its other columns proven unchanged (one snapshot, `EXCEPT ALL` both ways: 4,122 rows, 0 and 0).

**F.6 Priorities**
- Analysis and model improvement go to the ten. The other cities keep basic collection where storage allows (2.A).
- Serving, evaluation and blinded challengers stay separate.

**F.7 Adaptive paper strategies (Hassan, 6 Oct)**
- **What:** each strategy trades only cities whose status is Candidate, when its own conditions meet. It decides before and during each city's own peak, and on the market's move when the likely winner climbs fast.
- **The Candidate gate (built 7 Oct, shadow).** The tick reads `v_city_status` once, on a daemon thread started right after it writes the hour's station reports (2.7 s for all 672 rows, 7 Oct; a tick job already runs 43-59 s against whole billed minutes), and the engine waits at most 2 s for it. After the engine has decided, a BUY where the city-day's status at that checkpoint is not `candidate` is held back: NONE (HOLD when something is held), reason `not_candidate`, no order, and what it would have bought kept in `params_version.status.would`, so the gate can itself be judged on settled outcomes. Every decided row names the status and the rules version. Unread, every BUY is held back. An S10 SWITCH's buy half is gated too (held back, the switch holds); S10's own SELL never is. The statuses go into the tick's recorded inputs and the nightly replay applies them; a run recorded before the gate replays without it. Today the gate rarely bites: every engine view is anchored on the market at w = 0 while `settings.strategy_learning` is off, so no engine strategy bought in the 7 days to 7 Oct (every decision other than NONE was S10's own WAIT).
- **Bounds:** a strategy's city list is learned from its own settled results within Rule 11's bounds. Paper only; it never loosens a rule.
- **The momentum trigger, measured first (7 Oct): not adopted** (`docs/F7_MOMENTUM_2026-10-07.md`). `tools/f7/momentum_study.py`, its rules committed before its first run (`2cd1f58`), tested twelve forms on 7,957 pre-cutoff events (30 Dec 2025 - 31 Aug 2026): the leading bucket climbing 0.10 or 0.20 in an hour, before (3 h to 0 h) or during (0 h to +1 h) the city's peak, alone, with the forecasts agreeing, or on the forecast's own bucket; one share at the next price plus the books' half-spread and the taker fee. All twelve lose on average (-0.60 to -3.66 cents a share); none has its 99.58% interval above zero; before the peak M 0.10 and A 0.10 lose with the interval wholly below zero. The market prices its climbing favourite about right (during the peak, a 0.20 climb wins 68.8% at 68.8 cents). The strategies keep deciding at each city's own peak-relative checkpoints, which the tick already schedules.

### Phase 2: the market and the inputs, fresh at the decision

**2.1 What the record's price is**

- **What:** `prices-history`'s `p` is the training input. Before serving can read "the same thing" live, measure what `p` is.
- **How:**
  - On the hours where both exist (`book_snapshots`, every 2 h at odd UTC hours since 22 Aug), compare each bucket's `p` at hh:00 with the book's mid, best bid, best ask and last trade at that hour.
  - Read-only queries plus the committed record.
- **Verification:** per bucket and hour, the distribution of differences (median, 90th percentile, max) for each candidate definition, and the share within 0.005.
- **Checkpoint:** one live definition is chosen (expected: `prices-history`'s latest point, or the midpoint) whose difference from `p` is measured and written down. If none matches within 0.01 at the 90th percentile, stop and report.

**2.2 The live market reader**

- **What:** at each decision, each bucket's price by the 2.1 definition, with its timestamp.
- **How:**
  - one batched request per tick; the tick already batches `POST /books`, so reuse that call if 2.1 picks the mid;
  - inside `BUDGET_S` 45 s;
  - never raises into the tick.
- **Verification:**
  - unit tests on recorded answers;
  - the tick's duration on 3 real runs before and after, which must stay within one billed minute;
  - the price stored with its age.
- **Checkpoint:** 24 h of ticks with every due city's ladder read, or each miss logged with its reason.

**2.3 Live features equal training features (the parity test)**

- **What:** the serving code computes every feature with the same functions as the table builder, from live sources:
  - IEM reports fetched at the tick, all 50 stations in one request;
  - the hourly `_previous_day1` forecast;
  - the seven models' daily maxima;
  - the station history;
  - the live market.
- **How:**
  - the builder's feature functions move to a module that both the builder and the serving code import, so there is one implementation;
  - the live hourly forecast is fetched nightly inside `pipeline_daily`'s existing forecast step, with no new schedule; its minutes are measured;
  - each report's arrival time is logged, so the 20 min lag is measured every hour.
- **Verification:** for 3 real days, every live feature row recorded at the tick is compared with the row the builder makes for the same instant once the day is archived.
  - numbers: equal within 1e-6;
  - market: within the 2.1 tolerance;
  - buckets with a spread over 0.10 reported on their own, because 2.1 found no definition that meets the tolerance on them;
  - every difference explained.
- **Checkpoint:** 3 consecutive days of parity, or a written list of the differences and why each is acceptable.

**2.4 Market activity going forward (measured, then decided)**

- **What:** volume by bucket-hour as a feature for the future.
- **How:**
  - check whether Polymarket's data API serves past trades for these markets from here (host reachability not measured yet);
  - `trades_observed` already holds trade prints from 3 Oct 22:36Z.
- **Verification:** coverage per bucket-day.
- **Checkpoint:** either a past volume series exists (it is then added to the table in a later version, with its own leak test), or volume waits until enough live history accumulates. **No volume feature enters the model before it has history to train on.**

**2.5 Fresh forecast history (the morning gap)**

- **What:** store the same-day forecast's hourly curve at each tick, so a later version can learn from fresh runs honestly.
- **How:** one table, written by the tick or the intraday pipeline, with the storage per day measured. **Option for Hassan:** allow `single-runs-api.open-meteo.com`, which would give the history at once.
- **Checkpoint:** Hassan's choice is recorded. Nothing is fetched from a denied host.

**G2 (Hassan):** the parity results (2.3) and the 2.1 definition; approval to go on.

### Phase 3: the model (development months only)

**3.0 Pre-register**

- **What:** `docs/WXPREDICT_PREREG.md`, holding section 4 verbatim plus:
  - the model families;
  - the feature groups;
  - the ablations;
  - the hyper-parameter grid;
  - the dev/sealed split.
- **Checkpoint:** committed before any development score is computed. Its commit hash is quoted in every report.

**3.1 The code, properly**

- **What:**
  - `tools/wxpredict/dataset.py`, `model.py`, `walk_forward.py`, rewritten from the exploratory versions;
  - numpy and scikit-learn added to `requirements.txt` (CI), with pinned versions.
- **Tests** (`tests/test_wxpredict_model.py`):
  - the bucket mapping on C and F ladders, open tails and impossible offsets;
  - the fusion recovers weights planted in synthetic data;
  - each per-ladder softmax sums to 1;
  - the walk-forward never trains on a row whose day ends after the month's cutoff;
  - the sealed months are refused unless a flag is passed, and that flag is logged;
  - a fixed seed gives identical output.
- **Verification:** both suites, and CI's install time measured. A CI run's billed minutes before and after are written into the PR.

**3.2 A, the weather model, and its ablations**

- **What:** gradient-boosted classes of the day's maximum in the market's unit, as offsets from an anchor (the running maximum, or the forecast), with city as a category. Trained on every whole station day, listed or not.
- **Ablations**, each walk-forward on the development months and scored by section 4's metric against the market:
  - **A** station now only;
  - **B** + city;
  - **C** + day-ahead forecasts;
  - **D** + hourly forecast;
  - **E** + calendar and activity;
  - **F** + station history (climatology, the forecast's past error);
  - **G** everything.
- **Verification:** each group's added gain with its interval. A group that adds nothing (interval spanning 0) is reported and kept out of v1. Each choice cites its row.
- **Checkpoint:** the v1 feature set and form are chosen on development months only, and written into the pre-registration's appendix (dated, before 3.6).

**3.3 B, the buckets**

- **What:** offsets summed into the venue's ladder. An offset below the running maximum keeps a floor (1e-4). F buckets use whole-°F venue rounding.
- **Verification:**
  - on every development row, the station's reading maps into the winning bucket as often as `label_unit` says;
  - F ladders end to end (the 2 °F buckets);
  - property tests: sums to 1, the impossible mass stays bounded.

**3.4 C, the fusion with the market**

- **What:** two forms, chosen on development months:
  - (i) a log-linear pool per hour group: `a·log q + b·log p + c·[running-max bucket]`, bounded;
  - (ii) a bucket-level gradient-boosted score with the market, the weather model and the context, softmaxed per ladder.
- **Verification:** each form's gain over the market and over the recalibrated market, per hour group, with intervals.
- **Checkpoint:** the simpler form wins unless the other is better with its 90% interval above 0 over the simpler one.

**3.5 D, calibration**

- **What:** reliability per hour group, per city type and per unit; a power transform fitted on earlier months if it helps.
- **Verification:** reliability tables before and after on development months, and coverage of the 80% set of buckets.

**3.6 The named checks on development months**

- Post-peak, morning centre and US, as in section 4. Each is reported with its numbers.
- A failure is explained before going on, not tuned away on the sealed test.

**3.7 Cost**

- **What:** the time to fit everything nightly, and the time to price one tick.
- **Checkpoint:**
  - the nightly fit fits inside `pipeline_daily` without pushing it past its measured minutes by more than Hassan approves;
  - tick inference adds under 2 s.
  - Preferred for the tick: pure-Python inference from exported trees (no numpy in the tick), proven equal to scikit-learn's output to 1e-9 on 10,000 rows.

**G3 (Hassan):** the development results (3.2-3.7), the frozen v1, and approval to score the sealed test once.

### Phase 4: the sealed test, once

**4.1 Score it**

- **What:** v1 exactly as frozen (its commit hash), walk-forward through 1 Sep - 4 Oct (and later listed days), scored by section 4.
- **Evidence:**
  - `docs/WXPREDICT_RESULT_<date>.md`, generated by the tool from committed inputs only;
  - the scored rows in `data/eval/wxpredict_<date>.csv.gz`.
- **Verification:** the report reproduces byte for byte from the committed inputs (a test).

**4.2 Against the engine as served**

- On the graded checkpoints in the sealed period (`v_checkpoint_outcome`, the engine's own calls), at the same checkpoints.
- Not against rd3, `da_floor` or `sd_corr` until they are unblinded.

**4.3 The verdict**

- Pass or fail by section 4's rule, as written.
- No re-run with changes. A failed v1 is reported as failed, and any v2 needs a new pre-registration and a new sealed period (the days after 4 Oct).

**G4 (Hassan):** the result; whether WXPredict goes into shadow.

### Phase 5: shadow, the nightly cycle, the board

**5.1 Serving code in the tick, shadow only**

- **What:** WXPredict prices every due city at each tick checkpoint and records its ladder. Nothing reads it for pricing or trading.
- **How:**
  - a new shadow table with the ladder, the inputs' ages, the version and the market at the decision;
  - or `variant_shadow_checkpoints`, if its day-ahead constraints fit (decided in this step from its DDL);
  - a migration with a contract test in `tests/database`;
  - RLS on, service-role writes;
  - read by the page through a view checked as `anon`.
- **Verification:**
  - the tick stays inside 45 s and one billed minute, on 3 real runs;
  - every due city gets a row or a logged reason;
  - each row's ladder sums to 1.

**5.2 The registry**

- `model_registry` rows: `wxpredict v1` candidate, then shadow.
- The version is on every shadow row.
- `v_prediction_contract` includes WXPredict's calls (an equivalence check on the view's other rows, both ways).

**5.3 The nightly cycle (Rule 11)**

- **What:** inside `pipeline_daily`'s existing run:
  1. append yesterday's data;
  2. refit;
  3. check the refit against the current version on the last 14 days (out of sample for both);
  4. promote within shadow only if it is not worse, with a maximum change bound on the fusion weights;
  5. record the version, the evidence and a rollback in `model_registry`.
- **Verification:**
  - tests for the bound;
  - for refusing a worse refit;
  - for the version on every row;
  - for a refit never scored on its own training days;
  - the step's minutes measured and written into `MEASURED_MINUTES`.

**5.4 The scoreboard**

- **What:** a view comparing WXPredict, the market and the served engine per day, hour group and city, on settled days, plus a panel on `/predictive` beside the lineup (`PredictionLineup.tsx`).
- **Verification:**
  - the view read as `anon`;
  - `tsc`, `test:routes` and `next build`;
  - the panel's numbers equal the view's on one day, checked by hand and written down.

**5.5 Shadow acceptance**

- Pre-registered before shadow starts: section 4's rule on the first 20 graded shadow days.
- Reported as it stands each week. No change to v1 during the period.

**5.6 WXPredict's own shadow desk (R43, R45)**

- **Only if G4 showed an edge over the market.** A desk trading on prices that are measured no better than the market teaches nothing; that is the case today (section 1).
- **What:**
  - one new strategy in `shadow` (Rule 6: shadow is free; the Portfolio desk is untouched);
  - it decides with the same `decision_engine.decide` (Kelly book, no-trade band, rails, against-market gate), on WXPredict's shadow ladder from 5.1 instead of the engine's;
  - its BUYs go through the same path as the engine strategies' (`engine_orders`, `publish_engine_plan`, the fill simulator) onto its own shadow desk;
  - every decision row names WXPredict's version (`prediction_source`, `params_version`).
- **How:**
  - the strategy row and its desk through the existing lifecycle functions, in a migration with a `tests/database` contract;
  - the strategy id is chosen in this step after reading P8.1's numbering, and written down.
- **Tests:**
  - the decision on a recorded WXPredict ladder equals a hand-computed one;
  - a BUY becomes exactly one plan on this desk and on no other;
  - with no WXPredict row for a city, the decision is NONE with its own reason code, never an exception.
- **Verification:**
  - 3 real ticks: decisions written, the tick inside 45 s and one billed minute;
  - any BUY becomes a plan, then an order, then a fill, then a trade on this desk;
  - the page (P.3, P.4) shows it, with its reason when it does not trade.
- **Judged on settled outcomes, not on what the desk filled.** Hassan's rule: the paper desks are examples, not the truth.

**G5 (Hassan):** whether WXPredict serves any price. Anything about capital stays his alone.

---

## 6. Testing, at every step

| layer | tests | when |
|---|---|---|
| data (phase 1) | `tests/test_wxpredict_table.py`: leak plants (report, forecast hour, price, past day), DST instants, whole-day rule, labels, determinism, the seed, the station-day hand-over, the offset guard | every push |
| model (phase 3) | `tests/test_wxpredict_model.py`: bucket mapping, fusion recovery, softmax, walk-forward cutoffs, sealed-month refusal, seed determinism | every push |
| serving (phases 2, 5) | parity against the builder; pure-Python against scikit-learn; tick budget; recorded API answers | every push, plus 3 real days |
| database | a contract per migration in `tests/database` (RLS, grants, re-runnable, `anon` reads views only) | every push |
| web | `tsc`, `test:routes`, `next build`, plus a by-hand number check | every web PR |
| paper page (wave P) | `deskState` unit tests; route tests for the counts, in both sign-in modes; a Playwright render on a local build with recorded responses, with a screenshot | P.3, P.4 |
| paper desks (P.2, 5.6) | `tests/test_retire_desks.py` for the targeted mode; a `tests/database` contract for the WXPredict desk's migration | P.2, 5.6 |
| live | each change checked on its next real run: tick logs, row counts, view as `anon` | after each merge |

---

## 7. Full testing: what "complete" means

After phase 5 and before anything is called complete, every item below passes. **A failure goes back to its wave, never into a note.**

### 7.1 Reproducibility, end to end

- From a clean checkout of `main`, rebuild the table from the committed sources. The sha256 must equal `table_meta.json`'s.
- Refit v1 from the committed table. The exported model's hash must equal the committed artifact's.
- Re-score the sealed test. The report must equal the committed one byte for byte.

### 7.2 Both suites, and the web, on `main`

- `pytest`: green, with the count written down.
- The database contracts: green.
- Web: `tsc`, `test:routes`, `next build`.
- The CI run on the final PR: green.

### 7.3 Leak audit by hand

100 rows drawn at random (seed written down) from the sealed period. For each, every input is traced to its raw source:
- the report's valid time, at least 20 min before the decision;
- the forecast hour's run assumption;
- the price's stamp, at or before the decision;
- the past days, whole before the decision.

The table of all 100 is committed. **Any violation fails the build.**

### 7.4 A 7-day live soak, in shadow

Over 7 consecutive days:
- every due city gets a WXPredict row at every checkpoint, or a logged reason;
- the ladders sum to 1;
- the tick stays inside 45 s and one billed minute (from the jobs API);
- the nightly refit runs inside `pipeline_daily`, and its minutes match `MEASURED_MINUTES`;
- no new warning appears in `ingest_log`, and the watchdog (R14) shows green or names the cause;
- the scoreboard's numbers equal the view's on 2 days, checked by hand.
- the paper page's per-desk counts and reasons equal SQL on 2 days, and WXPredict's desk (5.6, if G4 allowed it) shows every decision's outcome or its reason.

### 7.5 Failure drills (each a test, plus one live dry run)

- IEM down, CLOB down, Open-Meteo 429: the tick writes a logged skip, never an exception, and the served engine is untouched.
- The model file missing or corrupt: the tick logs it and writes nothing.
- A refit that is worse: refused (phase 5.3), with the registry showing why.
- Rollback: the registry's rollback restores the previous version, and the next tick uses it.

### 7.6 The database

- Every view changed in the build: equivalence proven at the time, and re-read as `anon` once more.
- The storage stays under the cap through the soak (R2).
- No row deleted outside the archive: the archive's counts for the soak week are written down.

### 7.7 Minutes and the record

- October's and November's billed minutes measured against the budget Hassan set (D1).
- `PLAN_PROGRESS` and this document's status table match the live system line by line. This finishes R32.
- Every register row (section 3) is closed with evidence, or is a named Hassan decision.

**Then:** the result is reported to Hassan as **"complete"**, with the 7.1-7.7 evidence.

---

## 8. Risks, and what is done about each

| risk | handling |
|---|---|
| WXPredict does not beat the market by a meaningful margin | It is measured, not assumed (phase 4). The report says so plainly. The next lever, fresh same-day forecast history (2.5), is Hassan's choice. |
| Training/serving skew | The parity test (2.3) with one shared feature module. |
| A leak in features | Leak-plant tests per input; Codex review; the sealed test. |
| Over-fitting the development months | The sealed test is scored once (phase 4); ablations are kept small and pre-registered. |
| Actions minutes (R1: on course for about 3,300 in October before CI) | D1 first. Then pure-Python tick inference, the fit inside the existing nightly job, one validated push per step, and every change's minutes measured before merge, with the running total in section 10. |
| Storage over the cap (R2: 575 MB, 114.9%) | Wave 2.A, through the archive only, before the shadow table adds rows. |
| Stale records mislead the next session | Wave 0 corrects them; section 7.7 checks the record against the live system line by line. |
| scikit-learn version drift | Pinned versions; the exported trees are the served artifact, so the tick does not depend on it. |
| A blind test's integrity | WXPredict is never compared with rd3 / `da_floor` / `sd_corr` before their first look. |
| Context loss across sessions | This document plus `PLAN_PROGRESS`, updated in every PR, and the status table below. |
| The paper desks stay empty until 5.6 | The page says why, with the counts (P.4), so an empty desk is explained and not mistaken for a broken one. No rule is loosened to fill it (R45). |
| A page change verified only on a local build (this sandbox cannot reach production) | The render test uses responses recorded from production that day. The API is read through the Vercel tool after the deploy, and Hassan's own look is the acceptance (P.3). |

---

## 9. Decisions only Hassan can make

| id | decision | why it is his | blocks |
|---|---|---|---|
| D0 | Approve this document (or say what to change) | the plan | everything |
| D1 | October's minutes (R1). **Hassan, 5 Oct: revamp the Actions first (wave A), keeping quality and improving the engines' accuracy.** Still open: whether to allow paid overage as a margin (suggested: up to 400 minutes in October); GitHub showed 1,065 of 3,000 used on 5 Oct | Rule 7, and spending | the overage part only |
| D8 | Whether the engine strategies' desks (s2, s10 ×3, s11 ×2, s12) keep deciding on the engine's prices until G5 (A.5). Measured 5 Oct: 0 BUY in 11,591 decisions since 30 Sep (section 3.7), so this is about the tick's work and the record, not about trades they are making | the desks | A.5 |
| D2 | G1: merge #314 after phase 1.1 | merging | wave 2 onward |
| D3 | Allow `single-runs-api.open-meteo.com` in the environment's network access (the history of same-day runs) | network access | phase 2.5's faster path; optional |
| D4 | Any interim fix to the served engine before WXPredict (e.g. use the evening-before corrected centre on the same day). Default: none; WXPredict replaces it | prices served | none |
| D5 | Retire the eight flat ledgers now (R17); 29 Sep's "Retire all of s1, s3-s9" appears to cover it. Retiring deletes nothing: the trades stay readable, and the desks move to the page's archived list | the desks | P.2 |
| D6 | After rd3's first look (~25 Oct): S10's future (R33) | strategy | none |
| D7 | When to schedule the trading machinery (R35) and `settlement_verified` (R36) | capital and payouts | none |
| D9 | Storage (2.A, section 3.8). **Hassan, 6 Oct: no subscription; offload the data to the repository, recurrently.** So 2.A is the archive cycle: group A first, then group B with each reader moved to read the repository, then trades offloaded more often than nightly and the never-offloaded tables moved to the repository with their readers. Gap 124.7 MiB, growth about 5 MiB a day | decided | 2.A, so phase 2 from 2.2 |
| D10 | The P.5 report's three questions. **Hassan, 6 Oct:** (1) the market prior's "cheap NO" edges: yes, left to WXPredict, no change; (2) the against-market gate's favourite when the leader has no ask: yes, fix it (the highest bid stands in); (3) S10's model must cover every local hour a city's own peak-relative checkpoint falls on, not only 7-17 (measured 29 Sep - 6 Oct: only Madrid's post-peak, at 18:xx, fell outside). He adds: strategies decide before and during each city's own peak, and on the market's move when the likely winner climbs fast | decided | (2) and (3) are rule changes, each its own PR with tests |
| G3-G5 | as in section 5 | the model's use | their phases |

---

## 10. Status

Updated in every PR.

| step | state | evidence |
|---|---|---|
| D0 (this document) | **approved** (Hassan, 5 Oct: "D0: approved.") | chat, 5 Oct |
| D3 (`single-runs-api.open-meteo.com`) | **allowed** (Hassan, 5 Oct). One request from this sandbox, 5 Oct ~17:00Z: HTTP 200 in 0.81 s, ECMWF IFS 0.25 run 4 Oct 00Z at Heathrow, hourly from the run time | curl, 5 Oct |
| D4 (interim engine patch) | **none** (Hassan, 5 Oct: "no interim patch to the engine") | chat, 5 Oct |
| D8 (the engine strategies' desks) | **keep them running as they are until G5**; they are 5.6's comparison. Their share of the minutes is measured in A.1 (tick: 0 billed; intraday: about 1 of 4 billed a run) and goes to Hassan in A.5 if it matters | chat, 5 Oct; section 3.6 |
| D1 (minutes) | **wave A first; overage allowed up to 400 minutes in October as insurance, the goal still under 3,000** (Hassan, 5 Oct). 941 billed to 5 Oct 16:04Z by the jobs API; GitHub showed 1,065 of 3,000 | chat, 5 Oct; jobs API |
| Wave A (A.1-A.7, the Actions revamp) | **A.1 done 5 Oct** (section 3.6). **A.2 merged** as #315, 5 Oct 20:07:42Z (Hassan: "merge 315"): a docs-only PR skips the suites, the `pytest` job still reports; checkpoint open (the next 10 PR runs measured). The contracts half (A.2a) not built: about 7 s a run (put to Hassan). **A.3 merged** as #316 (5 Oct 21:38Z, `250e8c8`, after Hassan re-ran the CI the runner shortage had cancelled) **and live 6 Oct**: the migration's `DELETE` waited on the Supabase tool's confirmation, which never reached Hassan, so he ran the file in the SQL editor at about 06:58Z. Checked at 06:59:07Z against the 06:19Z snapshot: the calibration status reads `stopped`; `clock_expected_jobs` 41 -> 36 (`pipeline_daily` 17 -> 12); the six spec rows changed and every other row is identical; `settings` and `derived_trajectory` are unchanged (both were already off). `v_data_freshness` rebuilt with its `EXCEPT ALL` proof, 66 -> 68 rows; anon reads 68. Night 1 (6 Oct 04:36Z): 1,667 s, 28 billed, the five steps skipped, every other step green. The checkpoint is nights 2 and 3 plus 3 intraday runs. **A.6 done 6 Oct** (Hassan: "do what's necessary as long as nothing's broken"): `live_weather` and `restore_edge_marks` are `if: false` in their files, because the tick and a fixed root cause do their jobs. `retire_desks` and `verify_resolution_source` stay on, because nothing else does theirs (section 3.6). A.4, A.5 and A.7 todo; A.4 and A.5 wait on A.3's checkpoint, because both change what it measures | section 3.6; #315; #316; `PLAN_PROGRESS` |
| Wave 0 (0.1-0.4) | **0.1-0.3 done 5 Oct**: R16, R18, R19, R21, R29 closed on their own acceptance, live; R40's F4 holds; R2, R20, R22, R31 re-measured (section 3); `PLAN_PROGRESS` corrected: P0.3, P1.2, P1.3, P1.5, P2.2 done, and the five stale "PR open" rows (P2.7, P3.5, P3.7, P4.3, P6.5) were merged #117, #129, #130, #138, #141. 0.4: D0, D1, D3, D4, D5, D8 decided by Hassan 5 Oct; D2 (G1), D6 and D7 remain | section 3; `PLAN_PROGRESS` |
| Wave P (P.1-P.5, the paper desks) | **P.1 done 5 Oct 17:59Z** (nothing changed in substance). **P.2 done 5 Oct 21:19Z.** #317 merged (`eecc50f`); Hassan ran `retire_desks.yml` (attempt 1 never got a runner and changed nothing; attempt 2 ran 43 s, 1 billed). Export `35bb52d` (`data/archive/paper_desks/2026-10-05/`, row counts equal the snapshot); the eight desks retired with the D5 reason, each gaining only its `account_retired` row; the other 12 desks' rows identical by md5; anon reads 198 trades; the API lists 8 desks; the archived list shows 12 retired. Checkpoint quoted on #317. The whole-desk mode must still never be run (it would switch off s2). **P.3 + P.4 merged** as #318 (`a39c84f`, 6 Oct; Hassan: "merge 318"): the page opens on the desk with the newest trade, All desks lists every desk and every trade (Postgres merged with the trade archive), and a running strategy that buys nothing says so and why. Live 6 Oct 09:00:29Z: the production API equals SQL on all 20 desks (trades, closed, net, last trade, open positions) and on all seven running strategies' 24 h decision counts and reasons. Hassan's look at the page is the acceptance. **P.5 done 6 Oct** (`docs/P5_EACH_NONE_2026-10-06.md`, R44): each NONE is right by the engine's rules; three named questions for Hassan; S10's `no_ladder` now has its own words on the page | section 3.7; #317; #318; the P.5 report |
| D5 (the eight flat ledgers) | **retire s1, s3-s9, export first, through P.2's targeted mode; never the desks of s2, s10, s11, s12 or the Portfolio desk** (Hassan, 5 Oct). **Done 5 Oct 21:19Z** (P.2): the eight retired, the others untouched | chat, 5 Oct; section 3.7; #317 |
| Phase 1 (sources, table) | **merged** as #314 (`33d90d6`, 6 Oct) | `docs/WXPREDICT.md`, `table_meta.json` |
| 1.1 (R37: 2 review findings) | **done 6 Oct**, both threads answered and resolved, CI green on `770c987` and on the merge with main (`25a9e32`). (a) One whole-day rule (`common.max_gap_h`, gaps of 3 h or less from midnight to midnight, and the reports must run past the day's end) everywhere a day's maximum is read. `station_daily` gains `max_gap_h`: a fresh fetch of the climate windows rebuilt the old file byte for byte, and the new file differs only in that column on all 123,452 rows. Table: 686,111 -> 684,990 rows. All 9,586 venue events are kept; 30 of them have no whole station day, so no station verdict. Unlisted station days 11,855 -> 11,820 (left out: 38 with a gap, 8 with no report, 1 not reported to its end). (b) `decision_local` is the decision instant (hh:01) and parses back on all 684,990 rows. Two builds are byte-identical (sha256 `d03b73e8...`); 8 mutations, each caught; both suites green | commit message; #314 threads; `table_meta.json` |
| G1 / D2 | **passed 6 Oct**: Hassan, "merge 314"; merged `33d90d6` | chat, 6 Oct |
| Wave 2 (2.A-2.F) | **2.A measured 6 Oct (section 3.8): the archive cycle alone cannot reach the checkpoint.** The project is on Supabase's Free plan (read-only above 500 MB, per its docs), at 574.7 MiB, growing about 5 MiB a day; the gap to 450 MiB is 124.7 MiB. Cuts that leave every reader whole: 30.0 MB. With readers changed (s2's correlation input, the plan-approval warning, the analytics page, the backtest's book reader and regime book age): 85.6 MB. Waits on D9 (recommended: the Pro plan). 2.B waits on A.3's checkpoint. 2.C-2.F todo | section 3.8; section 9 (D9) |
| Wave F (F.1-F.7, the seasonal focus) | **F.1 done 6 Oct:** the Seasonal Focus 10 is lucknow, karachi, helsinki, wellington, tel_aviv, milan, chicago, moscow, miami, amsterdam. They were chosen on pre-cutoff data by `tools/focus/season_predictability.py` and recorded in `docs/FOCUS_PREREG.md` and `focus_sets`; the set is evaluated from 8 Oct. **F.2-F.5 built 6 Oct:** a measured status rule (forecast disagreement adopted; cloud, pressure and wind tested and not adopted), `v_city_status`, one selection for all of Predictive, the record on each card, and the focus against all. **F.7, 7 Oct:** the momentum trigger was measured on pre-cutoff data and not adopted (no rule pays after costs). F.6 and the rest of F.7 (the Candidate gate, the per-city list) todo | `docs/FOCUS_PREREG.md`; `data/eval/focus/`; `v_city_status`; `web/lib/focus.ts` |
| Phase 2 (2.1-2.5) | **2.1, 6 Oct: up to a 0.10 spread, `p` is the book's midpoint; over 0.10 it is open.** On hours where `p` did not move and the spread is at most 0.10, the mid matches it at the 90th percentile exactly (54,415 pairs, 98.6% within 0.01). The bid, the ask and the last trade are each further off. **Spreads over 0.10 (271 pairs, 0.5%) miss the tolerance:** 84.9% within 0.01, 90th percentile 0.02. The venue's display rule (the last trade) does not explain them: on the 25 with an archived trade, it is within 0.01 on 5 against the mid's 15. Cause not measured. The live reader (2.2) reads the mid for every bucket, with the spread stored. 2.3 reports the wide buckets on their own, and G2 sees them. 2.2-2.5 todo | `docs/WXPREDICT.md` (Phase 2.1); `tools/wxpredict/what_is_p.py` |
| G2 | | |
| Phase 3 (3.0-3.7) | todo (exploratory first look in section 2.2 only) | |
| G3 | | |
| Phase 4 (4.1-4.3) | todo (sealed test untouched) | |
| G4 | | |
| Phase 5 (5.1-5.6) | todo (5.6 only if G4 shows an edge) | |
| G5 | | |
| Full testing (7.1-7.7) | todo | |
| Dated: rd3 / `da_floor` / `sd_corr` first looks | ~25 Oct, as pre-registered | |
| Dated: P3.10 ensemble test | ~29 Oct | |
| CI minutes used by this build | #314: 4 CI runs before 5 Oct ~17:30Z (pytest 3.5-5.0 min each); 0 since (three `[skip ci]` docs pushes). #315: 2 runs, 305 s and 313 s, 6 billed each. #317: 1 run, 235 s, 4 billed. #316: 2 runs on `2ff90c8` and 2 on `8e3e19a`, all cancelled before a runner was assigned (0 billed). `retire_desks.yml`: 43 s, 1 billed (jobs API). #316's re-run by Hassan: green, minutes not measured. #318: Tests 3 runs (the first cancelled by the next push; then 5 min 42 s and 5 min 16 s), web build 3 runs (57 s failed on Node 20, then 1 min 40 s and 1 min 36 s), as run times from the runs API, queueing included; billed minutes not measured | jobs API; runs API |
