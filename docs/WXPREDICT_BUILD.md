# WXPredict: the build document

**Status: v2, the final build document, 5 Oct 2026 ~16:30Z, awaiting Hassan's approval. Nothing beyond phase 1 is built until he approves it.**

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
| R1 | **Actions minutes, October** (Rule 7) | 941 billed minutes from 1 Oct to 5 Oct 16:04Z, over 283 runs (jobs API, each job rounded up). **CI: 419** (tests 331, web 88). **Scheduled: 522** (pipeline_daily 153, tick 113, intraday 105, forecasts 71 before it left the clock on 4 Oct, archive 69, other 11). The cadence since 4 Oct costs about 91 a day at the per-run costs measured 1-5 Oct (tick 24 × 1, intraday 4 × 4, pipeline_daily ~37, archive ~14). That projects to about **3,300 by 31 Oct with no CI at all, over the 3,000 budget**. | Hassan chooses: cut cadence (only P6.1 may), allow paid overage, or both. Then the build's own CI is capped (one push per step, about 25 pushes in total, which is about 125-150 CI minutes, measured as it goes). | 0, Hassan |
| R2 | **Database size** (P1.6) | **575 MB, 114.9% of the 500 MB tier** (`pg_database_size`, 5 Oct). P1.6's record says phases 1-2 done and 3.1-3.3 live. | Measure the largest tables; finish P1.6 phase 3 through the archive (export, verify, commit, prune: never delete); get under the cap with headroom. WXPredict's shadow table must fit the plan. | 2 |

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
| R16 | P0.3 retire the paper desks | doing; Hassan must start `retire_desks.yml` | `retire_desks.yml` ran on 23 Sep (success). **0 open paper positions** (`paper_trades`): s1 107 closed (−$825.17), s3 41 (−$96.97), s4 49 (+$121.51), s12 1 ($0.00) | Verify each desk's state, then close the step in the record. | 0 |
| R17 | P8.2 retire the eight old ledgers once flat | "not yet" | **they are flat now** (0 open positions) | Retire them through `paper_desk_retire`. This is covered by Hassan's 29 Sep "Retire all of s1, s3-s9", but confirm in section 9, D5. | 2 |
| R18 | P1.2 UI writes behind auth | doing | CLAUDE.md: every browser write goes through the operator route; sign-in off by Hassan | Verify the RPC revoke is live (anon cannot call a write RPC), then close. | 0 |
| R19 | P1.3, P1.5 archiver floors and the push guard | doing; "the next archive run must log ok" | every `archive_*` job ok on 2 of 2 runs in 48 h (research, resolution, trades included) | Check the specific acceptance in each row, then close. | 0 |
| R20 | P1.7 mirror | live; 63 of 75 tables (per the record, 28 Sep) | `mirror_to_repo` ok on 2 of 2 runs | Re-count the coverage; decide on the 12 tables not mirrored (each named). | 2 |
| R21 | P2.2 station agreement view | doing | not re-measured | Verify `sql/ad4_82` is applied, then close or finish. | 0 |
| R22 | P2.8 every forecast model | live; acceptance ≥5 models with lead 1 for every date of the last 7 days | the committed record has 7 models × 48 cities through 3 Oct, and 4 Oct has 280 of 336 rows (R3) | Close once R3 holds for 7 nights. | 2 |
| R23 | P2.10 / P3.10 test (the ensemble record's first 30 days) | waits for 30 days of record | first night 29 Sep → about 29 Oct | Run as planned. | dated |
| R24 | P3.4 forward-only fits | live; applied 0 | trajectory 1,152 cells, applied 0; post-processing 399 cells, applied 2 (27 days) | **Fold**: WXPredict learns these inside one model. The fits keep running for the record. | n/a |
| R25 | P3.6 calibration | doing | `calibration_map`: T fitted 1.264, not applied (`applies: false`), because validation got worse (log loss 1.6822 → 1.6901). 16 settlement dates; the full refit needs 30. Next weekly update 6 Oct. | **Fold** for WXPredict (its own calibration, section 5 phase 3.5). The engine's map runs on as recorded. | n/a |
| R26 | P3.8 hit tournament part 2 | todo | v1 in shadow, nightly ok | **Fold**: WXPredict's scoreboard (phase 5.4) is the per-city learning loop. | n/a |
| R27 | P3.9 the width served | off; on when the forward score's lower bound is above 0 | `station_width_pricing` enabled false | **Fold** (WXPredict's width is learned). The engine's flag stays as is. | n/a |
| R28 | P4.3 repoint `v_city_hit_history` | waits for a week of checkpoints | 12 graded dates (24 Sep - 5 Oct) | Repoint with the equivalence check, or decide not to; this is the hit/miss page. | 2 |
| R29 | P4.5 restore the archived verdicts | pending (10,690) | `restore_verdicts` ok on 2 of 2 runs | Count the restored rows against 10,690, then close. | 0 |
| R30 | P4.6 part 2 (public-forecast hit rate and reliability bins per checkpoint) | not done | not re-measured | **Fold** into the WXPredict scoreboard (phase 5.4). | 5 |
| R31 | P6.2 n8n under 2,000 executions | doing | not re-measured | Count this month's executions; close or finish. | 2 |
| R32 | P6.6 docs clean-up | todo | stale lines found in `PLAN_PROGRESS` today (R15-R19, R29) | Wave 0 corrects the record. The wider clean-up comes at the end (section 7). | 0, 7 |
| R33 | P7.2-P7.9 S10 (rd1/rd3): shadow, portfolio, board, chain | 7.6-7.9 todo | rd1 and S10 run in shadow; **rd3 is blind until its first look, about 25 Oct** (trigger `trig_019QK37oqwVbAMcVWYnRQCVF`) | **Fold**: WXPredict generalises the remaining-day model. S10 keeps running in shadow and rd3's first look runs as pre-registered. After it, Hassan decides whether S10 is retired (section 9, D6). P7.7 (portfolio) is Hassan's (capital). | dated, Hassan |
| R34 | `da_floor:v1`, `sd_corr:v1` | blind, in shadow | first looks about 25 Oct | Run as pre-registered. Not read before then. | dated |
| R35 | P5.5-P5.12 part 2s, P8.3, P8.4 (the trading machinery: solver, timing, fills, learning, rails, the decision log, suite acceptance, conflict rules) | partial | trading only; nothing about forecasting | **Not in this build.** Listed so nothing is lost. They touch capital and trading, so they are Hassan's to schedule after G5 (section 9, D7). | Hassan |
| R36 | `settlement_verified` false | the gate for auto-settlement; `docs/settlement_verification.md` unmeasured | not changed | Hassan's (it pays out); listed only. | Hassan |

