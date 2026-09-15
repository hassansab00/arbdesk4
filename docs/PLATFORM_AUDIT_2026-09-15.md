# ArbDesk4 — full platform audit and finalization plan

Prepared 15 September 2026, 08:30–10:30 UTC, against the live system: repository
`hassansab00/arbdesk4` at `5c466f9`, Supabase project `jittmxhzgqpifitwupss`
(Postgres 17, eu-west-1), the n8n Cloud instance (`steelwyre-stratify`), and the
GitHub Actions history (872 runs). Every number below was read from the live
database, the live n8n execution log, or the live Actions logs during this
session unless it says otherwise. Nothing was changed: no SQL was applied, no
workflow was edited, no job was dispatched.

---

## 0. Verdict in one page

**Collection works. Synthesis does not.** The desk collects the right raw
material on schedule — markets, order books, trades, two weather-observation
feeds, three forecast feeds and authoritative settlement evidence are all
current to within their cadence this morning. But nothing downstream of the
collection layer has produced a tradeable decision, and the reasons are
mechanical, not statistical:

| # | Root cause | Effect today | Fix size |
|---|---|---|---|
| RC1 | **PostgREST caps every response at 1,000 rows and silently ignores a larger `limit`.** Ten reads across five engines still page with a 10,000–200,000 assumption and stop after the first page. | Forecast skill is measured for **1 of 54 cities** (New York, whose first 1,000 forecast rows happen to reach today; Amsterdam's stop on 7 Aug, before any verified outcome). The weather model receives 1,000 of 21,939 training rows and rejects every city as "under 120 days". Calibration and the databank band freeze read partial inputs. Result: **2,189 of 2,594 priced bands are `no_verified_skill`, only 33 are pricing-eligible, 15 edges are tradeable.** | Small. Switch the reads to the existing `common.rest_all`, as the databank was on 14 Sep. Three files, one guard test. |
| RC2 | **The strategy signal step is gated behind a GitHub repository variable (`PAPER_TRADES_ENABLED`) that is not set.** | The intraday job skips "Strategy signals", "Paper strategy proposals", settlement, exits and the recovery sweep on every run (verified on run #34, steps 8–13 `skipped`). All nine strategies are enabled in the database and **none has ever been evaluated by the current engine**. The 1,894 rows in `signals` are `system` alerts from the old runner, 1,893 of them "pending approval" forever. | Trivial to flip; but flip it only after RC1, or it will evaluate nine strategies against a board where 99% of bands are ineligible. |
| RC3 | **The evidence chain is gated harder than the data supports.** Pricing requires skill measured against *verified station evidence*; verified evidence started on 14 Sep (748 city-days, 50 cities, ~15 days each); calibration needs 300 settled bands and has 0; the databank's band freeze needs venue resolution rows and has 0 (its venue read fails with a 400 because it puts ~1,000 market ids in one URL). | Even after RC1, sample sizes are 15–19 days per city: skill will be thin, calibration will not fit for weeks, and `fact_band_outcome` will stay frozen at 8 Sep until the URL bug is fixed. | Medium. Fix the URL chunking, let evidence accrue, and add the backfill of verified outcomes for the whole archive (the WRH time series is available historically). |
| RC4 | **n8n cadence is not governed by the settings the UI shows.** `settings.workflow_schedules` says every job is `manual` (0 runs/month); the live workflows run on their own Schedule Triggers and report `trigger: "production"`, which `should_run()` treats as a manual override. | The Workflows page's schedule control is decorative; P0.3 (hourly) and P2.2 paper maintenance (hourly, doing nothing: 0 orders) alone consume ~1,440 executions/month against a ~2,000 budget shared with 40 other workflows on the same instance. | Small: pass `schedule` for scheduled triggers, and drop P2.2 to a sane cadence. |
| RC5 | **Live weather timestamps are in the future for Asia/Pacific cities.** P1.5 writes Open-Meteo's `current.time` (local wall-clock under `timezone=auto`) as if it were UTC. | 20 of 54 `live_weather` rows are stamped up to 6.5 h in the future; `minutes_to_peak` is null for all 54 rows; `day_decided` is false everywhere. Strategies S5 and S7 can never pass their gates; the City Monitor and "where is the day" panels are wrong for half the world. | Small: add `utc_offset_seconds` or request `timeformat=unixtime`; rebuild the `live_weather` projection. |
| RC6 | **The database is at 503 MB on a 500 MB plan, mostly index bloat left by the 14 Sep prune.** `weather_forecasts` holds 77k rows but 125 MB (79 MB of indexes); `trades_observed` 107 MB, never vacuumed. | Statement timeouts (57014) already hit `recompute_capacity` and P0.4's volume rollup; a hard stop on writes is the next symptom. | Operational: `VACUUM FULL` + `REINDEX` on four tables, then a size guard. |
| RC7 | **The application is credential-free and 19 `SECURITY DEFINER` RPCs are executable by `anon`**, including `approve_signal`, `close_position`, `log_paper_trade`, `paper_desk_create/reset/update`, `update_setting`, `set_strategy_enabled`, `queue_backtest`. `settings` (which holds the n8n webhook URLs) is readable by the browser key. | Anyone who finds the deployment URL can reset the paper desk, flip strategies, change settings and fire n8n webhooks. Acceptable for a private prototype; not for a proprietary product. Vercel deployment protection could not be verified from this session. | Medium: owner authentication (Supabase Auth) on the command path; move mutating RPCs to `authenticated`+owner check; keep reads anonymous if desired. |

Everything else in this document hangs off those seven. The plan in §5 is
ordered so that RC1 and RC5 land first (they unlock pricing and timing with
almost no code), RC2 is flipped the same day, and the trading loop is then
proven end-to-end on paper before any modelling work starts.

**What is genuinely good and must be preserved:** the collection layer (13 n8n
workflows, 5 scheduled Actions), the append-only evidence design
(`weather_resolution_evidence`, `weather_resolution_attempts`,
`research_captures`, `proprietary_data_corrections`), the honest empty-states in
the UI, the truthful health views (`v_operational_health`,
`v_execution_health`, `v_outcome_evidence_health`, `v_data_freshness`), the
schedule/budget accounting, and the test discipline (796 tests, an n8n
structural validator, a database contract suite, a web build that mirrors
Vercel). This is a system that knows how to say "I don't know"; it now needs
to be allowed to know.

---

## 1. What was inspected, and how

| Layer | Method | Coverage |
|---|---|---|
| Repository | Full tree read; `PYTHONPATH=scripts pytest` (796 passed, 6 skipped, 12 s); `tools/gen_provenance.py` (no diff); `scripts/validate_n8n_workflows.py` (**crashes**, see §4.7); `npm ci && npm run typecheck && npm run build` in `web/` (clean, 18 routes) | 100% of tracked files listed; engines, SQL, workflows, web read in depth |
| Database | 60+ read-only queries via the Supabase MCP: row counts (exact, not estimates), freshness, health views, block-reason distributions, evidence status, grants, policies, function bodies, view definitions, relation sizes and vacuum state; security and performance advisors | All 71 tables, 91 views, the 13 settings, the RPC surface |
| n8n | Workflow list (53 workflows, 13 AD4), 242 executions since 13 Sep, full data of the failed P0.4 execution 7379; delegated per-workflow audit of all 13 live definitions vs the repository templates | See §4.2 |
| GitHub Actions | Run history for all 11 workflows, step-level status of intraday run #34 and daily runs #14/#15, failure logs of daily #11, forecasts #25, intraday #30 | See §4.3 |
| Web | Static audit of all 18 routes, 3 API routes, hooks and libs against the live view/column shapes; production build | See §4.5 |
| Not verifiable from here | The Vercel project (no team is visible to the Vercel connector), the deployed URL and its deployment protection, the n8n plan's execution allowance, the GitHub Actions bill, the paper-worker host (none exists), email delivery (disabled) | Called out where relevant |

---

## 2. Live state scorecard (15 Sep 2026, 08:30 UTC)

### 2.1 Market data (Polymarket)

| Item | Live value | Verdict |
|---|---|---|
| Markets known / open (resolution ≥ today) | 1,455 / 102 | ok — discovery current (`last_seen_at` 08:18 today) |
| Latest resolution date | 16 Sep | ok |
| Days with no market rows, 25 Aug → today | none | ok — the 6–11 Sep hole was backfilled today via `days_back` |
| Open markets with `condition_id` | **0 of 102** (0 of 1,455 overall) | **defect** — P0.2 never stores the CLOB condition id; venue-confirmed settlement (`paper_settlement.py`) and `v_venue_*_resolution` cannot match anything, hence `confirmed_markets = 0`, `venue_evidence_rows = 0` |
| Open markets with rules text | 50 of 102 | attention — P0.5 runs daily; the other 52 were discovered after its last run |
| Open markets with venue tick/min-order size | 0 of 102 | defect — `execution_limits` is a provisional global setting, not per market |
| Book snapshots (total / last 24 h / newest) | 87,941 / 19,528 / 08:01 today | ok — hourly capture works |
| P0.3 fetch failures per hour | 2–5 in daytime, **220–383 at 21:00–03:00 UTC** | attention — a third of the 1,000-band cap fails overnight; the delegated n8n audit names the cause |
| Trades observed (total / newest) | 147,170 / 02:52 today | ok — P0.4 every 6 h, 5,000 trades/run cap |
| Historical unit conflicts (market says F, city says C) | 697 markets, 0 open | corrected via `proprietary_data_corrections` (667 unit + 9,182 label corrections) — the canonical views are clean; the raw rows are preserved |
| Zero-width non-tail bands (raw) | 6,157 | corrected in `v_canonical_bands` (15,761 rows) — raw preserved |

### 2.2 Weather data

| Item | Live value | Verdict |
|---|---|---|
| Cities active / with coordinates / with timezone | 54 / 54 / 54 | ok (17 coordinates still `to_verify` against the settlement station, 0 verified) |
| Station observations (IEM METAR, Actions) | 234,343 rows, newest 06:40 UTC; station ZSJN (Jinan) fails every run | ok, one dead station |
| NWS observations (n8n P1.2) | 12 US cities, 680 rows per run, every 2 h | ok |
| Forecast rows / newest run / horizon | 77,282 / 03:00 UTC / to 22 Sep | ok |
| Models with a future per city | 12 US cities: 3 (NWS, OM best-match, OM forecast); 22 cities: 2; **20 cities: only `open_meteo_forecast`** | attention — for 20 cities the divergence mechanism is dormant ("sigma cannot widen") |
| `live_weather` (54 rows) | 20 rows time-stamped in the future (RC5); `minutes_to_peak` null ×54; `day_decided` false ×54; US rows carry `source=open-meteo` with `obs_source=NWS` | **defect** |
| Forecast-condition features (P1.4, NWS gridpoint) | 14,544 rows, 12 cities | ok, US only |
| Ensemble forecasts | **0 rows** (table exists, "cannot be backfilled") | not started |
| Authoritative outcome evidence (WRH time series) | 748 city-days verified, 50 cities, 24 Aug → 14 Sep; 338 attempts `unsupported_source` (Weather Underground primaries: Taipei, Jinan, Jakarta, Lagos; markets without saved rules); 228 `not_final` (yesterday) | ok and growing ~50/day; WU adapter missing |
| Cities with 0 verified days | DC, Jakarta, Jinan, Lagos (+Taipei 1) | attention — DC's rules name "NOAA timeseries" with a null precision rule and no open market; the WU cities need an adapter |

### 2.3 Model layer

| Item | Live value | Verdict |
|---|---|---|
| Skill rows in the latest run | 7 rows, **1 city** (NYC, n=18 at lead 1) | **defect (RC1)** — 45 cities have 17–19 joinable verified days and would clear the 10-day minimum |
| Latest-prob bands by state | 869 `city_lead_proxy` blocked, 693 `fixed_cold_start` (σ = 8.2 °C) blocked, 627 `city_lead` blocked, 372 `legacy` eligible (pre-12 Sep rows), 22 + 11 eligible | **defect (RC1/RC3)** |
| Edges in the latest run | 2,200 rows: 1,968 `no_verified_skill`, 188 `no_book`, 26 `dead_band`, 2 `anomaly`, **15 tradeable** | consequence |
| Weather model (per-city correction) | 0 of 54 fitted; "insufficient usable data" | **defect (RC1)** — it reads 1,000 of 21,939 feature rows |
| Model forecast (AD4's own prediction) | 0 rows, "no fitted model" | consequence |
| Calibration map | not fitted: 0 settled bands banked, 300 needed | consequence (RC3 + URL bug) |
| Calibration feedback (σ honesty) | 0 cities | consequence |
| Capacity / correlation / peak hour / climate / feature cache | all refreshed 16:57 yesterday, 08:49 today | ok (capacity was timing out until sliced per city on 14 Sep) |
| Prediction scorecard (public-model skill on verified days) | lead-1 MAE 0.9–1.9 °C, lead-7 2.1–2.4 °C; day-ahead bias −0.26 °C across 53 cities | this is the baseline to beat |

### 2.4 Decision and paper-trading layer

| Item | Live value | Verdict |
|---|---|---|
| Strategies enabled | **9 of 9** (docs say "all ship disabled") | inconsistent with the documented rollout; harmless today because the engine never runs |
| Strategy signals ever produced by `signal_engine.py` | 0 | **defect (RC2)** |
| `signals` rows | 1,894, all `system / ALERT / implausible_edge_anomaly`, 1,893 pending approval, last 13 Sep | queue pollution — alerts need their own lifecycle |
| Paper accounts / orders / positions / fills | 3 / 0 / 0 / 0 | never exercised end-to-end |
| Paper worker | no host provisioned; `P2.2_paper_trades` n8n workflow not imported; `/api/paper-cycle` has nothing to wake | **not deployed** |
| Legacy `paper_trades` / `ledger` | 0 / 0 | empty by design |
| Backtests | 5 runs: 2 failed (old `metrics` contract), 3 "complete" with **0 trades over 739 city-days** and empty result sets | vacuous — nothing was eligible to trade |
| `settlement_verified` gate | false | unchanged since build; superseded by the venue-confirmed adapter, which has no condition ids to work with |

### 2.5 Evidence, learning and integrity

| Item | Live value | Verdict |
|---|---|---|
| `fact_forecast_outcome` | 5,230 rows, refreshed today (+204) | ok |
| `fact_band_outcome` | 5,605 rows, **frozen since 8 Sep**, 0 verified, 0 venue-confirmed | stale (URL bug + no condition ids) |
| `fact_signal_outcome` | 1,894 rows = the system alerts | meaningless as trading evidence |
| Research captures (immutable revisions) | 26,565 rows (skipped in Actions because `PAPER_TRADES_ENABLED` is unset — the rows came from the 12–13 Sep runs) | paused |
| Integrity manifests | 0 (baseline never created) | not started |
| Quality flags | 0 | detector never run |
| Synthesis findings | 6 established (forecast lean, morning tell, persistence bar, cloud effect, peak hour), `desk_vs_market` collecting with n=0 | honest |

### 2.6 Operations

| Item | Live value | Verdict |
|---|---|---|
| Database size | **503 MB** (plan limit 500 MB); `weather_forecasts` 125 MB for 77k rows (79 MB indexes); `trades_observed` 107 MB, never vacuumed | **at risk (RC6)** |
| Statement timeouts in the last 48 h | `recompute_capacity` (fixed 14 Sep), P0.4 "Refresh volume rollups" (execution 7379), PostgREST 502s during schema reloads | attention |
| Actions: scheduled runs | intraday 6/day, observations 4/day, daily 1/day, forecasts 1/day, model 1/week, archive 1/month | matches the budget doc |
| Actions: failures in the last 48 h | forecasts #25 (Open-Meteo previous-runs read timeouts; 28/54 cities in 20 min), daily #10/#11 (capacity timeout, fixed), intraday #30 (PostgREST 502, retry added), archive #7/#8 (fixed) | every one has a fix in `main` |
| n8n: AD4 executions/day | ≈ 62 (24 P0.3 + 24 P2.2 + 12 P1.2 + …) ≈ **1,860/month** before the Stratify workflows sharing the instance | **over budget (RC4)** |
| Security advisors | 63 SECURITY DEFINER views (ERROR), 19 anon-executable SECURITY DEFINER functions, 29 mutable search paths, 16 RLS-without-policy tables | see RC7 |
| Performance advisors | 25 unindexed foreign keys, 31 unused indexes, 4 duplicate indexes, 7 duplicate permissive policies | secondary |

---

## 3. The seven root causes, with evidence and the exact fix

### RC1 — PostgREST's 1,000-row cap is silently truncating engine reads

**Evidence.** The project's own commit `e850e65` (14 Sep) proved the cap on the
databank ("PostgREST caps a response at db-max-rows — 1,000 on this project —
and IGNORES a larger ?limit= without a word") and moved four reads to
`common.rest_all`. The same pattern survives elsewhere:

| Read | File:line | Asks for | Receives | Consequence (verified live) |
|---|---|---|---|---|
| `weather_forecasts` per city, `order=for_date.asc` | `scripts/measure_skill.py:101-117` | 10,000 | 1,000 | For Amsterdam the 1,000 oldest rows end 7 Aug; verified outcomes start 24 Aug → zero pairs → no skill row. NYC has 1,099 rows in the window so its page reaches 14 Sep → the only city with skill. Cities with short archives (Ankara, Paris, Hong Kong, Qingdao) reach the join but have 4–6 pairs, under `MIN_SAMPLE=10`. |
| `v_verified_weather_outcomes` per city | `measure_skill.py:32-51` | 10,000 | ≤1,000 | harmless today (≤16 rows/city), wrong by construction |
| `v_city_day_features` (all cities, no order) | `scripts/weather_model.py:652-656` | 200,000 | 1,000 | 21,939 rows exist; 1,000 ÷ 54 ≈ 18 days per city < `MIN_DAYS=120` → every city skipped → "no fitted model" on every weekly run |
| `v_forecast_features`, `derived_weather_model`, `v_city_day_features` (recent) | `weather_model.py:531-537, 598-602, 626-630` | 5,000–20,000 | ≤1,000 | forward prediction and anchors truncated once the model exists |
| `v_verified_fact_band_outcome` | `scripts/calibration.py:129-133` | 50,000 | ≤1,000 | calibration will fit on the first 1,000 bands forever |
| `bands` per 100 markets | `scripts/databank.py:237-240` | 20,000 | 1,000 | 100 markets × 11 bands = 1,100 → ~9% of bands never banked |
| `markets`, `band_probabilities`, `v_opportunities`, `signals`, `paper_trades`, `fact_signal_outcome` | `databank.py:206-208, 254-263, 299-309` | 5,000–50,000 | ≤1,000 | partial freezes, silently |
| `derived_city_correlation` | `scripts/signal_engine.py:147-148` | 2,000 | 1,000 | correlated-exposure check sees 1,000 of 21,750 pairs |
| `v_forecast_divergence` | `scripts/probability_engine.py:303-307` | 5,000 | 1,000 | its own "cap reached" warning at 5,000 can never fire |
| `book_snapshots` (backtest) | `scripts/backtest/runner.py:135-138` | unbounded | 1,000 | later bands get no book → 0 trades |
| `bands` (n8n P0.3 "Load live bands") | live workflow `e0HiTavIzilGmBPo` | 5,000 (Config says 1,200) | **1,000** | every run logs `requested: 1000`; with ~1,670 bands eligible (yesterday's + today's + tomorrow's markets) the arbitrary first 1,000 get books; `v_book_capture_health` shows 116 bands `not_attempted_or_unrecorded` and the edge engine 94 `no_book` |

**Fix.** (a) Replace every bare `rest(..., limit>1000)` with
`rest_all(path, filters, order="<unique stable order>")`; for `measure_skill`
order by `for_date,lead_days,model,run_at` and, better, filter `for_date` to the
verified-outcome span first so the read is 1/10th the size. (b) Add a test that
fails on any `rest(` call whose `limit` exceeds 1,000, generalising
`tests/test_databank_outcome_truth.py:73`. (c) In P0.3 filter
`markets.resolution_date=gte.<today local>` and page with `Range` headers or
`offset` until a short page. (d) Optionally raise `db-max-rows` in the Supabase
API settings to 10,000 — but keep (a)–(c), because a cap will always exist.

**Expected effect.** Skill for ~45 cities at leads 1–7 on the next daily run;
`pricing_eligible` for those cities on the next intraday run; the weather model
fits on Monday's run (or on a manual dispatch) for every city with ≥120 cached
days (most have 180+).

### RC2 — the signal engine is switched off by an unset repository variable

`pipeline_intraday.yml:88-111` gates six steps on
`vars.PAPER_TRADES_ENABLED == 'true'`. Run #34 (today 04:49) shows all six
`skipped`. `docs/paper_trades_rollout.md` step 7 says to set it "after
migrations and worker checks"; the migrations were applied on 12 Sep, the
worker was never deployed, and the variable was never set — so the strategy
runner, which was only restored into that gated block on 14 Sep (`98bfa06`),
has never run in production.

**Fix.** Set the variable to `true` once RC1 is merged. Before that, the engine
audit found two defects that would make the first run fail anyway
(§4.4, items 1 and 2): `signal_engine.py:167` writes a UUID into
`signals.signal_id`, which is `bigserial`, and the payload lacks the
`decision_at`/`decision_inputs` keys `paper_plans.py:29` requires. Fix those in
the same change.

### RC3 — the evidence chain is starved and one link is broken

* Verified outcomes exist for 15–16 days per city (24 Aug → 14 Sep, minus the
  6–11 Sep market gap). That is enough for skill (≥10 pairs) but not for a
  stable width, and it is nowhere near the 300 settled bands calibration wants.
* The band freeze (`databank.bank_bands`) has produced nothing since 8 Sep
  because `databank.py:214-227` puts every open market id into one
  `market_id=in.(…)` URL (≈37 KB) and PostgREST answers 400. The bands read
  five lines lower already chunks by 100; the two venue reads do not.
* Venue-confirmed resolution is impossible because `markets.condition_id` is
  null for all 1,455 markets: P0.2 never stores it (the `bands` rows carry the
  condition id and tokens, `markets` does not).
* `weather_outcomes.py` processes 250 city-days per run and the nightly window is
  14 days, so the historical archive (observations back to 18 Mar, forecasts back
  to 18 Mar after the prune, markets back to April) is never verified.

**Fix.** Chunk the venue reads; copy `condition_id` up to `markets` in P0.2 (or
derive it in SQL from `bands`); run `weather_outcomes.py --days 200` with a
higher per-run cap once to backfill the whole archive (the WRH time series
serves historical dates); add the Weather Underground adapter for the four
WU-primary cities (Taipei, Jinan, Jakarta, Lagos) or mark them non-tradeable.

### RC4 — the n8n schedule gate never gates, and P2.2 burns a quarter of the budget

Every live workflow calls `should_run(p_job, p_trigger)` with
`p_trigger: $execution.mode === "trigger" ? "schedule" : $execution.mode`.
n8n's `$execution.mode` expression evaluates to `"production"` or `"test"`,
never `"trigger"`, so scheduled runs send `p_trigger = "production"`, which
`should_run()` treats as an operator override ("manual trigger: production",
run = true). The `ingest_log.detail.trigger` field on every scheduled run reads
`"production"`, which confirms it. Consequences: the Workflows page's
`manual/auto/off` control does nothing; `v_execution_budget` reports 0
runs/month for everything while the instance actually runs ≈2,550 AD4
executions/month (P0.3 720, P2.2 720, P1.2 360, six 6-hourly jobs 720, P0.5 30)
plus the Stratify/Steelwyre workflows on the same instance. P2.2 (paper
maintenance) runs hourly to expire orders that do not exist.

**Fix.** In each "Check schedule" node use
`p_trigger: $execution.mode === "production" && <trigger node is the Schedule Trigger> ? "schedule" : "manual"`
(n8n exposes the trigger node via `$execution.customData`/`$prevNode.name`
from the trigger; simplest: branch the Schedule Trigger through a Set node
that stamps `trigger: "schedule"` before Config). Then set real cadences in
`workflow_schedules` (P0.3 hourly in the 12–18 local-solar window per
city, 3-hourly otherwise; P2.2 every 6 h; P1.5 every 3 h as documented — the
live trigger is 6 h) and let the gate enforce them.

### RC5 — P1.5 writes local wall-clock as UTC and half-overwrites P1.2

`Build rows` in the live P1.5 sets `observed_at: cur.time || now`, where
`cur.time` is Open-Meteo's `current.time` under `timezone=auto`, i.e.
`2026-09-15T15:00` Wellington local, stored as `15:00+00`. It also upserts
`live_weather` with `resolution=merge-duplicates`, so for the 12 US cities
P1.2's `obs_source=NWS`, `running_max_c` and `updated_at` survive underneath
P1.5's `source=open-meteo, source_kind=model` — mixed provenance in one row.
Neither writer sets `minutes_to_peak`, `day_decided` or `peak_window_state`,
and the Action that computed them (`live_weather.py`) is manual-only.

**Fix.** Request `timeformat=unixtime` (or add `utc_offset_seconds`) and write a
true UTC `observed_at`; give model readings their own row/table
(`live_weather_model`) or a `source`-keyed unique index so a station reading is
never overwritten by an interpolation; compute `minutes_to_peak`,
`running_max_c`, `day_decided` in one place (a SQL function called at the end of
P1.2 and P1.5, from `v_trade_timing` / `v_city_temp_trend`).

### RC6 — the database is at the plan limit because of index bloat

| Table | Rows | Heap | Indexes | Note |
|---|---|---|---|---|
| weather_forecasts | 77,345 | 46 MB | **79 MB** | pruned from 330k rows on 14 Sep; indexes never rebuilt |
| trades_observed | 147,170 | 53 MB | 54 MB | never vacuumed; 10 indexes |
| book_snapshots | 87,941 | 62 MB | 7.7 MB | grows ~1,000 rows/h (~25 MB/month) |
| weather_observations | 234,343 | 28 MB | 28 MB | pruned; 5,621 dead tuples |
| research_captures | 27,284 | 22 MB | 9.8 MB | grows with every intraday run once re-enabled |

**Fix.** Run `VACUUM FULL` + `REINDEX` on the four big tables from the SQL
editor (not from a job — it needs a non-transactional session), which should
return the database to roughly 300 MB; drop the four duplicate indexes and
review the 31 unused ones; add `book_snapshots` and `trades_observed` to the
monthly archive job (keep 90 days of raw books hot, archive the rest as
gzipped CSV releases like the observations); put a `v_storage_report` alert in
P4.1 at 420 MB.

### RC7 — a credential-free app with anonymous mutation

The browser key can read 140 relations (including `settings`, which holds the
n8n production webhook URLs, and every strategy/paper table) and execute 19
`SECURITY DEFINER` functions that mutate state. The Paper Trades page goes
through the JWT-verified `paper-desk` Edge Function, which is right; the
Strategies, Workflows, Goals, Campaigns and Backtest pages call
`set_strategy_enabled`, `update_setting`, `set_run_scope`, `upsert_deployment`,
`queue_backtest` directly with the anon key. Whether the Vercel deployment has
password protection could not be verified from this session.

**Fix (in order).** Turn on Vercel deployment protection today; add Supabase
Auth with a single owner account and an `is_desk_owner()` check inside every
mutating RPC (revoke `EXECUTE` from `anon`); move `n8n_webhooks` out of the
browser-readable `settings` row into a server-only table; keep read views
public if the desk is meant to be showable, otherwise put them behind
`authenticated` too. Then address the advisor list mechanically
(`security_invoker = true` on the 63 views after installing the reader grants
they need; `SET search_path` on the 29 functions).

---

## 4. Findings by area

### 4.1 Database (Supabase, 71 tables, 91 views)

**Schema and migration hygiene.** Two parallel histories coexist: the 64
idempotent `sql/ad4_*.sql` files (the install order in `sql/INSTALL_ORDER.txt`,
verified by `tests/test_sql_order.py`) and 42 recorded Supabase migrations, of
which 22 have files in `supabase/migrations/` and 20 (7 Sep) do not. That is
workable but means a fresh environment cannot be rebuilt from either source
alone. Recommendation: freeze the `sql/` series, generate one baseline
migration from the live schema (`supabase db dump --schema public`), and make
`supabase/migrations` the only forward path.

**Data integrity.** The correction layer works as designed: 9,850 corrections
(667 unit, 9,182 half-open label normalisations, 1 symbolic tail) turn 6,157
malformed raw bands and 697 unit-conflicted markets into a clean canonical
layer without touching the raw rows. Open markets have zero unit conflicts. The
quality-flag detector has never run (0 flags) and no integrity manifest exists;
both are one command each (`refresh_data_quality_flags()`,
`scripts/data_integrity.py`).

**Freshness.** `v_data_freshness` reports 26 ok / 3 stale / 6 empty of 35
tracked tables. Stale: `fact_band_outcome` (RC3), `fact_signal_outcome`
(meaningless until real signals exist), `signals` (RC2). Empty: `paper_trades`,
`ledger`, `strategy_conflicts`, `backtest_trades`, `derived_model_forecast`,
`derived_weather_model` (RC1).

**Performance.** The heavy views were cached (`derived_city_climate`,
`derived_city_day_features`, `derived_climb_profile`) and the four busiest
tables indexed (`ad4_44`), which is why the UI loads. Two timeouts remain live:
P0.4's "Refresh volume rollups" RPC (statement timeout on 147k trades; move to
an incremental rollup keyed by `traded_at` watermark) and any read that touches
`weather_forecasts` without `city_key` (79 MB of index for 77k rows; RC6).
The 25 unindexed foreign keys matter only for `signals` and `edges` at scale.

**Security.** See RC7. Additionally: `ad4_view_restore`, `data_freshness_spec`,
`synthesis_thresholds` and 13 other tables have RLS enabled with no policy —
correct for internal tables, but `weather_forecast_features` and
`derived_forecast_skill_model` are read by the UI through views, so keep the
views `security_invoker=false` or add policies before flipping them.

**Size.** See RC6. Retention is otherwise sound: the archive job proved
readback before pruning and the release assets exist
(`forecasts-2024-01-01-to-2026-03-16.csv.gz`,
`observations-2025-07-22-to-2026-03-16.csv.gz`).

### 4.2 n8n (13 AD4 workflows, all on one Cloud instance with 40 others)

| Workflow | Live trigger | Gate call | Writes | Executions since 13 Sep | Verdict |
|---|---|---|---|---|---|
| P0.2 Market Discovery | every 6 h + webhook + manual | yes, ungated (RC4) | markets/bands via RPC | 8 scheduled + 6 manual, all ok (`attention` = missed slugs) | works; add `condition_id`, tick/min-size to `markets`; `days_back` backfill added today |
| P0.3 Book + Volume | **hourly** | yes, ungated | `book_snapshots` direct insert with full `raw_book` ladders | 46+, all ok | works but capped at 1,000 bands (RC1), YES token only, includes yesterday's dead markets (380 failed fetches overnight) |
| P0.4 Trade History | every 6 h | yes, ungated | trades via RPC + rollup RPC | 8, **2 errors** (rollup statement timeout) | make the rollup incremental |
| P0.5 Rules Text | daily 21:00 | yes, ungated | markets.rules_text | 2 ok | runs after discovery of the next day's markets, so 52 of 102 open markets lack rules until the next night; run it after P0.2 instead |
| P1.1 Live Weather Alerts | webhook, **inactive** | — | — | 0 | email off by choice; the template has a node without an id (validator crash, §4.7) |
| P1.2 NWS Monitor | every 2 h | yes, ungated | weather_observations, live_weather, weather_events | 23 ok | works for 12 US cities; does not compute peak timing |
| P1.3 NWS Forecast | every 6 h | yes, ungated | weather_forecasts (model `nws`) | 8 ok | works; "24 partial days skipped" is correct behaviour |
| P1.4 NWS Gridpoint | every 6 h | yes, ungated | weather_forecast_features | 8 ok | works, US only |
| P1.5 Open-Meteo Global | **every 6 h** (settings say 3 h) | yes, ungated | live_weather, weather_forecasts, weather_forecast_features | 8 ok | RC5; the only global forecast/live source, so it deserves 3 h |
| P2.1 Relearn | webhook only | — | dispatches GitHub Actions | 2 errors | no PAT in Config; harmless |
| P2.2 Paper Maintenance | **hourly** | yes, ungated | expire_paper_commands RPC | 46 ok, 0 work | 720 executions/month for nothing; 6-hourly is plenty until a worker exists |
| P3.1 Email Digests | inactive | — | — | 0 | needs SMTP credential + recipient |
| P4.1 Health Watchdog | every 6 h | yes, ungated | ingest_log verdict | 8, "20 anomalies in 24 h" every time | the anomaly rule fires on the desk's own `implausible_edge` rows, so it is red by construction; email off |

Not imported: `P2.2_paper_trades.template.json` (the worker-driven paper cycle).
Credentials: the Supabase credential is bound in every node (no key in Config
— good). The templates in the repository match the live definitions in
structure; the live P1.5 has the documented `on_conflict` fix for forecast
features. Budget: ≈2,550 AD4 executions/month before the Stratify engines,
which also run on this instance (their blueprint runs on 13–14 Sep consumed 14
executions in two bursts, and two were running during this audit).

### 4.3 GitHub Actions (11 workflows, 872 runs)

| Workflow | Cadence | Last 48 h | Finding |
|---|---|---|---|
| Intraday Pipeline | 6×/day | green | model forecast "no fitted model" (RC1); six steps skipped (RC2); probabilities 3 min, edges 40 s |
| Daily Pipeline | 1×/day + manual | #10 failed (capacity timeout), #11 failed (same), #12–#15 green | today's run #15: forecasts ingest 22 min, outcomes 250 targets all `unsupported_source` (the backlog is now the four WU cities), skill 1 city, databank +204 forecast facts / 0 band facts, calibration refused, derived ok, backtest sweep 0 |
| Forecasts (Open-Meteo previous runs) | nightly 03:10 | #25 failed | Open-Meteo previous-runs API read timeouts (4 of 28 cities), 20-minute soft deadline stops at city 29/54; the wrapper fails the job on any missed chunk even though 1,848 rows were written. Make a partial night a success with a resumable list, and raise the per-city timeout |
| Observations (IEM) | 4×/day | green, `partial` | ZSJN is not an IEM station → every run "partial"; map Jinan to its IEM-served station or drop it from the IEM roster |
| Weather Model | weekly | green, 0 fits | RC1 |
| Archive Observations | monthly + manual | #6/#9 green, #7/#8 failed then fixed | works; add books/trades |
| Backtest | on demand + daily sweep | green, 0 trades | vacuous until pricing is eligible; no claim/lease, so a manual dispatch and the daily sweep can double-run |
| Live Weather Monitor | manual only | — | superseded by P1.2/P1.5; keep as fallback |
| Verify Resolution Source | manual | — | superseded by `weather_outcomes.py` evidence; the legacy `settlement_verified` gate can be retired |
| Tests / Web build | PRs + main | green | 796 tests in 9 s; web build 40 s |

Cost: the measured 770 scheduled minutes/month plus CI is within the 2,000
private-repo allowance; the one real risk (a multi-hour backfill) is bounded at
25 minutes per link. Every job caps at ≤120 minutes (`test_no_single_job_can_burn_a_fifth_of_the_month`).

### 4.4 Python engines (delegated audit, verified against live data where possible)

The full per-module table and 25 ranked defects from the engine audit are
reproduced in Appendix B. The ones that decide whether a paper trade can ever
happen:

1. `signal_engine.py:167` — `signal_id = str(uuid4())` into a `bigserial` column: the first real signal insert will fail with 22P02. Drop the key.
2. `signal_engine.py:165-178` / `paper_plans.py:29` — the payload no longer carries `decision_at`, `decision_inputs`, `cycle_id`; every proposal would be `blocked` on a KeyError. Restore the enrichment.
3. `signal_engine.py:109-134` — the strategy context never fills `minutes_to_peak`, `slope_*`, `implied_max_*`, `forecast_max_c`, `mae_bands`, `lead_days`, `s5_allowed`; S3, S5, S7, S8, S9 and S1-time cannot fire. Join `v_trade_timing`, `v_city_peak_approach`, the verified skill row and the priced forecast.
4. `measure_skill.py:101-117` — RC1.
5. `weather_model.py:652-656` — RC1.
6. `databank.py:214-227` — the URL-too-long venue read (RC3); `:237-240` capped bands; `:260-263` reads `v_opportunities`, which only holds *open* markets, so every frozen band fact has a null market price and the "did being right pay" views can never fill; `:281` stores the calibrated probability as `model_prob`, which `calibration.py:130` then fits on (recursive calibration) — fit on `raw_prob`.
7. `paper_plans.py:62,80-82` — a plan requires a full fill at the best ask; on these books most plans will block. Walk the ladder to `max_slippage` and set the limit at the last level consumed.
8. `strategy_rules.py:131` + `signal_engine.py:203` — `portfolio=None` → `suggested_shares = 0` for every strategy except S6. Build the portfolio from `paper_accounts.cash`.
9. `regime.py:209` — shortest-lead pick without a newest-run tiebreak; archive rows carry a synthetic midnight `run_at`, so a city can be marked `stale_forecast` and BLOCKED with confidence 0 while a fresh run exists.
10. `paper_exits.py:35` — an automatic exit posts at the *lowest* bid, i.e. sells through the whole ladder.
11. No dedupe/TTL in `signal_engine.main` — identical signals every 4 h.
12. Rule drift: `v_trade_plan.would_fire` (SQL), the Python strategies and `web/lib/cover.ts` implement S1/S3/S4/S5/S7/S8 three times with different gates (S1's SQL adds a $200 depth floor; S7's SQL uses the latest reading where Python uses the running max; the browser cover omits the anchor and liquidity gates). One implementation (Python) should emit a `trade_plans` row and SQL/UI should only project it (plan item C20).
13. Band containment: the probability lattice rounds to half-steps while settlement and every `_contains` compare raw values against `[lo, hi)` (plan item C03); this decides who wins at the boundary.

### 4.5 Web application (Next.js 14, 18 routes, 3 API routes)

Build and typecheck are clean; every page follows the loading/error/empty
contract and names the upstream job when a panel is empty, which is unusual and
valuable. Under today's data:

| Route | Reads | Actions (auth) | State today |
|---|---|---|---|
| `/` Overview, `/board` | v_opportunities, v_latest_book, live_weather, weather_forecasts, cities, v_city_volume | none | populated; "tradeable" is empty for 53 cities (RC1); future timestamps render for Asia/Pacific cities (RC5) |
| `/opportunities` | v_trade_plan, v_city_day_plan, v_opportunity_context | none | every row's first blocker is `no_verified_skill`; the page should say so in words, today it shows struck-through strategies |
| `/paper-trades` | Edge Function `paper-desk` (JWT) → single-desk RPCs; `/api/paper-cycle` | manual ticket, approve/reject, exits (Edge JWT) | a manual order queues and never fills (no worker); the page says "order remains queued", which is honest but the desk is unusable |
| `/predictive` | v_prediction_ladder, v_prediction_scorecard_all, v_forecast_convergence_all, v_edge_scaling, v_bankroll_curve | none | forward ladder shows cold-start σ = 8 °C for 693 bands — a visibly wrong number that the page should label as a placeholder |
| `/strategies` | v_strategy_board, v_trade_plan | `set_strategy_enabled` (**anon**) | all nine "on, but nothing has met its conditions in 30 days" — true, but for the wrong reason (RC2) |
| `/clusters`, `/globe` | v_city_stats, v_opportunities, v_peak_hour_coverage | none | fine |
| `/live`, `/monitor` | live_weather, v_city_peak_approach, v_city_today_readings, v_band_price_history | none | timing panels empty for all 54 cities (`minutes_to_peak` null); future readings shown as "in 6 h" |
| `/analytics` | v_strategy_board, derived_forecast_skill, band_probabilities, paper_trades | none | reads legacy `paper_trades` (empty) not the paper desk; skill chart shows one city |
| `/databank`, `/synthesis` | v_archive_*, v_synthesis_*, v_learning_state, v_outcome_evidence_health, v_calibration, v_edge_realisation | none | honest: "desk vs market: collecting, n=0" |
| `/backtest` | backtest_runs/results, v_backtest_window | `queue_backtest`, `backtest_readiness` (**anon**) | three complete runs with 0 trades and empty results |
| `/campaigns`, `/goals` | v_campaign_state, v_trade_plan, v_trade_timing, settings | `upsert_deployment`, `set_deployment_status`, `update_setting` (**anon**) | Goals recomputes spreads client-side from `lib/spread.ts` (a third implementation of fills and fees) |
| `/workflows` | settings (`workflow_schedules`, `n8n_webhooks`), v_workflow_runs, v_execution_budget | Run buttons POST to n8n production webhooks from the browser; `update_setting`, `set_run_scope` (**anon**) | the cadence control is decorative (RC4); the budget panel shows 0 runs/month while the instance runs ~2,550 |
| `/docs` | — | — | fine |

Cross-cutting: three fill/fee implementations (`paper_execution.py`,
`web/lib/execution.ts`, `web/lib/spread.ts`) and two S8 implementations; the
browser should display the engine's plan, not recompute it. `lib/time.ts`
formats in the viewer's chosen zone and would show the RC5 rows as future
times rather than flag them.

### 4.6 Paper trading stack

Designed well (transactional RPCs, leases, command keys, append-only
activity, venue-identity checks, a database contract suite in PGlite) and
never exercised: 3 accounts, 0 orders. Three things are missing for the first
paper fill: the worker host (`Dockerfile.paper-worker` +
`PAPER_WORKER_URL/TOKEN` in Vercel), the `condition_id` on markets (venue
settlement), and the engine fixes in §4.4 items 1–3 and 7–8. Until a worker
exists, an *automatic* account can still fill inside the 4-hourly Actions
sweep (proposal → settlement → exits → recovery run in one job); an *assisted*
approval expires after 5 minutes and will always miss the sweep.

### 4.7 Tests, docs, tooling

* `scripts/validate_n8n_workflows.py` crashes (`KeyError: 'id'`) on
  `P1.1_live_weather_alerts.template.json` (one node without an id) and
  `P2.2_paper_maintenance.template.json` (eleven). `paper_trades_rollout.md`
  claims the 14-workflow validator passes; it does not on `main`.
* `docs/strategies.md` and `docs/EVERYTHING.md` say all strategies ship
  disabled; the database has all nine enabled.
* `docs/compute_budget.md` says P1.5 runs every 3 h; the live trigger is 6 h.
* `docs/n8n_workflows.md`'s execution budget (1,770/month) omits P2.2 (720).
* The 8 Sep improvement plan (`docs/ArbDesk4_Improvement_Plan.md`) remains the
  right architecture document; this audit changes its ordering, not its
  direction: C01 (pagination) turned out to be the single most consequential
  item on its list.

---

## 5. Finalization plan

The ordering principle: **each phase must produce evidence the next phase
needs.** No modelling work starts until the desk prices every city; no strategy
is trusted until its paper record exists; no "proprietary" claim is made until
the data behind it is versioned and the algorithm beats a named baseline on
held-out days.

Owner key: **H** = Hassan (accounts, secrets, hosting, approvals); **D** = the
development session (code, SQL, n8n definitions, tests). Effort is engineering
time, not calendar time.

### Phase 0 — Stop the bleeding (days 1–3)

| # | Task | Owner | Effort | Acceptance |
|---|---|---|---|---|
| 0.1 | RC1: move every capped read to `rest_all` (`measure_skill`, `weather_model` ×4, `databank` ×8, `calibration`, `signal_engine`, `probability_engine`, `backtest/runner`); add the repo-wide guard test | D | 0.5 d | daily run writes skill rows for ≥45 cities at leads 1–7; `weather_model.py` dispatched manually fits ≥40 cities; `bank_bands` freezes >0 bands |
| 0.2 | RC3: chunk the venue reads by 100 market ids; copy `condition_id`, `min_tick_size`, `min_order_size` into `markets` from P0.2 | D | 0.5 d | `v_outcome_evidence_health.confirmed_markets > 0` after the next daily run |
| 0.3 | RC6: `VACUUM FULL` + `REINDEX` on `weather_forecasts`, `trades_observed`, `book_snapshots`, `weather_observations`; drop the 4 duplicate indexes | H (SQL editor, ~10 min, Appendix A.5) | 0.1 d | database ≤ 350 MB in `v_storage_report` |
| 0.4 | RC7 (first step): Vercel deployment protection on; rotate nothing yet | H | 0.1 d | deployed URL asks for a password |
| 0.5 | Engine defects 1, 2, 8, 11 (§4.4): serial `signal_id`, restored decision payload, real portfolio sizing, dedupe/TTL | D | 1 d | `PYTHONPATH=scripts python scripts/signal_engine.py` against the live DB writes rows or logs per-strategy block reasons |
| 0.6 | RC2: set `PAPER_TRADES_ENABLED=true` once 0.1 and 0.5 are merged | H | — | intraday run #N shows steps 8–13 executed; `signals` gains `strategy_id <> 'system'` rows within 24 h |
| 0.7 | RC4: fix the `p_trigger` expression in all 13 workflows; P2.2 → 6 h; P1.5 → 3 h; P0.3 paginate + today-only filter; chain P0.5 after P0.2 | D (publish via MCP after H approves) | 0.5 d | `ingest_log.detail.trigger = 'schedule'` on scheduled runs; `v_execution_budget` matches reality; P0.3 `requested` equals the live band count |
| 0.8 | RC5: P1.5 `timeformat=unixtime`; model readings to their own rows; one SQL function computes `running_max_c`, `minutes_to_peak`, `day_decided` after every weather write | D | 1 d | no `live_weather.observed_at > now()`; `minutes_to_peak` non-null for every city inside its daylight window |
| 0.9 | Fix the validator crash (node ids), correct the four stale doc statements (§4.7) | D | 0.2 d | `python scripts/validate_n8n_workflows.py` exits 0 |

Exit criterion for Phase 0: **≥ 40 cities pricing-eligible on the board, a
strategy signal row in `signals`, database under the plan limit, the deployment
behind a password.**

### Phase 1 — Prove the loop on paper (week 1–2)

| # | Task | Owner | Effort | Acceptance |
|---|---|---|---|---|
| 1.1 | Deploy the paper worker (`Dockerfile.paper-worker`) on Fly.io / Render / Cloud Run, HTTPS, `PAPER_WORKER_TOKEN`; set `PAPER_WORKER_URL/TOKEN` in Vercel; import `P2.2_paper_trades` into n8n bound to the two header-auth credentials | H (accounts) + D (config) | 0.5 d | manual ticket fills within 60 s; assisted approval fills before its 5-minute expiry |
| 1.2 | Venue-confirmed settlement end to end: with `condition_id` present, `paper_settlement.py` pays a settled paper position; add the attempts table so void/split markets are not re-asked every run | D | 1 d | one real paper position settles with matching Gamma/CLOB identity and a ledger delta |
| 1.3 | Evidence backfill: `weather_outcomes.py` over the full archive (24 Aug is the market floor; observations reach 18 Mar) at 1,000 city-days/run until `backlog_done = 0`; Weather Underground adapter or explicit exclusion for Taipei, Jinan, Jakarta, Lagos; DC precision rule | D | 1 d | ≥ 40 verified days per city; skill `n_days ≥ 40` at lead 1 |
| 1.4 | NO-side books: fetch `token_no` in P0.3 (both tokens per band, 2 requests) and store side-tagged snapshots; make the edge engine use the real NO ladder | D | 0.5 d | `v_latest_book` has both sides; NO edges no longer synthetic |
| 1.5 | Alerts lifecycle: `system` signals move to `anomalies`/an `incidents` table with acknowledge/cooldown; dismiss the 1,893 pending alerts in one migration | D | 0.5 d | Signals drawer shows only trade proposals |
| 1.6 | Proposal realism: plans walk the ladder to `max_slippage` and set the limit at the last level consumed (defect 7); exits use the executable bid path; per-strategy sizing from `capital_cap_pct`/`max_concurrent` | D | 1 d | ≥ 1 assisted proposal per day on real books; no exit sells through the ladder |
| 1.7 | Truthful ops: P4.1 rules keyed to `v_operational_health`/`v_execution_health` and storage, not to the desk's own anomaly rows; n8n execution counter; Actions failure → incident row | D | 0.5 d | watchdog is green on a healthy day |
| 1.8 | RC7 (second step): Supabase Auth owner login; `is_desk_owner()` inside every mutating RPC; revoke `EXECUTE` from `anon`; move `n8n_webhooks` server-side | D | 1.5 d | anon key cannot mutate; advisor list shrinks to reads only |

Exit criterion: **a manual, an assisted and an automatic paper trade have each
gone decision → plan → order → fill → settlement with a reconciled cash ledger,
on real books, with every timestamp in the activity log.**

### Phase 2 — Make it learn (week 3–4)

| # | Task | Acceptance |
|---|---|---|
| 2.1 | Calibration on `raw_prob` (not the calibrated value); Platt first, isotonic once n ≥ 1,000; per-lead maps | `v_calibration` populated; reliability diagram slope within 0.9–1.1 on held-out weeks |
| 2.2 | Backtest harness: atomic claim, per-band as-of books, the *same* width rule and tradeability as the live engine (engine audit items 17–19), walk-forward by week | a run over 24 Aug → today produces trades and a net P&L per strategy with confidence intervals |
| 2.3 | Scorecards: Brier and log score of the desk vs the market price per city/lead; CRPS of the temperature distribution; edge realisation (did a 10-point edge pay 10 points net of fees) | `/predictive` and `/databank` show "desk vs market" with n ≥ 60 |
| 2.4 | Strategy promotion rule: a strategy may stay enabled only with ≥ 30 settled paper trades and net P&L > 0 at the 80% interval; otherwise it runs in shadow (signals logged, no plans) | Strategies page shows shadow/live with the evidence behind each |
| 2.5 | Model promotion gate: the fitted city model replaces the raw public forecast only where it beats persistence *and* the raw forecast on held-out days | `derived_weather_model.beats_persistence` decides `derived_model_forecast` use |

### Phase 3 — Superior data (weeks 3–6, in parallel)

See §6 for the rationale. Deliverables: ensemble member capture (P1.6),
peak-window book capture every 10–15 minutes for the 12–18 local-solar window
of each city (P0.3-fast, worker-driven), both-side ladders, full trade tape with
provider cursors, contract-rule versions per market, station observations at
full resolution (already), and the canonical **city-day record** dataset
assembled nightly (§6.3).

### Phase 4 — Superior algorithms (weeks 5–10)

See §7. Deliverables in order: (1) EMOS/NGR post-processing on ensemble
members per city; (2) the intraday nowcast — the conditional distribution of
the daily maximum given the readings up to hour *t*; (3) calibrated pricing
with capacity-aware sizing; (4) an execution model fitted on the book archive;
(5) portfolio construction on the forecast-error correlation matrix; (6) a
strategy research harness that replays any day from the city-day record.

### Phase 5 — Proprietary hardening (ongoing from week 2)

Owner authentication everywhere; a single-source migration history; nightly
integrity manifests; data quality flags in the pipeline; export/restore
rehearsal of the archive releases; a runbook; cost dashboards (n8n executions,
Actions minutes, database size) on the Workflows page; a private, versioned
model registry (`model_versions` already exists — use it for every fitted
artefact with its input manifest hash).

---

## 6. Proprietary data strategy

### 6.1 What is already a moat

| Asset | Size today | Why it is hard to replicate |
|---|---|---|
| Hourly order books with full ladders for every band, 54 cities | 87,941 snapshots, ~1,000/hour | Polymarket does not serve history; nobody can reconstruct the 21:00 book of a resolved market later |
| Trade tape per band | 147,170 trades | same |
| Station observation series (IEM + NWS), including SPECI | 234,343 rows live + 2 archived releases | public but only as a rolling window; the settled *series* per market-day is what settlement and nowcasting need |
| Forecast archive at issue time, three models, leads 0–7 | 77k live + archived | previous-runs archives exist, but the *as-issued* forecast paired with the *as-traded* book does not |
| Verified settlement evidence with payload hashes | 748 city-days | this is the ground truth every claim is scored against |
| Corrections/provenance layer | 9,850 corrections, immutable | shows the desk knows which raw rows are wrong and why |

### 6.2 What to add, in priority order

1. **Ensemble members (P1.6).** Open-Meteo's ensemble API serves ECMWF IFS 0.25°
   (51 members), GFS 0.25° (31), ICON seamless (40) for free, but keeps only
   ~3 days of history — exactly why `ensemble_forecasts` exists and is empty.
   One request per city per run, daily max per member per lead, stored as
   `(city, model, member, run_at, for_date, max_c)`. This is the single most
   valuable addition: ensemble spread is a *forecast of uncertainty*, whereas
   the current σ is a climatological average error.
2. **Peak-window books at 10–15 minute resolution.** Hourly is fine for
   research; the trade happens in the last two hours before the peak. Capture
   only the ~6 cities inside their 12–18 local-solar window at any moment (the
   Globe already computes this band), both tokens. Cost: ~30 requests/minute,
   run from the worker, not n8n (no per-execution charge).
3. **Both-side ladders** (1.4) and **venue metadata** per market (tick, min
   size, fee schedule, `condition_id`).
4. **Contract-rule versions** (`market_rule_versions`): raw text, hash,
   parsed source/unit/precision/fallback, capture time. Rules changed on
   5 Sep for Cape Town; the desk must know which version a decision saw.
5. **Observation series at the settlement station for all 54 cities** through
   the WRH time series (already the settlement source; 48 cities), with the
   NWS API adding sub-hourly SPECI for the US 12.
6. **Historical backfill:** forecasts (previous-runs API, already scheduled),
   verified outcomes for the whole observation archive (1.3), and — the one
   thing that cannot be backfilled — books and ensembles from today onwards.

### 6.3 The canonical city-day record

Assemble nightly, one row per (city, resolution date), immutable once the day
settles, versioned by `record_version`:

* identity: market id, condition id, rule version, unit, precision, station;
* forecasts: per model and lead, the value as issued (`issued_at`) and its
  ensemble mean/spread where available;
* the day: climatology, morning features (P1.4/P1.5), the observation series,
  running-max path, peak hour, trend slopes at each hour;
* the market: book at each capture (both sides), trade tape summary per hour,
  implied probability path per band;
* decisions: every probability row, edge row, signal, plan, order, fill;
* truth: verified maximum, winning band, venue resolution, payout.

This table is the product. Every backtest, every scorecard and every model fit
reads from it; nothing else has to join twelve tables at research time. Export
it monthly as a release asset with a manifest hash (the integrity script
already computes these).

### 6.4 Retention and cost

Keep 180 days of raw observations and forecasts hot (already), 90 days of raw
books and trades hot (new), the city-day record forever (small: ~54 rows/day ×
~5 KB). At those windows the database stays near 300 MB on the free plan; if
the peak-window capture is added, plan for the $25 Pro tier (8 GB) rather than
fighting the ceiling.

---

## 7. Algorithm roadmap

Each step names its baseline and the number that decides whether it ships.

### 7.1 Width from ensembles (replaces the MAE-Normal)

Today σ = 1.2533 × MAE × multipliers, i.e. the same width every day for a
city and lead. With members: fit per city and lead the EMOS/NGR model
μ = a + b·ens_mean, σ² = c + d·ens_var (four parameters, ~60 days of data),
then score CRPS on held-out days against (i) the raw ensemble, (ii) the current
MAE-Normal. Ship where CRPS improves by ≥ 5%. This is standard post-processing
(Gneiting et al.) and the biggest single gain available: sharp days get sharp
distributions.

### 7.2 The intraday nowcast (the real edge)

The market reprices slowly during the day while the observation series reveals
the outcome. Model the conditional distribution of the daily maximum given the
state at local hour *t*: features = running max, latest reading, 1 h and 3 h
slopes, hours to climatological peak, the morning forecast, ensemble spread,
cloud/wind/dew-point depression, day-of-year; target = final maximum. Fit
per-hour quantile regressions (or one gradient-boosted quantile model with hour
as a feature) on the 21,939 city-days already cached, evaluated by pinball loss
against the "forecast + climatological climb" rule S7 uses today. Ship when it
beats that rule at every hour from 10:00 local onward. The output feeds S5/S7
directly and gives every band a live probability path instead of a morning
snapshot.

### 7.3 Calibration and pricing

Calibrate raw model probabilities per lead (Platt → isotonic as n grows);
price = calibrated probability; edge = price − executable price − fees; size =
fractional Kelly (¼) capped by the capacity curve at the chosen slippage and by
the account policy. Evaluate: Brier skill score vs the market, and realised edge
per unit of quoted edge (should be ≈ 1; today unknowable).

### 7.4 Execution model

From the book archive: fill probability and expected slippage as a function of
order size, band state (`market_state`), time to peak and hour of day; the
IOC-at-best-ask rule becomes "walk to the size where expected slippage equals a
third of the edge". Evaluate on the paper record: simulated vs realised fill
price distribution.

### 7.5 Portfolio

`derived_city_correlation` (forecast-error correlation) already exists; use it
to cap the risk budget per correlated cluster (ten European positions under one
ridge are one position) and to size baskets. Evaluate: drawdown of the
correlation-aware book vs the naïve one in walk-forward backtests.

### 7.6 Strategy research harness

Replay any city-day from the record with the exact books at each capture; run
all nine strategies plus candidates; report net P&L, hit rate, fill rate and
turnover with bootstrap intervals; promote per §5 Phase 2.4. S7 and S8 (the
manual trades written as rules) get their first honest measurement here.

---

## 8. Cadence and budgets after the plan

| Job | Where | Cadence | Monthly starts |
|---|---|---|---|
| P0.2 discovery (+ P0.5 chained) | n8n | 6 h | 120 |
| P0.3 books, both sides, paginated | n8n | hourly | 720 |
| Peak-window books (12–18 local solar, ≤ 6 cities at once) | worker | 10–15 min | 0 n8n executions |
| P0.4 trades | n8n | 6 h | 120 |
| P1.2 NWS observations | n8n | 2 h | 360 |
| P1.3 / P1.4 NWS forecast + gridpoint | n8n | 6 h | 240 |
| P1.5 Open-Meteo global | n8n | 3 h | 240 |
| P1.6 ensembles (new) | n8n or Actions | 12 h | 60 |
| P2.2 paper maintenance | n8n | 6 h | 120 |
| P4.1 watchdog | n8n | 6 h | 120 |
| Intraday pipeline | Actions | 4 h | 180 |
| Daily pipeline, forecasts archive, observations | Actions | as now | 180 |
| **n8n total** | | | **≈ 2,100** (+ Stratify) |

The n8n number only holds if the schedule gate works (0.7). If the instance is
on the Starter plan (2,500/month) the Stratify engines and manual runs leave no
headroom; measure the plan's allowance before adding P1.6 to n8n — Actions has
no per-run quota and is the better home for a twice-daily fetch.

---

## 9. Definition of "fully running"

The platform is running when all of the following are true on the same day and
have been true for seven consecutive days:

1. `v_operational_health.state = 'ready'` for ≥ 45 cities; no `live_weather`
   row in the future; `minutes_to_peak` populated inside daylight.
2. ≥ 45 cities pricing-eligible; `blocked_probabilities < 10%` of bands.
3. Every enabled strategy has fired or logged a reason; ≥ 1 paper proposal per
   day; assisted approvals fill within 60 s; automatic accounts trade inside
   policy; every fill has book evidence; settlements pay against venue truth.
4. `fact_band_outcome` grows every day; calibration fitted; `desk_vs_market`
   established (n ≥ 60); a backtest over the last 30 days reproduces the paper
   record's fills.
5. The weather model is fitted for ≥ 40 cities and shipped only where it beats
   persistence and the raw forecast; ensemble members captured daily.
6. Database < 80% of plan; n8n and Actions within budget with the counts on the
   Workflows page matching the providers' dashboards.
7. No anonymous mutation; owner login; secrets only in server environments;
   migration history single-sourced; validator and 796+ tests green in CI.
8. The city-day record is assembled nightly and exported monthly with a manifest
   hash.

---

## Appendix A — exact patches for Phase 0

### A.1 `scripts/measure_skill.py` — read the whole scope (RC1)

```python
from common import rest_all, upsert, log_run, get_cities

def daily_max_observed(city_key, start, end, timezone):
    rows = rest_all("v_verified_weather_outcomes", [
        ("select", "for_date,observed_max_c"),
        ("city_key", f"eq.{city_key}"),
        ("for_date", f"gte.{start.isoformat()}"),
        ("for_date", f"lte.{end.isoformat()}"),
    ], order="for_date.asc", page_size=1000)
    return {str(r["for_date"]): r["observed_max_c"]
            for r in rows if r.get("observed_max_c") is not None}

def forecasts(city_key, start, end):
    # Bound the read to the days that can be scored: everything else is
    # archive the join would discard anyway.
    return rest_all("weather_forecasts", [
        ("select", "for_date,lead_days,forecast_max_c,model,run_at"),
        ("city_key", f"eq.{city_key}"),
        ("for_date", f"gte.{start.isoformat()}"),
        ("for_date", f"lte.{end.isoformat()}"),
    ], order="for_date.asc,lead_days.asc,model.asc,run_at.asc", page_size=1000)
```

and in `main()` pass `start = max(start, first_verified_date - 1 day)` so a
400-day window does not page 1,400 rows per city for nothing.

Guard test (generalises `tests/test_databank_outcome_truth.py`):

```python
import re, pathlib
BARE = re.compile(r'rest\((?!_all)[^)]*?\("limit",\s*"(\d+)"\)', re.S)
def test_no_bare_read_asks_for_more_than_the_server_cap():
    for p in pathlib.Path("scripts").rglob("*.py"):
        for m in BARE.finditer(p.read_text()):
            assert int(m.group(1)) <= 1000, f"{p}: limit {m.group(1)} > PostgREST cap; use rest_all"
```

### A.2 `scripts/weather_model.py` — fit on every cached day (RC1)

```python
rows = rest_all("v_city_day_features", [
    ("select", "city_key,obs_date,max_c,n_obs,prev_max_c,morning_temp_c,"
               "dewpoint_depression_c,cloud_mean,wind_mean,precip_total"),
], order="city_key.asc,obs_date.asc", page_size=1000)
```

Same change for `predict_forward` (`v_forecast_features`, order
`city_key.asc,for_date.asc,run_at.asc`), `load_fits` (`derived_weather_model`,
order `city_key.asc`) and `recent_days` (`v_city_day_features`, order
`city_key.asc,obs_date.asc`).

### A.3 `scripts/databank.py` — venue reads by chunk (RC3)

```python
market_resolution, band_resolution = {}, {}
ids = list(by_market)
for i in range(0, len(ids), 100):
    chunk = ",".join(ids[i:i + 100])
    for r in rest_all("v_venue_market_resolution",
                      [("select", "market_id,resolution_state"), ("market_id", f"in.({chunk})")],
                      order="market_id.asc", page_size=1000):
        market_resolution[r["market_id"]] = r["resolution_state"]
    for r in rest_all("v_venue_band_resolution",
                      [("select", "band_id,market_id,settled_yes,resolution_state,confirmed_at"),
                       ("market_id", f"in.({chunk})")],
                      order="band_id.asc", page_size=1000):
        band_resolution[r["band_id"]] = r
```

Also: read prices from `v_latest_edge`/`v_latest_book` (not `v_opportunities`,
which excludes settled markets) and freeze `raw_prob` as `model_prob`.

### A.4 n8n — the gate and the timestamp

"Check schedule" body, every workflow:

```
={{ JSON.stringify({ p_job: "P0.3_book_volume_snapshot",
     p_trigger: $('Schedule Trigger').isExecuted ? "schedule"
              : ($('Webhook Trigger').isExecuted ? "webhook" : "manual") }) }}
```

(where the schedule node is named differently — `Schedule (hourly)`,
`Schedule (every 6h)` — use that name). If `isExecuted` is unavailable on the
instance's n8n version, add a Set node after the Schedule Trigger that stamps
`trigger = "schedule"` and read it from Config.

P1.5 "Build rows", the live reading:

```js
const offs = Number(r.utc_offset_seconds || 0);           // Open-Meteo returns it per location
const localIso = cur.time ? `${cur.time}:00` : null;       // "2026-09-15T15:00" local wall clock
const observedAt = localIso
  ? new Date(Date.parse(localIso + 'Z') - offs * 1000).toISOString()
  : now;
live.push({ ..., observed_at: observedAt, ... });
```

and write model readings with `source_kind = 'model'` to their own row
(`on_conflict=city_key,source`) once `live_weather` carries a
`(city_key, source)` unique index, so P1.2's station row is never merged over.

P0.3 "Load live bands": filter
`markets.resolution_date=gte.{{ $now.toFormat('yyyy-MM-dd') }}` and loop with
`offset` until a page shorter than 1,000 comes back (or set `Range` headers).

### A.5 SQL editor — reclaim the space (RC6)

Run between P0.3 cycles (it takes an exclusive lock for a few minutes):

```sql
vacuum (full, analyze) public.weather_forecasts;
vacuum (full, analyze) public.trades_observed;
vacuum (full, analyze) public.book_snapshots;
vacuum (full, analyze) public.weather_observations;
drop index if exists backtest_runs_run_id_key, deployments_deployment_id_key,
                     paper_trades_trade_id_key, signals_signal_id_key;
select * from v_storage_report order by total_bytes desc limit 8;
```

### A.6 `scripts/signal_engine.py` — the two inserts that cannot land

* remove `"signal_id": str(uuid4())` from the row (the column is `bigserial`);
* build the payload as the deleted `signals.py` did: `decision_at`,
  `cycle_id`, `decision_inputs = {band_id: asdict(view)}` plus the market,
  forecast and edge evidence, so `paper_plans.py` can read
  `payload["decision_at"]`.

---

## Appendix B — engine audit, per module

Line references are to `scripts/`. "Capped" = a bare `rest()` with a limit above the 1,000-row server cap.

| Module | Reads | Writes | Gate | Status | Defects |
|---|---|---|---|---|---|
| common.py | — | ingest_log | — | ok | `rpc()` has no retry (227-241); `log_run` swallows failures |
| probability_engine.py | v_canonical_markets, v_canonical_bands (rest_all), weather_forecasts newest run, derived_forecast_skill(_model) verified-then-any, calibration adjustment, v_forecast_divergence (capped) | band_probabilities | none | runs | verified-skill block (§3 RC1); divergence read capped |
| edge_engine.py | v_canonical_*, v_latest_prob per 100 bands, v_latest_book | edges, anomalies | none | runs | NO side synthesised from YES bids; book age 2 h, prob age 12 h |
| regime.py | weather_forecasts, 120-day history, derived_weather_peak, book_snapshots | — | — | latent | shortest-lead tie → synthetic-midnight run can read as stale (item 9) |
| signal_engine.py | strategies, v_opportunities (rest_all), live_weather, capacity, correlation (capped), paper_positions | signals, strategy_conflicts | `PAPER_TRADES_ENABLED` | never run | items 1, 2, 3, 8, 11 |
| strategies/s1–s9, strategy_rules, conflicts | context | — | regime filters | S1/S2/S4/S6 reachable; S3/S5/S7/S8/S9 not | timing/skill fields never populated; capital caps unused |
| paper_plans.py | accounts, signals (15-min window), plans, CLOB/Gamma | paper_book_evidence, publish_paper_plan | `PAPER_TRADES_ENABLED` | dead upstream | `payload['decision_at']` KeyError; full-fill-at-best-ask rule |
| paper_execution.py | pure | — | — | ok | Decimal, per-level fee, IOC |
| paper_exits.py | accounts (automatic, auto_exit), positions, orders, CLOB | queue_automatic_paper_exit | policy | dead | exit limit = lowest bid |
| paper_settlement.py | positions, evidence, markets, Gamma + CLOB | paper_resolution_evidence, settle_paper_inventory | none | runs, finds nothing (no condition ids) | no attempts table for void/split |
| paper_worker(_server).py | claim_paper_order, CLOB | complete_paper_order | worker host / sweep | sweep only | rejects markets whose fee exponent ≠ 1 |
| measure_skill.py | verified outcomes, weather_forecasts (capped) | derived_forecast_skill(_model) | ≥ 10 pairs | 1 city | RC1 |
| weather_outcomes.py | markets rules, evidence | evidence, attempts | 250 city-days/run | works, slow backfill | WU adapter missing |
| databank.py | outcomes, forecasts (rest_all), markets, venue views (URL 400), bands (capped), probabilities, v_opportunities, signals, paper_trades | fact_* | venue truth for bands | forecasts ok, bands frozen | items 6 |
| calibration.py | v_verified_fact_band_outcome (capped) | calibration_map, model_versions | ≥ 300 | refuses | fits on calibrated prob |
| capacity.py | cities, RPCs | derived_* | — | ok | `recompute_correlation` unguarded |
| weather_model.py | v_city_day_features (capped), v_forecast_features | derived_weather_model, derived_model_forecast | ≥ 120 days, 6 features | no fit | RC1 |
| ingest_observations / ingest_forecasts / forecast_backfill_job | IEM, Open-Meteo previous runs | weather_observations, weather_forecasts | chain budget | partial / nightly incomplete | ZSJN; one missed chunk fails the night |
| live_weather.py | IEM 3 h window | live_weather, weather_events | manual only | not scheduled | UTC day; `peak_window_state=None` hard-coded |
| archive_observations.py / data_integrity.py / research_capture.py | keyset paging | releases, manifests, captures | readback count | ok | — |
| backtest/* | markets, outcomes, skill (verified, as-of), book_snapshots (capped) | backtest_results/trades/runs | queued row | 0 trades | no lease; sigma/tradeability differ from live |

Ranked defect list (impact on producing a real paper trade): see §4.4 items
1–13; the remaining twelve from the audit — `regime` tie-break, `rpc()`
retries, `forecast_backfill_job` partial-night failure, `capacity`
correlation guard, backtest lease and as-of books, `live_weather` local day,
`cover.ts` parity, `execution.ts` per-level fees, P0.3 NO tokens, S7 SQL/Python
drift, `paper_settlement` attempts, `calibration` input column — are folded
into Phases 0–2 above.

---

## Appendix C — how to re-check the headline numbers

```sql
-- pricing eligibility (RC1)
select pricing_block_reason, skill_source, pricing_eligible, count(*)
from v_latest_prob group by 1,2,3 order by 4 desc;

-- skill coverage per run
select computed_at, count(*) rows, count(distinct city_key) cities
from derived_forecast_skill where computed_at > now() - interval '3 days' group by 1 order by 1 desc;

-- what the first 1,000 forecast rows of a city reach (the cap)
select max(for_date) from (select for_date from weather_forecasts
  where city_key = 'amsterdam' and for_date >= '2025-08-11' order by for_date limit 1000) x;

-- live weather in the future (RC5)
select count(*) from live_weather where observed_at > now();

-- schedule gate reality (RC4)
select job, detail->>'trigger' trig, count(*) from ingest_log
 where logged_at > now() - interval '2 days' group by 1,2 order by 1;

-- size (RC6)
select * from v_storage_report order by total_bytes desc limit 8;

-- evidence chain
select * from v_outcome_evidence_health;
select * from v_weather_resolution_collection_health;
select * from v_execution_health;
```

GitHub: Actions → Intraday Pipeline → latest run → steps 8–13 (`skipped` until
`PAPER_TRADES_ENABLED` is set). n8n: any scheduled execution → Summary node →
`trigger` field (`production` until the gate expression is fixed).
