# Schema assumptions this build makes

`ad4_schema.sql` / `ad4_functions*.sql` (the base Phase 0 schema) are not
in this repo - they were run directly against Supabase before this repo
existed, and this session has no Supabase/Postgres credentials to inspect
the live schema. Everything here is built against:

1. Column names the spec itself states explicitly (e.g. Task 2's SQL
   naming `bands.band_id`, `bands.band_lo/hi/open_low/open_high/
   band_label/token_yes/token_no`, `markets.market_id/city_key/
   resolution_date/unit`, `cities.display_name/icao/station_name/
   timezone/band_width`, `book_snapshots.best_bid/best_ask/spread/
   market_state`, via the `v_opportunities` view definition).
2. Field lists the spec gives for tables whose full schema isn't shown
   (the `Signal` dataclass for `signals`, the trade-lifecycle fields for
   `paper_trades`, the ledger chain fields) - used verbatim as the
   intended column names.
3. Where neither applies, a documented, defensive best-effort guess -
   listed below. **Verify each of these against the real schema before
   trusting the output; none of them break Task 2's SQL (everything uses
   `add column if not exists`), but a wrong guess in Python will surface
   as a PostgREST 400 on an unknown column, not silent corruption.**

| File | Assumption | Why | Verify by |
|---|---|---|---|
| `edge_engine.py` | `book_snapshots` has `bid_levels`/`ask_levels` (jsonb arrays of `{price,size}`), falling back to `bids`/`asks` | Spec §0.2 says book_snapshots stores "Depth + price-impact curves + volume" but never names the columns | `select bid_levels,ask_levels from book_snapshots limit 1` in the SQL editor |
| `edge_engine.py` | NO-side book is the complement of the YES book (`no_ask = 1 - yes_bid`), not a separately-stored NO-token book | Nothing in the spec names a second per-band book row/column for the NO CTF token | If a real per-token book exists, swap `levels_for_side()`'s NO branch for a direct read - nothing else changes |
| `regime.py` | `derived_weather_peak` has a `window_width_h` column, queried with `select=*` and `.get()` | Spec explicitly names this column (`derived_weather_peak.window_width_h`) in Task 5 | Already spec-given, low risk |
| `edge_engine.py` | `anomalies` table accepts `{detected_at, band_id, kind, side, value, detail}` | Spec lists `anomalies` only as an empty "support table" with no schema shown | Insert is wrapped in try/except and logs to stderr on mismatch rather than crashing the run |
| `probability_engine.py` | `band_probabilities` has `prob_id` (bigint PK), `calibrated_prob`, `forecast_version`, `calibration_version` in addition to the Task-2-added columns | `edges.prob_id references band_probabilities(prob_id)` and Task 4's verify SQL both name these; Task 4 step 8 explicitly lists `forecast_version`/`calibration_version` as fields to write | Spec-given, low risk |

Everything downstream (edges, capacity, correlation, strategies, paper
engine, signals, settlement, backtest) inherits these same assumptions
transitively through the tables above, rather than repeating new guesses -
noted inline in each file only where a *new* assumption is introduced.
