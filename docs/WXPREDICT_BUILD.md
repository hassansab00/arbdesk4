# WXPredict: the build document

**Status: v1, written 5 Oct 2026 ~16:00Z, awaiting Hassan's approval. Nothing beyond phase 1 is built until he approves it.**

This document governs the WXPredict build. A new session resumes from it and
from `docs/PLAN_PROGRESS.md`.

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
| Actions minutes are a hard budget (Rule 7) | 3,000 a month, CI included; October was at 890 billed by 5 Oct 09:00Z. No new scheduled workflow (only P6.1 may add one). Every change to a scheduled job's runtime is measured before merge and written into `MEASURED_MINUTES`. |
| Adaptive never means unbounded (Rule 11) | Every learned parameter has a prior, hard bounds, a minimum sample, a maximum change per nightly update, and a version on every decision that used it. Nothing is evaluated on data it learned from. |
| Blind tests stay blind | rd3, `da_floor:v1` and `sd_corr:v1` are pre-registered. Their scores are not read before their first look (about 25 Oct; `docs/CHALLENGER_C_PREREG.md`, `docs/P11_DA_FLOOR_PREREG.md`, `docs/SD_CORR_PREREG.md`). WXPredict is compared with the market and the served engine, never with them, until they are unblinded. |
| The sealed test stays sealed | 1 Sep - 4 Oct 2026, and every listed day after it, is WXPredict's final test (section 3). No model output on those days is scored or looked at before G4. |
| Both suites before every push | `PYTHONPATH=scripts python -m pytest tests/ -q`; `npm ci --prefix tests/database --ignore-scripts && npm test --prefix tests/database`. For web changes also: `cd web && ./node_modules/.bin/tsc --noEmit && npm run test:routes && ./node_modules/.bin/next build`. |
| Generated files are regenerated | `python3 tools/gen_provenance.py && python3 tools/gen_sql_owner.py`. They are never edited by hand. |
| Few pushes | One validated push beats three speculative ones. Progress notes ride the work's PR. |
| Merging | Merge when CI is green on the PR's head and Hassan has said to merge. Codex's review is read and every finding is answered, but it does not hold a merge Hassan has ordered. |
| Secrets | No key value is ever printed. The Supabase secret key never goes in `NEXT_PUBLIC_*` or an n8n Config node. Email workflows stay disabled. |
| Focus | Only WXPredict. Anything else noticed is mentioned in one line and not worked on. |

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
   - The likely cause is the floor read from the NWS five-minute feed. That was fixed 4 Oct (#297) and is not yet confirmed on a graded day.
6. **Too many half-built models.** The engine, S10 (rd1/rd3), `da_floor`, `sd_corr`, MOS, the weather model, the trajectory, the hit tournament and the post-processing fit are each partly wired, and nothing combines them.

**WXPredict is the one model that replaces this patchwork as the same-day predictor.** The old paths keep running untouched until Hassan decides otherwise (G5). Nothing is retired or deleted by this build.

### Hassan's requirements, and where each is met and checked

| requirement (Hassan, 5 Oct) | design | verified in |
|---|---|---|
| Beat the market | Model A (weather) fused with the market (C), judged on the venue's winner against the market's own price at the same instant | 3.4, 4.1 |
| The market is a datapoint, and must be fresh | The market's price at the decision is an input. Training uses the venue's hourly snapshot at the decision instant. Serving reads the live price at the tick (phase 2) | 1.x (table), 2.1-2.3 |
| City-specific | City is a learned categorical input; per-city error and peak-hour history; per-city calibration checked | 3.2 ablation B, 3.5 |
| Market activity by time and date | Local hour, weekday, day of year; the market's moves over 1/3/6 h; bucket-hours that moved (an activity proxy); volume once a history exists | 3.2 ablation E, 2.4 |
| All weather parameters | Every field the station reports, plus the hourly forecast's temperature, dew point, cloud, sunshine, wind, rain and pressure, and the seven models | 3.2 ablations |
| Adapts to rises and falls | 1 h / 3 h changes, observed minus forecast now and 3 h ago, the running maximum and its age, the remaining forecast curve | 3.2, 3.6 (post-peak check) |
| Predicts the max from all weather permutations | One gradient-boosted model over all inputs together, so interactions are learned, not hand-coded | 3.2 |
| Reflected in the current UI | `/predictive` (the lineup and a WXPredict scoreboard), `v_prediction_contract`, the model registry | 5.4, 5.5 |

---

## 2. Where we are (5 Oct ~16:00Z)

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

## 3. The evaluation contract: frozen before any model is judged

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
- **Post-peak** (the engine's defect 1): D rows from 2 h after the city's typical peak where the running maximum already sits in the winning bucket. WXPredict's mean probability on the winner against the market's and the engine's (0.621).
- **Morning centre** (defect 3): mean absolute error of WXPredict's expected maximum at 09:00, against the engine's raw centre (0.99) and the corrected one (0.77) on the same days.
- **US** (defect 5): the number of US rows where WXPredict gave the winner under 1%, against the market's count.
- **Per city:** gain and interval for every city with 20 or more scored dates.

**What a pass does not mean.** A log-loss gain is not a trading edge: costs, spreads and fees are not in it. Trading on WXPredict is outside this build and is Hassan's (Rule 6).

---

## 4. The build, step by step

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

- **What:** `docs/WXPREDICT_PREREG.md`, holding section 3 verbatim plus:
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
- **Ablations**, each walk-forward on the development months and scored by section 3's metric against the market:
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

- Post-peak, morning centre and US, as in section 3. Each is reported with its numbers.
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

- **What:** v1 exactly as frozen (its commit hash), walk-forward through 1 Sep - 4 Oct (and later listed days), scored by section 3.
- **Evidence:**
  - `docs/WXPREDICT_RESULT_<date>.md`, generated by the tool from committed inputs only;
  - the scored rows in `data/eval/wxpredict_<date>.csv.gz`.
- **Verification:** the report reproduces byte for byte from the committed inputs (a test).

**4.2 Against the engine as served**

- On the graded checkpoints in the sealed period (`v_checkpoint_outcome`, the engine's own calls), at the same checkpoints.
- Not against rd3, `da_floor` or `sd_corr` until they are unblinded.

**4.3 The verdict**

- Pass or fail by section 3's rule, as written.
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

- Pre-registered before shadow starts: section 3's rule on the first 20 graded shadow days.
- Reported as it stands each week. No change to v1 during the period.

**G5 (Hassan):** whether WXPredict serves any price. Anything about capital stays his alone.

---

## 5. Testing, all in one place

| layer | tests | when |
|---|---|---|
| data (phase 1) | `tests/test_wxpredict_table.py`: leak plants (report, forecast hour, price, past day), DST instants, whole-day rule, labels, determinism, the seed, the station-day hand-over, the offset guard | every push |
| model (phase 3) | `tests/test_wxpredict_model.py`: bucket mapping, fusion recovery, softmax, walk-forward cutoffs, sealed-month refusal, seed determinism | every push |
| serving (phases 2, 5) | parity against the builder; pure-Python against scikit-learn; tick budget; recorded API answers | every push, plus 3 real days |
| database | a contract per migration in `tests/database` (RLS, grants, re-runnable, `anon` reads views only) | every push |
| web | `tsc`, `test:routes`, `next build`, plus a by-hand number check | every web PR |
| live | each change checked on its next real run: tick logs, row counts, view as `anon` | after each merge |

---

## 6. Risks, and what is done about each

| risk | handling |
|---|---|
| WXPredict does not beat the market by a meaningful margin | It is measured, not assumed (phase 4). The report says so plainly. The next lever, fresh same-day forecast history (2.5), is Hassan's choice. |
| Training/serving skew | The parity test (2.3) with one shared feature module. |
| A leak in features | Leak-plant tests per input; Codex review; the sealed test. |
| Over-fitting the development months | The sealed test is scored once (phase 4); ablations are kept small and pre-registered. |
| Actions minutes | Pure-Python tick inference; the fit inside the existing nightly job; every change's minutes measured before merge. |
| scikit-learn version drift | Pinned versions; the exported trees are the served artifact, so the tick does not depend on it. |
| A blind test's integrity | WXPredict is never compared with rd3 / `da_floor` / `sd_corr` before their first look. |
| Context loss across sessions | This document plus `PLAN_PROGRESS`, updated in every PR, and the status table below. |

---

## 7. Status

| step | state | evidence |
|---|---|---|
| 1 (sources, table) | built, PR #314 open, CI green on `b80b2a7` | `docs/WXPREDICT.md`, `table_meta.json` |
| 1.1 (2 review findings) | todo (findings verified: 89 of 24,518 station days fail the rule) | |
| G1 | waiting | |
| 2.1 - 2.5 | todo | |
| G2 | | |
| 3.0 - 3.7 | todo (exploratory first look in section 2.2 only) | |
| G3 | | |
| 4.1 - 4.3 | todo (sealed test untouched) | |
| G4 | | |
| 5.1 - 5.5 | todo | |
| G5 | | |
