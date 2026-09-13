# AD4 handoff — 13 September 2026

Written to end a very long session and let a fresh one start cold. Everything
here was measured against the live database or the live repository, not
recalled.

## The stack, in one breath

Weather-derivatives paper-trading desk on Polymarket temperature markets.
Supabase (Postgres + PostgREST) holds the data; **n8n ingests** (markets,
books, trades, forecasts); **GitHub Actions computes** (probabilities, edges,
skill, settlement, backtests); **Next.js on Vercel** is the UI, deployed from
`main`.

Repo `hassansab00/arbdesk4`. Supabase project `jittmxhzgqpifitwupss`.

> **Vercel deploys from `main`.** Work pushed to a branch is invisible to the
> user no matter how much of it there is. This cost a day: sixteen commits sat
> on a branch while the user looked at an unchanged site and was told each time
> that the work was "pushed".

## Two agents, one repository

**Codex works in this repo at the same time.** Its work arrives as
`supabase/migrations/2026MMDD_*.sql` and the Phase 1/2 script changes.

**Do not touch** `scripts/databank.py`, `scripts/calibration.py`,
`scripts/paper_settlement.py` — the user assigned those to Codex explicitly.

Three collisions happened. Read these before changing anything shared:

1. **Proprietary columns.** `ad4_57` granted `anon` table-wide SELECT on
   `weather_resolution_evidence`, which includes `raw_payload` and
   `source_url`. Phase 2A deliberately withholds those two
   ("worker-only even in the single-user/no-login deployment") and grants
   thirteen verdict columns. Narrowed back. **Never widen a grant to make a
   query work without checking what the migration already decided.**

2. **`security_invoker` views.** `v_verified_weather_outcomes` is
   `security_invoker`, and its inner CTE *reads* `raw_payload` and
   `source_url` before the outer SELECT drops them. A security_invoker view
   needs the caller to hold SELECT on every column it READS, not only those it
   returns — so the correct grant makes the view unreadable. Resolved in
   `ad4_61` by making that one view run as DEFINER, which is stricter than a
   column grant (anon never touches the base table). The alternative, one line
   in Codex's CTE, is theirs to make.

3. **`one_single_paper_desk`.** Codex's single-operator migration enforced
   exactly one paper account. The user explicitly asked for several, so
   `ad4_60` drops that index and makes `create_single_paper_account()`
   deterministic. **This reverses a Codex decision on the user's instruction** —
   if a `supabase db reset` ever runs, their migration recreates the index and
   silently returns the desk to one account.

**Two migration systems run on one database** and neither knows about the
other: `sql/ad4_NN_*.sql` + `INSTALL_ORDER.txt` (this side) and
`supabase/migrations/` (Codex). Nothing in CI applies either — both are run by
hand. Anything written only in `sql/` is lost by a Supabase reset.

## Live state

**Database: ~498 MB against a 500 MB free tier.** It hit 513 MB (102.6%) and a
REINDEX of the five biggest tables brought it back. This is not fixed, only
postponed — see "Do this first".

| Table | Total | Indexes | Rows |
|---|---|---|---|
| `weather_forecasts` | 124 MB | 78 MB | 343,097 |
| `weather_observations` | 121 MB | 59 MB | 519,649 |
| `trades_observed` | 102 MB | 49 MB | 140,007 |

Growth ≈ 22,800 rows/week ≈ **34 MB/month**.

**Feeds are healthy.** Open-Meteo 54 cities and NWS 12 cities, both ~1h old,
ingested by n8n. The platform *is* forecasting — what was missing was never the
forecast.

**Empty for real reasons, not bugs:**

| Relation | Why |
|---|---|
| `weather_resolution_evidence` | 0 rows. The capture has never run. This is the root of the whole Phase 2A gate story below |
| `backtest_results` / `backtest_trades` | Both queued runs failed writing with `400`. See "Known bugs" |
| `derived_weather_model` / `derived_model_forecast` | `weather_model.yml` is Monday-only and has never produced output |
| `ledger`, `paper_trades`, `strategy_conflicts` | 0 of 10 strategies enabled, paper desk paused. Nothing has traded |

## The Phase 2A gate — read this before "fixing" an empty panel

Phase 2A made every settled comparison require a corroborating row in
`weather_resolution_evidence`. **That table is empty**, so
`v_prediction_scorecard`, `v_forecast_convergence`, `v_calibration`,
`v_edge_realisation` and `v_edge_scaling` all returned zero rows — while
`fact_forecast_outcome` held 2,268 real settled outcomes and
`fact_band_outcome` held 5,605.

`ad4_62` publishes `v_prediction_scorecard_all` and
`v_forecast_convergence_all`: the same measurements over the same source
table, carrying corroboration as a **column** (`verified`, `n_verified`,
`fully_verified`) instead of using it as a filter. `/predictive` reads those
and prints the ratio in a banner. The Phase 2A views are untouched. When the
evidence capture runs, both go green with nothing to change.

## Done in this session

- **P0.2** was posting raw Polymarket objects into `markets`; **P0.4** had four
  stacked faults and now writes ~5,000 trades. P1.2/1.3/1.4 rebuilt and
  published. P1.5's service key moved into the n8n credential.
- **17 cities given coordinates** from settlement-airport positions, 37 → 54.
  Hong Kong used the Observatory rather than VHHH — the one judgement call, and
  still unconfirmed by the user.