### 3.5 This session's own items

| id | item | state | wave |
|---|---|---|---|
| R37 | #314: two Codex findings on `b80b2a7` (the whole-day rule; `decision_local` at hh:01) | verified real (89 of 24,518 station days fail the rule); fix designed (phase 1.1) | 1 |
| R38 | Exploratory model code (`tools/wxpredict/dataset.py`, `model.py`, `walk_forward.py`), outside git | locally excluded, backed up in the scratchpad | 3 (rewritten with tests) |
| R39 | `single-runs-api.open-meteo.com` refused by the network policy | 403 measured | Hassan (D3) |
| R40 | F3-A, dated checks, F4 (the 30 Sep handoff's items) | **F3-A:** `fec-v1` exists; WXPredict's contract (section 4) extends it. **Dated check B** (1 Oct archive) done 3 Oct per the record. **F4:** the 401s fixed (#292) and minutes decided (#302); the rest are R1-R11 above. | 0 (record) |

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
Wave 0   put the record straight; decisions D1-D7 asked     (read-only + one docs PR)
Phase 1  finish #314                                        -> G1 merge
Wave 2   the operations WXPredict depends on (R2-R11, R13-R15, R17, R20, R22, R28, R31)
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

**0.4 Put decisions D1-D7 (section 9) to Hassan**

- Only R1 (minutes) blocks the build. A wave that adds CI or scheduled minutes does not start before D1 is answered.

**Evidence:** one docs PR. It carries no code, so it can ride phase 1.1's push and avoid a CI run of its own.

### Phase 1: finish (PR #314)

**1.1 The two open review findings**

- **What:**
  - **(a) One rule for a "whole day."** A station day is whole when its reports leave no gap over 3 h, counting from local midnight to the first report and from the last report to the next midnight.
    - Measured on 24,518 station days: 89 fail. 81 have a gap over 3 h, and 8 have no report at all.
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

**2.F The remaining record items (R17, R20, R28, R31, R13)**

- **What:** retire the flat ledgers (after D5); mirror coverage; the hit-history repoint (with its equivalence check); n8n executions; the US re-measure at 7 graded days after the fix.

**Gate:** wave 2 needs no Hassan gate, beyond D1 for minutes and D5 for the ledgers. Phase 2 starts when 2.A and 2.B have passed their checkpoints. 2.C-2.F may overlap phase 2.

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

---

## 9. Decisions only Hassan can make

| id | decision | why it is his | blocks |
|---|---|---|---|
| D0 | Approve this document (or say what to change) | the plan | everything |
| D1 | October's minutes (R1): cut a cadence (which one; only P6.1 may), allow paid overage, or both | Rule 7, and spending | waves that add minutes |
| D2 | G1: merge #314 after phase 1.1 | merging | wave 2 onward |
| D3 | Allow `single-runs-api.open-meteo.com` in the environment's network access (the history of same-day runs) | network access | phase 2.5's faster path; optional |
| D4 | Any interim fix to the served engine before WXPredict (e.g. use the evening-before corrected centre on the same day). Default: none; WXPredict replaces it | prices served | none |
| D5 | Retire the eight flat ledgers now (R17); 29 Sep's "Retire all of s1, s3-s9" appears to cover it | the desks | 2.F |
| D6 | After rd3's first look (~25 Oct): S10's future (R33) | strategy | none |
| D7 | When to schedule the trading machinery (R35) and `settlement_verified` (R36) | capital and payouts | none |
| G3-G5 | as in section 5 | the model's use | their phases |

---

## 10. Status

Updated in every PR.

| step | state | evidence |
|---|---|---|
| D0 (this document) | waiting for Hassan | |
| D1 (minutes) | waiting; 941 billed to 5 Oct 16:04Z, projected ~3,300 by 31 Oct without CI | jobs API, 5 Oct |
| Wave 0 (0.1-0.4) | todo | |
| Phase 1 (sources, table) | built, PR #314 open, CI green on `b80b2a7` | `docs/WXPREDICT.md`, `table_meta.json` |
| 1.1 (R37: 2 review findings) | todo (verified: 89 of 24,518 station days fail the rule) | |
| G1 / D2 | waiting | |
| Wave 2 (2.A-2.F) | todo | |
| Phase 2 (2.1-2.5) | todo | |
| G2 | | |
| Phase 3 (3.0-3.7) | todo (exploratory first look in section 2.2 only) | |
| G3 | | |
| Phase 4 (4.1-4.3) | todo (sealed test untouched) | |
| G4 | | |
| Phase 5 (5.1-5.5) | todo | |
| G5 | | |
| Full testing (7.1-7.7) | todo | |
| Dated: rd3 / `da_floor` / `sd_corr` first looks | ~25 Oct, as pre-registered | |
| Dated: P3.10 ensemble test | ~29 Oct | |
| CI minutes used by this build | #314 so far: 4 CI runs (pytest 3.5-5.0 min each) | jobs API |
