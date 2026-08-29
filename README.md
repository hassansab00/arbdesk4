# AD4 - Arb Desk

Weather-based quantitative trading system for Polymarket daily
high-temperature band markets across 54 cities. Paper trading only:
real market data, real calculations, real logged trades, real settlement
and P&L, no automated real-money order submission.

Full build specification: see the spec document supplied alongside this
repo (`AD4-COMPLETE-BUILD-SPEC.md` + Revision A). This README is the map
of what got built against it and where to find things.

## Start here if you're picking this up

1. **Run the SQL, in this order**, in the Supabase SQL editor (each file
   is idempotent - safe to re-run):
   `ad4_phase1_tables.sql` (pre-existing) → `sql/ad4_phase2.sql` →
   `sql/ad4_phase2_ranking.sql` → `sql/ad4_capacity_correlation.sql` →
   `sql/ad4_strategies_seed.sql` → `sql/ad4_paper_engine_columns.sql` →
   `sql/ad4_settlement.sql` → `sql/ad4_backtest.sql` → `sql/ad4_rpc.sql` →
   `sql/ad4_live_weather.sql` → **`sql/ad4_rls.sql` last** (it grants
   EXECUTE on functions the earlier files define).
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

sql/                        Every ad4_*.sql file from Task 2 onward
docs/                       Honest-limitation write-ups and design decisions -
                             read architecture_deviations.md and
                             schema_assumptions.md first
n8n/                        Importable workflow templates (Task 15)
web/                        Next.js frontend (Task 14)
tests/                      pytest suite - 112 tests, run with:
                             PYTHONPATH=scripts python -m pytest tests/ -q
```

## What to read before changing anything

- `docs/schema_assumptions.md` - every place this build guessed a column
  name because the base schema (`ad4_schema.sql`) isn't in this repo and
  this session had no live database to inspect.
- `docs/architecture_deviations.md` - where this build deliberately
  differs from the literal spec text (mainly: several things Revision A
  describes as n8n-triggered Postgres RPCs are GitHub Actions Python jobs
  instead, to keep non-trivial logic in one tested place).
- `docs/skill_baseline.md` / `docs/settlement_verification.md` - both
  document a real network-access gap in this build session (no egress to
  Supabase or weather.gov from the sandbox it ran in) and exactly what to
  run, with real network access, before trusting that part of the system.

## Non-negotiable rules (from the spec, still true)

Never invent an empirical claim. Preserve `observed_at`/`valid_at` and
`run_at`/`for_date` separation. All P&L is net outside cost-analysis
views. The paper engine fills at executable price, not mid. Every fired
signal is logged, approved or not. Band identity is not comparable across
dates. UTC date bucketing must stay consistent between the forecast
ingest and skill measurement jobs. Never log secrets. Model probability
never auto-fills the user's own input unless explicitly toggled on.
