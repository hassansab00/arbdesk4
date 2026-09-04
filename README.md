# AD4 - Arb Desk

Weather-based quantitative trading system for Polymarket daily
high-temperature band markets across 54 cities. Paper trading only:
real market data, real calculations, real logged trades, real settlement
and P&L, no automated real-money order submission.

Full build specification: see the spec document supplied alongside this
repo (`AD4-COMPLETE-BUILD-SPEC.md` + Revision A). This README is the map
of what got built against it and where to find things.

## Start here if you're picking this up

0. **Follow `docs/GO_LIVE.md`.** It is the single ordered checklist for
   taking this from a fresh Supabase project to a working desk, with the
   exact command and the exact expected result at every step. Everything
   below is the map; that file is the route.

1. **Run the SQL, in this order** - eighteen files, in the Supabase SQL
   editor (each file is idempotent, safe to re-run any number of times).
   `docs/GO_LIVE.md` step 3 is the same list with a one-line description
   of each:

   1. **`sql/ad4_00_preflight.sql`** - **run this first.** It guarantees
      every table, column and unique key the other seventeen need, whatever
      state the database is in, and prints a NOTICE listing exactly what
      it had to add. This is what makes the rest of the run order safe:
      the base Phase 0 schema is not in this repo, so nothing else may
      assume a column exists.
   2. `ad4_phase1_tables.sql`
   3. `sql/ad4_phase2.sql`
   4. `sql/ad4_phase2_ranking.sql`
   5. `sql/ad4_capacity_correlation.sql`
   6. `sql/ad4_strategies_seed.sql`
   7. `sql/ad4_paper_engine_columns.sql`
   8. `sql/ad4_settlement.sql`
   9. `sql/ad4_backtest.sql`
   10. `sql/ad4_rpc.sql`
   11. `sql/ad4_live_weather.sql`
   12. `sql/ad4_rls.sql` (it grants EXECUTE on functions the earlier
       files define, so it has to come after them).
   13. `sql/ad4_13_reconcile.sql` - reconciles files 1-12 with the real
       Phase 0 column shapes, which none of them could see.
   14. `sql/ad4_14_workflows.sql` - the Workflows page and its run history.
   15. `sql/ad4_15_pipeline_fixes.sql` - the `system` strategy the Signal
       Engine writes against.
   16. `sql/ad4_16_nws.sql` - api.weather.gov: per-city NWS ids, today's
       solar transit, and `v_forecast_divergence`.
   17. `sql/ad4_17_city_stats.sql` - each city's climatological normal and
       volatility, which is what lets the desk say how hot today is FOR THAT
       CITY rather than in absolute degrees.
   18. **`sql/ad4_18_databank.sql` last** - the immutable record of
       predictions against outcomes. Everything else here is live or derived
       and is destroyed by its own next run; this is the only durable
       asset the desk has, and calibration is fitted on it.

   Then run **`sql/ad4_99_verify.sql`** against the real database. It is
   read-only and returns one grid: every check PASS / FAIL / ATTENTION,
   which file to re-run for anything that failed, which tables have data
   and which job fills the ones that don't, and the **actual column shape**
   of the six tables this repo previously had to guess at.

   Each of the others also opens with its own self-sufficiency guard, so
   any one of them can be re-run in isolation without the others. The
   preflight file is still the thing to run first - it is the only place
   that sees the whole picture at once.
2. Set the GitHub Actions secrets `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`
   (already required by the three pre-existing workflows) - every new
   workflow under `.github/workflows/` reuses them.
3. Read `docs/skill_baseline.md` and `docs/settlement_verification.md`
   **before trusting probability or settlement output** - both document
   a real gap this build session couldn't close itself (no live
   Supabase/weather.gov network access from this sandbox) and exactly
   how to close it.
4. Deploy `web/` to Vercel with the two `NEXT_PUBLIC_SUPABASE_*` env vars
   (see `web/README.md`). Zero other Vercel config - there is no API
   layer.
5. Import the three `n8n/*.template.json` workflows and fill in their
   Config nodes (see `n8n/README.md`).

## Repo structure

```
scripts/                   Python engines (Tasks 1, 3-13d)
  common.py                 shared Supabase REST helpers
  cost_model.py              Task 3 - fee curve, ladder walking, round-trip cost
  probability_engine.py      Task 4 - bias-corrected Normal + whole-number lattice
  regime.py                  Task 5 - confidence/label, per-city percentiles
  edge_engine.py              Task 6 - executable price, net edge, tradeability
  market_state.py             Task 6 - §2.4 classification
  capacity.py                 Task 7 - thin wrapper around SQL RPCs
  strategies/                 Task 8 - base contract + all six strategies + conflicts
  paper_engine.py              Task 9 - fills, partial fills, legging risk, ledger
  signals.py                   Task 10 - orchestrates strategies + system signals
  settlement.py                 Task 11 - resolution verification + settle
  backtest/                     Task 12 - walk-forward engine, metrics, runner
  live_weather.py                Task 13d - resolution-source-first weather poll
  measure_skill.py, ingest_*.py  pre-existing Phase 0/1, lightly patched

sql/                        Every ad4_*.sql file. ad4_00_preflight.sql
                             runs FIRST and guarantees the schema the
                             other seventeen assume.
docs/                       Honest-limitation write-ups and design decisions -
                             read architecture_deviations.md and
                             schema_assumptions.md first
n8n/                        Importable workflow templates (Task 15)
web/                        Next.js frontend (Task 14)
tests/                      pytest suite - 229 tests, run with:
                             PYTHONPATH=scripts python -m pytest tests/ -q
```

## What to read before changing anything

- `docs/GO_LIVE.md` - the one ordered first-run checklist. Start here.
- `docs/schema_assumptions.md` - what the build used to guess about the
  base schema, and how `sql/ad4_00_preflight.sql` removed the guessing.
- `docs/architecture_deviations.md` - where this build deliberately
  differs from the literal spec text (mainly: several things Revision A
  describes as n8n-triggered Postgres RPCs are GitHub Actions Python jobs
  instead, to keep non-trivial logic in one tested place).
- `docs/skill_baseline.md` / `docs/settlement_verification.md` - both
  document a real network-access gap in this build session (no egress to
  Supabase or weather.gov from the sandbox it ran in) and exactly what to
  run, with real network access, before trusting that part of the system.

## Non-negotiable rules (from the spec, still true)

Never invent an empirical claim. Market volume and book depth are
different facts and are never merged into one "liquidity" number.
Preserve `observed_at`/`valid_at` and
`run_at`/`for_date` separation. All P&L is net outside cost-analysis
views. The paper engine fills at executable price, not mid. Every fired
signal is logged, approved or not. Band identity is not comparable across
dates. UTC date bucketing must stay consistent between the forecast
ingest and skill measurement jobs. Never log secrets. Model probability
never auto-fills the user's own input unless explicitly toggled on.
