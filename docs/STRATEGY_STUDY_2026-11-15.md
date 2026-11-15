# Is there a profitable strategy on the predictions? — 15 Nov 2026

Hassan, 15 Nov: "devise the best most profitable strategy based on the
predictions. The buy ins can be YES or NO and can be multiple cities based on
accuracy and performance."

**The answer: not yet. No rule made money at the prices that were on the book.**
I replayed 158,650 candidate trades from 9,504 served calls (25 Sep -
14 Nov) against the recorded best ask and bid, with the taker fee, held to
settlement. Every rule family lost, and choosing cities by their record did
not help. One rule looked profitable on the development dates and did not
repeat on the held-out dates.

The numbers are rebuilt from committed files by `tools/strategy_study.py`
(`data/eval/strategy_study_2026-11-15/result.json`).

## 1. What was replayed

- **A trade** is one bucket and one side, bought at the touch and held to
  settlement, one dollar staked.
  - YES pays 1 if the bucket wins; the price is the best ask.
  - NO pays 1 if it loses; the price is 1 minus the best bid, because the
    venue's NO book mirrors the YES book. A bucket with no bid has no NO to buy.
  - The cost adds the taker fee, 0.05 x price x (1 - price).
- **The prediction** is the served ladder. At the morning call it is the
  corrected ladder, which that call prices from once
  `settings.station_same_day_pricing` is on. Noon and later are scored from 17
  Oct, when `wxrd3` began serving them.
- **Not modelled:** order size, depth beyond the touch, or selling before
  settlement. A real order of any size does no better than this replay.
- **Development and holdout.** Rules were compared on dates to 31 Oct. The
  holdout, 1-14 Nov, was read once, for the one rule left standing.
- Returns are per dollar staked. Intervals are 90%, resampling dates.

## 2. Betting where the model sees an edge loses

Buy any side whose model probability beats its cost by a threshold. On the
development dates I tried 132 variants with 80 or more trades (six checkpoints,
both sides, four thresholds, four price ranges). **None had an interval above
zero; 101 had one wholly below zero.** The best was -6.6% a dollar
(-8.5% to -4.8%; evening before, NO, threshold 0.03, 2,766 trades).

With the threshold at 0.06 (development dates):

| Checkpoint | YES trades | Return per dollar | 90% | NO trades | Return per dollar | 90% |
|---|---|---|---|---|---|---|
| Evening before | 1,573 | -45.7% | -53.0% to -38.2% | 2,257 | -6.9% | -9.5% to -4.4% |
| Morning | 1,097 | -38.1% | -49.1% to -26.3% | 1,468 | -8.5% | -12.9% to -3.8% |
| Noon | 507 | -35.8% | -52.7% to -19.8% | 578 | -15.5% | -20.9% to -10.8% |
| 2 h before peak | 515 | -11.5% | -36.3% to +11.7% | 599 | -12.2% | -17.1% to -7.7% |
| 1 h before peak | 466 | -34.2% | -50.5% to -16.9% | 512 | -18.7% | -28.8% to -6.3% |
| 1 h after peak | 227 | -46.4% | -70.3% to -21.1% | 253 | -57.0% | -71.0% to -42.7% |

When the model disagrees with the book, the book is usually right. That is the
audit's finding in money: the market's probabilities are more accurate than
the model's at every checkpoint.

## 3. The market alone, at the touch, also loses

Buy every bucket in a price range, whatever the model says (all dates):

| Price | YES trades | Return per dollar | NO trades | Return per dollar |
|---|---|---|---|---|
| 0.03 to 0.100 | 7,934 | -35.8% | 667 | -64.2% |
| 0.10 to 0.250 | 7,097 | -24.8% | 650 | -46.2% |
| 0.25 to 0.450 | 7,516 | -15.8% | 2,354 | -21.8% |
| 0.45 to 0.650 | 5,242 | -8.7% | 5,906 | -7.6% |
| 0.65 to 0.850 | 1,998 | -5.5% | 7,801 | -4.3% |
| 0.85 to 0.930 | 459 | -2.5% | 5,208 | -1.8% |
| 0.93 to 0.970 | 428 | +1.2% | 4,045 | -1.1% |
| 0.97 to 0.995 | 648 | -0.8% | 9,044 | -0.6% |

