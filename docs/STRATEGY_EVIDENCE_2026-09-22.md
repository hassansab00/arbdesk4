# Does any of this actually have an edge? Measured, 2026-09-22

A 12-month synthetic experiment (`ArbDesk4_Dynamic_Strategies_12_Month_Experiment`)
tested four dynamic strategies - S8D, S9D, S7D and a new candidate R1 - across
five assumed market worlds. Its own conclusion is the honest one: the
profitable worlds **assume** the market under-prices our forecast by 15-35%,
and it says plainly that it "did not discover that advantage in ArbDesk4".

That leaves exactly one question, and it is answerable from data we already
hold rather than from a simulator: **is there an information gap, and in which
direction?** Two measurements below answer it, and a third kills the new
candidate.

---

## 1. The information gap is negative. The market's probability is better than ours.

Multiclass Brier score per settled ladder. Lower is better. Both sides scored
on the SAME ladders, the SAME bands, the same day, with the winner present.

| | Brier |
| --- | --- |
| our model | **0.7913** |
| the market's implied probability | **0.1505** |
| a uniform guess across the same bands | 0.9074 |

131 ladders, 10.9 common bands each. **Our model beats the market on 3.1% of
ladders.** It is barely better than guessing uniformly (0.79 against 0.91)
while the market is five times better than us.

### The first cut of this was wrong, and the correction matters

The first attempt normalised each side over whatever bands it had priced. The
market quotes a median 6.8 of 11 bands and they sum to 0.329, and the winner
is priced by the market on only 135 of 749 ladders - so filtering to ladders
with a winner silently kept the ladders where the market HAD priced it. That
flatters the market by selection. The numbers above use only bands both sides
priced, only ladders where the winner is among them, each side renormalised
over that same set.

Timing was the other candidate confound and it is not one: the model's
probability is stamped a mean 22.1 hours AFTER the start of the resolution
day, so it is a same-day estimate being compared with a same-day price, not a
two-day-old forecast against a fresh quote.

### What that does to the experiment's table

Its profitable rows assume `L`, the weight the market puts on public-only
information, is 0.15 or 0.35 - i.e. that the market under-weights what we
know. We measure the opposite. The applicable row is therefore **"No
information gap", and even that is optimistic**, because L = 0 assumes the
market and the model are equally informed:

| assumed world | median net P&L on $10,000 |
| --- | --- |
| no information gap (daily entry) | **-$832** |
| no information gap (selective) | -$9 |
| 15% gap | +$226 |
| 35% gap | +$11,278 |

The +$11,278 is what the model would earn **if it were substantially better
than the price**. It is five times worse.

### This agrees with a completely independent measurement

Measured the same day by a different route: the market's top two bands contain
the winner 26.1% of the time, the model's top two 15.3%; and every strategy
whose entry test reduces to (model - price - fee) > 0 lost money on its own
settled signals - s1 -17.8c on the dollar over 738, s3 -9.1c over 819, s6
-20.4c over 1,105, s4 -2.2c over 451. Two independent measurements, same
answer.

---

## 2. R1 - forecast-revision lag - has no lag to trade

R1's premise is that when our forecast revises, the price has not yet caught
up. The experiment assumed that gap existed. It is directly measurable on data
we already store: `band_probabilities.computed_at` gives every revision,
`book_snapshots.observed_at` gives the book before and after.

Consecutive probability revisions of at least 2 points, over 25 days, each
paired with the book immediately before and the first book 2-8 hours after:

| | |
| --- | --- |
| revisions paired | **6,411** |
| correlation, our revision vs the price move that follows | **0.0387** |
| price moved the same way we revised | **52.5%** |
| mean price move after an UP revision | +0.0091 |
| mean price move after a DOWN revision | +0.0035 |

Both means are positive: the price drifts up whichever way we revise, which is
drift, not signal. With n = 6,411 a correlation of 0.039 is about three
standard errors from zero - statistically detectable, and economically
worthless: it explains 0.15% of the variance, and a 52.5% directional hit rate
cannot pay a three-cent median spread on a twenty-five-cent contract.

**R1 is refuted on our own data.** Not "unproven" - measured, and absent.

---

## 3. What of the experiment is already built here

Several of its controls were shipped on 2026-09-22 independently, before this
report was read. They are not new work:

| experiment's control | status here |
| --- | --- |
| quarter-Kelly sizing on a conservative probability | `allocator.DEFAULT_KELLY_FRACTION = 0.25`, in `Strategy.size()` |
| basket construction across 2-4 adjacent buckets by post-cost return | `allocator.allocate()` solves the whole ladder as one horse-race Kelly problem - strictly better than enumerating windows |
| fee-inclusive edge gate | `allocator.effective_cost()`, `q + 0.05q(1-q)` |
| liquidity cap on the least-liquid leg | depth cap per leg |
| minimum fill size | venue $5 minimum, dropped not shrunk |
| settlement-driven learning, model-vs-market weighting | `strategy_gate.py` - posterior on return per stake, zero-centred prior |
| calibration after completed outcomes | exists; inactive pending 30 settlement dates and 300 ladders |
| stale-observation rejection | s7's 90-minute reading gate, plus the new per-city `observation_trust` floor |

## 4. What is worth taking from it

Three controls it has that we do not, all cheap and all sound regardless of
whether any edge exists:

1. **Drawdown-scaled risk.** Risk multiplier declines with drawdown to a 20%
   floor; new entries stop at 30% drawdown while open positions resolve.
2. **Correlation budgets.** 1.5% of equity per city-day and 4% per longitude
   sector. Weather is spatially correlated and nothing here limits it -
   `derived_city_correlation` already holds the measured correlations, so the
   sector proxy can be replaced with the real thing.
3. **Daily gross entry budget.** 8% of day-start equity, not reset by exits.

And one finding worth acting on directly: its **selective policy beat its
daily-entry policy in every world without an assumed edge** (-$9 against -$832
with no gap; -$47 against -$328 under stress). Forcing a trade every day costs
money precisely when there is no edge. The desk should be allowed to decline.

## 5. What would have to be true for any of this to make money

The mechanism is not in doubt - it is the input. Every strategy here prices a
band from a probability. If that probability is worse than the price, no
sizing rule, basket shape or dynamic threshold can recover it; it can only
lose more slowly. The measurements above say ours is worse by a wide margin.

So the only work that changes the answer is work on the probability itself:

- **Station-specific bias and spread correction** of the public forecasts, per
  station, season and lead - the standard meteorological post-processing step.
  Measurable against a held-out period with no trading involved.
- **A proper distribution, not a point forecast**, converted to band
  probabilities through the contract's exact boundaries and rounding.
- **The gate: beat the market's Brier out of sample** on unseen dates before
  any capital follows. Today that gate reads 0.7913 against 0.1505.

Until the model beats the price, the honest position is the one the desk
already holds: three model-free strategies enabled, every model-vs-price
strategy retired, and sizing that declines rather than forces a trade.
