# Open items — measured 2026-09-22

Everything here was measured against the live system on the date shown, not
recalled. Each entry says what is wrong, how it was measured, and what "done"
would mean. **Update the status line when you touch one.** An item whose status
line is stale is worse than no list, because it is the list people trust.

Numbering matches the report Hassan approved on 2026-09-22: 1-3 are being
worked now, 4-9 are the checklist to revisit once they land.

---

## In progress

### 1. `pipeline_daily` fails every day on the backtest step
**Status: IN PROGRESS (2026-09-22)**

Run 26 died on `The action 'Queued backtests' has timed out after 30 minutes`.
Three faults, not one:

- Backtest `5f7bc321` asks for 9 strategies x 30 days x 48 cities. The last
  comparable completed runs were 2 strategies x 14 days and took 14 and 29
  minutes. Nothing bounds the work to the step budget.
- That run is now stuck `status='running'` since 09:26 with no `finished_at`.
  `poll_and_run_queued()` only selects `status='queued'`, so **nothing will
  ever reclaim it** - the row lies about its state for ever.
- Its params were captured while all nine strategies were enabled, so it would
  spend the budget backtesting the five retired on 2026-09-22.

It also burns 30 metered Actions minutes a day.

**Done means:** the daily run is green, a stalled run is reclaimed rather than
abandoned, and the work fits the step or checkpoints across runs.

### 2. Storage is over the free tier and climbing
**Status: IN PROGRESS (2026-09-22)**

522 MB / **104.5%** of the 500 MB tier, up from 508 MB / 101.6% the same
morning - roughly 14 MB a day. Largest tables:

    book_snapshots             101 MB    183,837 rows
    trades_observed             57 MB     96,312
    research_captures           49 MB     21,037
    paper_resolution_evidence   43 MB      6,333
    edges                       41 MB    134,062

The archive-then-prune cycle exists and runs. It is not keeping pace with what
the now-hourly observation feed writes.

**Done means:** under 500 MB with headroom, and the retention that gets it
there is automatic rather than a one-off sweep.

### 3. Supabase security advisors: 83 at ERROR
**Status: IN PROGRESS (2026-09-22)**

    ERROR  82  security_definer_view          incl. v_paper_desks, v_city_stats
    ERROR   1  rls_disabled_in_public         derived_model_promotion
    WARN   32  function_search_path_mutable
    WARN   22  anon_security_definer_function_executable
    WARN   22  authenticated_security_definer_function_executable
    WARN    1  materialized_view_in_api       mv_venue_band_resolution
    INFO   16  rls_enabled_no_policy

A `security definer` view runs as its owner, so it bypasses the CALLER's RLS.
Nothing here is known to be exploited; it is the largest single block of known
risk in the system.

**Done means:** both ERROR classes cleared or each survivor carries a written
reason for existing.

---

## The checklist — revisit once 1-3 are done

### 4. The international under-read
27 of 30 remaining settlement misses are hourly-only cities, where the day's
true peak falls between 24 reports. Ruled out already: the station (48/48
match the site id in each market's `rules_text`), the day boundary (local
75.0% vs UTC 70.8%), rounding (worse), and the authoritative reader (76.2%).
Not fixable from a 24-sample feed. Options are a second source or a fitted
per-city bias correction. Measured; not corrected.

### 5. Nothing reads `observation_trust`
`v_settlement_agreement.observation_trust` is published per city and no
strategy consumes it. dallas 46.2%, beijing 66.7%, singapore 66.7%. A
running-maximum rule in a city we get right half the time is half a rule.

### 6. `s4_tail_fade` is still on
Same model-vs-price family as the five retired on 2026-09-22 - its single gate
is `no_edge_net_pp > 0` - and it is -2.2c on the dollar over 451 marked
signals. It was not among the five Hassan approved, so it was left alone.
**His decision, not a defect.**

### 7. Calibration is inactive
5 settlement dates against 30 needed, 207 complete ladders against 300. It
activates itself as the calendar fills. Nothing to fix; confirm it flips and
that prices then carry the haircut.

### 8. The new sizing has not run live
`scripts/allocator.py` and `scripts/strategy_gate.py` are wired into
`signal_engine` and tested, but have never touched real numbers. The next
`pipeline_intraday` run is the first. Check the per-strategy weight lines, the
resized count, and that no ladder over-stakes.

### 9. `s7_pre_peak_gradient` still cannot be tested properly
Its freshness gate went 0 -> 37 of 48 cities when the observation feed was
un-gated, but `pipeline_intraday` runs 6x/day and s7's entry window is 60
minutes wide, so it samples about a quarter of city-days. Hourly on Actions
costs ~540 runs/month against a 430 budget, so it needs a different home -
n8n, the way the observation feed moved.