- A taker pays the spread and the fee on every trade. Nothing here is a free
  bias to lean on.
- The nearest to fair is the expensive end: YES at 0.93 to 0.97 returned
  +1.2% (-0.6% to +2.8%), not separable from zero.

## 4. Buying the model's top pick loses

YES on the top pick when it is offered between 0.20 and 0.90 (all dates):

| Checkpoint | Trades | Pick won | Return per dollar | 90% |
|---|---|---|---|---|
| Evening before | 2,061 | 42.8% | -6.6% | -11.4% to -1.7% |
| Morning | 1,604 | 42.2% | -13.4% | -17.6% to -8.9% |
| Noon | 1,036 | 52.3% | -10.0% | -14.9% to -5.2% |
| 2 h before peak | 1,094 | 51.4% | -8.4% | -13.7% to -2.9% |
| 1 h before peak | 974 | 54.4% | -8.4% | -12.9% to -4.0% |
| 1 h after peak | 252 | 59.1% | -10.9% | -20.8% to -1.4% |

## 5. Choosing cities by their record does not work

The top-pick rule, on every city; on the cities whose earlier trades had paid
(a city trades on a date only on its record before that date); on the ten best
so far; and on the ten best picked afterwards:

| Call | Every city | Cities that paid so far | Ten best so far | Ten best in hindsight |
|---|---|---|---|---|
| Evening before | -6.6% | -8.8% | -11.9% | +16.9% |
| Morning | -13.4% | -5.0% | +0.1% | +17.9% |
| Noon to 1 h before peak | -8.9% | -9.3% | -4.8% | +18.1% |

The last column is why a city list looks attractive: picked afterwards, the
best ten cities return about +17% to +18% a dollar. Picked beforehand, the
same idea loses. A city's past profit here is mostly luck, so it does not
carry forward.

## 6. The one rule that looked good, and its holdout

One hour after the peak, buy YES on the top pick when the model gives it 0.90
or more and it is still offered below 0.97.

| Dates | Trades | Dates traded | Won | Mean cost | Return per dollar | 90% |
|---|---|---|---|---|---|---|
| Development (17-31 Oct) | 55 | 13 | 94.5% | 0.891 | +6.7% | +1.5% to +11.9% |
| Holdout (1-14 Nov) | 25 | 11 | 92.0% | 0.908 | -1.2% | -11.5% to +7.8% |
| Both | 80 | 24 | 93.8% | 0.896 | +4.3% | -0.6% to +8.8% |

- It passed on the development dates and failed on the holdout. Over both it
  is +4.3%, with an interval that includes zero.
- It trades about three times a day across all cities, so it would take
  months of shadow trading to settle either way.

## 7. No riskless ladder trade

- Buying every bucket's YES never cost less than the payout of 1: the lowest
  was 1.0061 across 9,473 calls.
- Buying every bucket's NO cost less than its payout on 1 of 220 calls that
  had a bid on every bucket, by 0.0056.

## 8. What I recommend

1. **Put no capital on any rule here.** None has shown a profit out of sample.
   The paper record agrees: the trade log on main stands at 1,217 closed
   trades, net -$1,817.68.
2. **Fix accuracy first.** A rule can only pay when the model knows something
   the price does not. The audit's two measured gains (the same-day station
   price, and the station bias learned from the served forecasts) narrow the
   gap but do not close it.
3. **If you want one rule watched, make it the post-peak rule in section 6**,
   in shadow only, pre-registered with a fixed number of trades before anyone
   looks. It is the only rule with a positive point estimate.
4. **Do not pick cities by past profit.** Section 5 shows it does not carry
   forward.

## 9. Not tested here

- **Resting orders.** A maker pays no fee and may earn the spread a taker
  pays. That needs fills, not quotes. An earlier resting-order test on 5-9 Oct
  lost (-0.0966 a share, 65 fills; `docs/PLAN_PROGRESS.md`, H2).
- **Selling before settlement**, and sizing by confidence.
- **The NO book itself.** NO prices here come from the YES bid. Direct NO books
  have been recorded only since 12 Nov.
