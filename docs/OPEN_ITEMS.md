# Open items — measured 2026-09-22

Everything here was measured against the live system on the date shown, not
recalled. Each entry says what is wrong, how it was measured, and what "done"
would mean. **Update the status line when you touch one.** An item whose status
line is stale is worse than no list, because it is the list people trust.

Numbering matches the report Hassan approved on 2026-09-22: 1-3 are being
worked now, 4-9 are the checklist to revisit once they land.

---

## Done 2026-09-22

### 1. `pipeline_daily` fails every day on the backtest step
**Status: FIXED 2026-09-22** - book read batched to one request per ladder
(`book_as_of`), a 26-minute budget under the step's 30 with each workflow
passing its own, stalled runs reclaimed after 90 minutes, and 5f7bc321
reclaimed by hand with a replacement queued over the four enabled
strategies. Proven by the next pipeline_daily run, or by Run workflow on
Backtest.

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
**Status: FIXED 2026-09-22** - 529.4 MB -> 493.6 MB live. The prune was not
broken; the steady state had no headroom. Every dataset now declares a floor
as well as a window and drops to it above 92% of the tier (`storage_pressure()`,
hysteresis to 85%). book_snapshots and edges moved from weekly to daily
reclaim - six days had left 22,737 and 15,234 dead rows, worth 30 MB.

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
**Status: RESOLVED 2026-09-22, one fixed and 82 accepted with a precondition.**
derived_model_promotion has RLS on with anon_read; 32 functions had their
search_path pinned; two same-day helpers stopped granting anon. The 82
`security_definer_view` findings are how a single-operator anon-key app reads
its own per-user tables - switching them blanks the desk pages. That holds
only while the app has no sign-in, and
`tests/test_the_definer_views_are_a_decision.py` fails the day it gains one.

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

## Done 2026-09-22, second pass

### 4. The international under-read
**Status: REFUTED 2026-09-22 — there is no offset to fit.**

27 of 30 remaining misses are hourly-only cities where the true peak falls
between 24 daily reports. The obvious remedy is a bias correction. It does not
work:

    a single global offset      delta = 0 is already optimal. Every non-zero
                                delta from -1.0 to +2.0 scores equal or worse:
                                90.7% at 0.0 and 0.1, 89.3% at 0.5.
    a per-city offset, fitted   train on the first half of each city's settled
    on half, tested on the      ladders and test on the second: 277 -> 278 of
    other half                  312. Only 7 test rows even drew a non-zero
                                offset.

One row in 312 is noise. The residual is not a systematic offset; it is
irreducible from a 24-sample feed. What would fix it is a SECOND SOURCE, which
is a data decision rather than a correction. Already ruled out before this:
the station (48/48 match the site id in each market's `rules_text`), the day
boundary (local 75.0% vs UTC 70.8%), rounding (worse), and the authoritative
reader (76.2%). **Do not re-derive this.**

### 5. Nothing reads `observation_trust`
**Status: FIXED 2026-09-22.** s5 and s7 carry `min_observation_trust` (0.80)
and `Strategy.applies_to()` enforces it in the base class, so a city below the
floor is not theirs to trade. The SQL mirror gates all three arms on the same
number. Live: 47 of 48 cities measured, 8 below the floor.

The trust is a COLUMN on `cities`, written by `refresh_observation_trust()`
nightly after the settlement freeze, not a join to `v_settlement_agreement` -
that view needs `band_contains()` from ad4_34 and therefore installs after
both engines that need to read it. NULL means fewer than ten settled ladders,
and every consumer reads it as "no opinion, let it through": a new city has
not failed, it has not been measured.

### 6. `s4_tail_fade` is still on
**Status: RETIRED 2026-09-22, on Hassan's instruction to work the checklist.**
451 marked signals, -2.2c on the dollar, 77.4% of its calls right. That last
number is why it outlived the other five by a day: being right three times in
four is a good rule that still loses money, because a NO leg on a tail band
costs 90-odd cents to win a few. Switched off, not deleted, with the same
stamp guard - one flag to reverse.

### 7. Calibration is inactive
**Status: CONFIRMED ON TRACK, nothing to fix.** The map is fitted and written
INACTIVE every night, so the number is visible and nothing prices on it. Its
current finding: OVERCONFIDENT, T = 2.730 - the ladder is too peaked and
calibration would flatten it toward uniform. It needs 30 settlement dates
(had 5) and 300 complete ladders (had 207), and the coherence fix of the same
morning changes which ladders count, so both counters move on their own.
Re-check when the calendar fills; there is no code to write.

### 8. The new sizing has not run live
**Status: VERIFIED against live prices 2026-09-22.** Atlanta, four quoted
bands of an eleven-band ladder, read straight off `v_trade_plan`:

    band        our p     price    cost incl fee
    84-85F      0.0784    0.1381   0.1440
    86-87F      0.2881    0.2619   0.2716
    88-89F      0.3907    0.4504   0.4605
    90-91F      0.1961    0.2670   0.2768
                -----     ------   ------
                0.9533    1.1174   1.1552

A dollar of payout costs 1.1552 while the quoted subset holds 0.9533 of our
probability - the overround, visible. The allocator takes ONE of the four
(86-87F, the only one clearing sigma = 0.9773), $41 of a $10,000 bankroll,
capped by depth. The flat 5% rule it replaced would have staked $500 on each
of the four, including 88-89F at 0.4605 to win 0.3907 - a 15% loss per dollar.

Still to see: a real `pipeline_intraday` run, for the per-strategy weight
lines and the resized count.

### 9. `s7_pre_peak_gradient`'s cadence
**Status: MEASURED, and deliberately not rebuilt yet.**

Turning each city's peak hour into a UTC instant and asking which of
`pipeline_intraday`'s six daily runs lands in the 60 minutes before it:
**15 of 48 cities are caught, and all 48 would be at an hourly cadence.**

So s7 is under-sampled, not blocked - the real blocker was freshness, and
that went 0 -> 37 of 48 cities when the feed was un-gated. 15 cities a day is
about 105 city-days a week, which is enough to find out whether s7 works.

Hourly on Actions costs ~540 runs a month against a 430 budget, so tripling
the coverage means moving the evaluation to n8n - a SECOND SIGNAL WRITER,
which is a design change with dedupe and conflict implications. Build that
cadence for a strategy that has earned it. s7 has never fired a signal.
**Revisit once it has a record on those 15.**