- **Signals feature removed** (the table still holds 1,894 orphan rows).
- **`common.py` retries transient write failures.** One 504 used to abort a
  53-city job at city 11, discarding the ten it had written.
- **The daily pipeline had not finished since 2026-09-08.**
  `recompute_correlation` timed out; two indexes took it 7,330 ms → 1,536 ms.
  The cause was a sequential scan of 342k forecast rows because no index led
  with `lead_days`.
- **`ad4_50` was deleting a constraint.** Its "an index whose columns are a
  prefix of a longer one is redundant" rule is true of lookups and false of
  unique indexes — it had dropped the one `refresh_weather_peak`'s
  `on conflict (city_key, month)` needs, which then failed `42P10` silently for
  seven days. `ad4_56` restores it; `ad4_50` now refuses to drop any unique
  index; `tests/test_index_dedupe_rules.py` guards both loops.
- **`capacity.py` no longer reports green while its derivations fail**, and
  logs `peaks`, which it used to compute and discard.
- **Paper desks.** The browser was reading **zero** accounts: the only policy
  was `owner_id = auth.uid()` against a NULL owner. Fixed, plus several
  independent desks each with their own budget, strategies and cities.
- **`anon` statement_timeout 3s → 8s** on the live database, matching
  `authenticated`.
- **`forecasts.yml` had no cron at all** — dispatch-only, which is why the
  forecast archive was never extended. Now nightly at 03:10 UTC.
- **The archive covers forecasts too** (`ad4_63`, `prune_forecasts`).

## Do this first

1. **Run the archive.** Actions → *Archive Observations* → `keep_days 180`,
   `table both`, **`commit` unticked**. Read what it would delete, then run
   again with commit ticked. Dry run verified: would delete 269,744 forecasts,
   keep 73,353.
2. **`vacuum full`** in the Supabase SQL editor afterwards. Postgres does not
   return space to the OS on DELETE, and `VACUUM` cannot run from a migration
   or through the MCP tool (both wrap statements in a transaction).
   ```sql
   vacuum full public.weather_forecasts;
   vacuum full public.weather_observations;
   ```
3. **Rotate the `service_role` key.** Repeatedly flagged, still outstanding. It
   went out in an exported workflow body.

Expected result: ~166 MB freed, database ≈ 340 MB, steady state thereafter
(the monthly sweep removes roughly the 34 MB that arrives each month).
`docs/local_archive.md` is the full procedure including loading an archive into
local PostgreSQL and pointing backtests at it through PostgREST with no code
change.

## Known bugs, not yet fixed

- **The backtest `400`.** Both queued runs failed writing `backtest_results` on
  4 and 5 September. The user's own plan diagnosed it on 8 September — *"the
  current writer sends `data`, while the live table still requires non-null
  `metrics` without a default"*. The live columns do include both, and a NaN
  theory was tested and disproved. Needs one run to reproduce. **This is the
  highest-value open bug.**
- **Sigma is far too wide at lead 1.** Austin states ±4.23 °C where measured
  MAE is 1.13 and the worst tenth is 2.00, so the model spreads ~10% per bucket
  while the market concentrates 47% and 39% into two.
  `v_city_prediction_confidence.honesty` already says so and nothing acts on
  it. Lives in `scripts/probability_engine.py` (Codex-adjacent).
- **`v_trade_plan` truncates at 1,000 rows** on /opportunities — PostgREST's
  default cap.
- **1,894 orphan `signals` rows** from the removed feature.
- **Convergence chart axis** — "days ahead" running right-to-left toward zero
  is confusing; should read as a countdown with the actual marked at zero.
  (This was task 3 and was not started.)

## Working rules learned the hard way

- **Measure, don't assert.** Several confident diagnoses were wrong: the
  Supabase outage blamed on `v_data_freshness` (24 ms, it was a victim);
  "backtest has never run" (48 runs); `trades_observed` "bloat" (140,007 rows
  at a normal 399 bytes, read from a stale statistics estimate); "27 of 55 rows
  have the centre outside their own band" (Celsius compared against Fahrenheit
  buckets).
- **Become the role.** `has_table_privilege` does not catch a
  `security_invoker` view failing on a base table. `set local role anon; select
  1 from <rel> limit 1;` does.
- **A view's tests can pass on a broken file** if an assertion matches the
  comment explaining the rule. Strip SQL comments before matching.
- **`v_prediction_ladder` carries one row per SIDE.** `calibrated_prob` is the
  same on both; `market_price` is not (YES ~0.09, NO ~0.83). Always filter
  `side = 'YES'`.
- **The ladder's end buckets are open-ended**, so `max(calibrated_prob)` picks
  the widest bucket, not the likeliest.
- **`paper_activity` is append-only** — a trigger refuses deletes. A reset is
  recorded as an event, not erased.
- The service_role key belongs only in the n8n credential, never in a Config
  node and never in a `NEXT_PUBLIC_` variable. Workflow JSON must never be
  exported with real config values.

## Verification

`PYTHONPATH=scripts python -m pytest tests/ -q` — **743 passed, 6 skipped**.
`cd web && npx tsc --noEmit` and `npm run build --prefix web` both clean.
`npm test --prefix tests/database` — Codex's paper contracts still pass.

Regenerate after adding any SQL relation, or three UI-contract tests fail:
```
python3 tools/gen_provenance.py && python3 tools/gen_sql_owner.py
```
