# The eight strategies

All eight ship `enabled = false`. That is deliberate and it is the single most
common cause of "the Signal Engine runs and nothing happens": with every
strategy off, `signals` stays empty however good the prices are. Section 5 of
`sql/ad4_diagnose.sql` reports the count, and the enabling is one statement:

```sql
update strategies set enabled = true where strategy_id = 's8_two_bucket_cover';
```

Enable them one at a time. Two strategies with overlapping conflict classes
firing on the same band is a resolution the conflict layer has to make, and it
is easier to read the log when only one of them is new.

| id | name | side | class | needs |
|---|---|---|---|---|
| `s1_buy_low_sell_signal` | Buy-low / sell-on-signal | BOTH | directional | prices |
| `s2_combination_arb` | Combination arb | BOTH | arb | prices only — no forecast |
| `s3_concentration` | Concentration | YES | directional | measured skill |
| `s4_tail_fade` | Tail fade | NO | directional | prices |
| `s5_running_max_lock` | Running-max lock | YES | directional | `live_weather`, seasonal gate |
| `s6_anchor_insurance` | Anchor + insurance | BOTH | basket | prices |
| **`s7_pre_peak_gradient`** | **Pre-peak gradient entry** | **BOTH** | **directional** | **`sql/ad4_26`** |
| **`s8_two_bucket_cover`** | **Two-bucket cover** | **YES** | **basket** | prices, forecast or `ad4_26` |

The last two are Hassan's own manual trading written down as rules. Both are
CANDIDATES: no hit rate is claimed for either, because neither has been
measured against this desk's data yet. Backtest them before sizing them.

---

## S7 — Pre-peak gradient entry

**The trade as traded by hand.** An hour or less before the peak, look at how
the temperature has *moved* over the last few readings. Still climbing, and the
band above the current reading is still live and usually still cheap. Already
rolling over, and that band is dead and the market has not always noticed.

The level alone cannot tell those apart. **28.4 °C an hour before peak is a buy
after 26.9 / 27.7 / 28.4 and a sell after 29.1 / 28.8 / 28.4.** Every gate below
exists to make that difference mechanical.

### What it reads

All from `sql/ad4_26_temp_trend.sql`:

| input | what it is |
|---|---|
| `slope_3_c_per_h` | least squares over the last **three** readings, on their real timestamps |
| `slope_6_c_per_h` | the same over the last six — the check on whether the short slope is a turn or a wobble |
| `rolling_over` | short slope negative while the six-reading slope is still positive. **That is the turn**, before the average catches up |
| `implied_max_c` | the latest reading plus how much this city has *historically* still climbed from this local hour |
| `implied_max_low_c` | the p10 case — the poor finish |

Least squares rather than last-minus-first because METAR is hourly but SPECIs
are not: uneven spacing breaks the naive form, and a SPECI landing between two
routine observations would fake a slope change.

### The gates

**Entry, YES.** Inside `entry_window_min` (default 60) of the peak; reading no
older than `max_reading_age_min` (default 90); `slope_3 > min_slope_c_per_h`
(default 0.10, below which it is instrument noise wearing a sign); not rolling
over; the band **contains `implied_max_c`**; and `implied_max_low_c` clears the
band's floor. That last one matters — without it the strategy buys the band the
day reaches only on its best tenth of afternoons, which is a hope rather than a
trade.

**Entry, NO.** The mirror, and the half a level-only desk cannot see. Rolling
over inside the window, sell the bands the day can no longer reach. Measured
against the **running max**, not the latest reading: a rolled-over day has
usually already fallen below its own high, and a band between the two is not
unreachable — the day has been there.

**Exit.** The entry test failing. Entered on a climb, the climb rolled over, the
band is now above the banked maximum. Holding to settlement would be a
different strategy — one with no reason to have entered early.

### Not S5

S5 waits for `day_decided` — the day provably over — and buys the band holding
the locked maximum. S7 fires *before* that, on the direction of travel, and
accepts the risk of being early. They may overlap; the conflict layer decides.

### Staleness is fatal here

A slope computed from a ninety-minute-old reading describes an hour that has
already finished. `max_reading_age_min` is a hard gate, not a preference. This
is also why S7 needs the **observation series** n8n P1.2 now archives: a single
latest reading has no slope at all.

---

## S8 — Two-bucket cover

**The trade.** Buy the two most likely buckets together as long as the pair
costs under `max_pair_cost` (default $0.70). Exactly one bucket settles Yes, so
a pair costing 70c returns $1.00 if either lands — about 43% on the stake — and
it lands whenever the day finishes anywhere in a two-bucket span.

**Why it is not free money.** Two buckets at 35c each are cheap for a reason,
usually because the market thinks the day lands somewhere else. Cost alone is a
trap, so the price test is the first of four:

1. **Cost** — the pair *including fees*, under the cap. Polymarket charges
   `shares × 0.05 × p × (1−p)`, which peaks near 50c: two 35c buckets carry
   meaningfully more fee than a 90c/5c pair, and ignoring that is how a 0.70
   rule quietly becomes 0.72.
2. **Edge** — the pair's model probability must exceed what it costs. Paying 70c
   for a pair the model makes 55% is a losing trade executed tidily.
3. **Adjacency** — the two must be neighbours. Two non-adjacent buckets covering
   a bimodal guess is not a cover, it is two bets, and it leaves the gap between
   them — where the forecast actually points — uncovered.
4. **Anchor** — one of the two must contain where the day is *actually* heading:
   the forecast, or the observation-implied maximum from `ad4_26`.

Gate 4 is what "backed by temp and forecast analysis" means in code. Either
anchor satisfies it: the forecast is the better estimate early in the day, the
implied maximum is better an hour before peak, and requiring both would switch
the strategy off exactly when they disagree — which is when it is most
interesting.

### Sizing

Both legs get the **same share count**, sized off the pair's total cost. The
default sizing (`cap_usd / price`) would buy more shares of the cheaper leg, and
a cover with unequal legs pays out unevenly — a directional bet wearing a
cover's name.

### One position, not two

Both signals share a dedupe bucket and the same payload object, so the conflict
layer treats the pair as the single position it is.

### The browser agrees

`web/lib/cover.ts` is a port of this strategy, and it is what the **City
Monitor** page uses to flag a live cover pair. A desk that reads "cover pair
live" on screen and then gets no signal has learned to distrust both surfaces,
so `tests/test_strategies.py` runs the two on the same six cases — including the
fee-sensitive mid-priced pair and the one right at the cap — and fails if they
disagree on a single qualify/block decision.
