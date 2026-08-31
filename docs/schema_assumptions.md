# Schema assumptions — and how they were removed

## The short version

They are gone. `sql/ad4_00_preflight.sql` now guarantees every table,
column and unique key the rest of the SQL needs, and each of the other
eleven files opens with its own guard for what that file touches. Nothing
in this repo assumes a schema state it has not itself ensured.

## What the problem was

`ad4_schema.sql` / `ad4_functions*.sql` (the base Phase 0 schema) are not
in this repo — they were applied directly to Supabase before this repo
existed. Every SQL file was therefore written against a *guess* at that
schema, and the guess was wrong in production:

```
ERROR:  column "universe" of relation "strategies" does not exist
ERROR:  column "capital_cap_pct" of relation "strategies" does not exist
```

The real `strategies` table had exactly eight columns:

```
strategy_id, name, side, origin, config, conflict_class, enabled, created_at
```

Fixing that one error at a time, via the operator, is the wrong shape of
fix — each round trip only finds the next wrong guess.

## What replaced it

**`sql/ad4_00_preflight.sql`**, which runs before everything else and:

1. `create table if not exists` for every table a later file reads or
   writes but never creates;
2. `alter table … add column if not exists` for every column any later
   file touches — driven off an explicit list, so the file can report
   what it actually had to add rather than asserting it blindly;
3. ensures the unique indexes that later foreign keys and `on conflict`
   clauses depend on (`cities.city_key`, `markets.market_id`,
   `bands.band_id`, `book_snapshots.snapshot_id`,
   `band_probabilities.prob_id`, `strategies.strategy_id`,
   `settings.key`), plus the composite uniques the Python ingest jobs
   upsert against;
4. creates the Supabase-specific objects the other files use — the `anon`
   role, the `supabase_realtime` publication, and `log_ingest()` — but
   only when they are genuinely absent, so on Supabase every one of those
   branches is a no-op and on a vanilla Postgres the same files still run;
5. ends with a verification block that RAISES NOTICE listing every object
   it added.

It never drops, renames, retypes or deletes anything. It is safe to run
any number of times, in any order, against an empty database or a live one.

## Verified, not assumed

Against a real local Postgres 16, starting from nothing but the confirmed
eight-column production shape of `strategies`:

- all twelve files run clean, twice, back to back, zero errors;
- the preflight file reported and added **108** missing columns on that
  run (five of them the `strategies` columns that failed in production);
- every RPC defined across the files then executes with representative
  data — `calc_recommendation` in both modes, `settle_markets`,
  `queue_backtest`, `recompute_capacity`, `recompute_correlation`,
  `refresh_derived`, `log_paper_trade`, `approve_signal`,
  `dismiss_signal`, `close_position`, `update_setting`,
  `upsert_deployment`, `set_deployment_status`, `build_morning_brief`,
  `build_eod_report`, `walk_ladder_jsonb`, `depth_usd`, `capacity_side`,
  and the four documented no-op stubs.

## Gaps this exposed that no file had covered

Two of these would have failed at runtime, not at install time, which is
why they had survived every earlier review:

| Column | Written by | Read by | Was added by |
|---|---|---|---|
| `paper_trades.partial_fill` | `scripts/paper_engine.py` | `log_paper_trade()` | *nothing* |
| `paper_trades.requested_shares` | `scripts/paper_engine.py` | `log_paper_trade()` | *nothing* |
| `backtest_trades.legs_requested` | `scripts/backtest/engine.py` | — | *nothing* |
| `backtest_trades.legs_filled` | `scripts/backtest/engine.py` | — | *nothing* |

All four are now guaranteed by the preflight file.

## Assumptions that remain, and how to check them

These are about *data shape*, not about whether a column exists, so the
preflight file cannot settle them. Each one is defensive in code — a wrong
guess surfaces as a logged warning or an empty result, never as silent
corruption.

| Where | Assumption | Why | Check it with |
|---|---|---|---|
| `edge_engine.py`, `ad4_capacity_correlation.sql`, the Goals page | `book_snapshots.bid_levels` / `ask_levels` are jsonb arrays of `{"price": n, "size": n}` | Spec §0.2 says book_snapshots stores depth and price-impact curves but never names the columns | `select bid_levels, ask_levels from book_snapshots limit 1;` |
| `edge_engine.py`, Goals page | The NO-side book is the complement of the YES book (`no_ask = 1 - yes_bid`), not a separately stored NO-token book | Nothing in the spec names a second per-band book row or column for the NO CTF token | If a real per-token book exists, swap the NO branch of `levels_for_side()` and of `lib/spread.ts`'s ladder builder — nothing else changes |
| `regime.py` | `derived_weather_peak.window_width_h` exists | Spec names this column explicitly in Task 5 | Spec-given, low risk |
| `edge_engine.py` | `anomalies` accepts `{detected_at, band_id, kind, side, value, detail}` | Spec lists `anomalies` only as an empty support table with no schema shown | Now guaranteed by the preflight file; the insert is still wrapped in try/except |
| `probability_engine.py` | `band_probabilities` has `prob_id`, `calibrated_prob`, `forecast_version`, `calibration_version` | `edges.prob_id references band_probabilities(prob_id)` and Task 4's own verify SQL both name these | Now guaranteed by the preflight file |
| `refresh_derived()`, `v_band_volume`, `v_city_volume` | `trades_observed` carries `city_key`, `band_id`, `price`, `size`, `observed_at` | Spec names `trades_observed` as historical market data but not its columns | `select city_key, band_id, price, size, observed_at from trades_observed limit 1;` — if `band_id` is absent or always null, band-level volume stays 0 and every band reads as thin, which is honest rather than wrong |

## Deliberate change: `strategies.universe` / `regime_filter` are jsonb

The seed used to write `text[]`. They are now `jsonb` arrays, because that
is what the preflight file can safely add to a table whose original shape
this repo does not control. PostgREST hands both back to
`scripts/strategies/base.py:StrategyConfig` as a plain Python list either
way, so nothing downstream changed.
