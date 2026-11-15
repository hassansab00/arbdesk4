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
| Evening before | -6.6% | -7.6% | -10.1% | +16.9% |
| Morning | -13.4% | -1.1% | -0.4% | +17.9% |
| Noon to 1 h before peak | -8.9% | -9.5% | -7.0% | +18.1% |

"So far" is what had settled before the date's first call. The first version
of this table counted the day before as known, which it is not at the
evening-before call (Codex on #512). The corrected figures are above; the
earlier ones were -8.8%, -5.0%, -9.3% and -11.9%, +0.1%, -4.8%.

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

## 10. YES on the three best cities, NO on the three worst (added 15 Nov)

Hassan, 15 Nov: "bet YES on the top performing 3 cities, and NO on the least
performing". Read as: once a day, YES on the model's top bucket in the three
cities where that pick has been right most often, and NO on the model's top
bucket in the three where it has been right least often.
`tools/city_sides_study.py` rebuilds
`data/eval/city_sides_2026-11-15/result.json` from the same trades, prices and
fee as the rest of this study.

**Traded as it could have been.** Each date's cities are chosen on their picks
that had settled before that date's first call (8 or more), ranked by hit
rate:

| Call | YES trades | YES on the 3 best | NO trades | NO on the 3 worst | Both legs | 90% | Does a city's rank carry forward? |
|---|---|---|---|---|---|---|---|
| Evening before | 124 | -8.1% | 124 | -7.2% | -7.6% | -17.2% to +2.2% | +0.06 |
| Morning | 94 | +11.1% | 94 | -16.3% | -2.6% | -13.7% to +8.6% | -0.07 |
| Noon | 61 | -12.2% | 57 | -21.6% | -16.7% | -31.9% to -1.5% | +0.45 |
| 2 h before peak | 47 | -8.6% | 60 | -8.5% | -8.5% | -24.5% to +7.9% | +0.38 |
| 1 h before peak | 47 | -3.1% | 60 | -48.7% | -28.6% | -43.6% to -13.6% | +0.26 |
| 1 h after peak | 61 | +4.0% | 61 | -72.6% | -34.3% | -40.7% to -27.7% | -0.02 |

- **The last column** is the rank correlation of each city's hit rate between
  the earlier and the later half of the dates. Near zero means the best
  cities so far are no likelier to be the best cities next.
- **The NO leg never paid.** Across 24 tests (six calls, two ways of ranking
  the cities, two legs), the NO leg had a positive return in
  0 of 12.
- **No test had an interval above zero,** and 7 of the 24 had one wholly
  below it. The nearest was YES on the three best cities one hour after the
  peak: +4.0% (-0.7% to +9.3%, 61 trades), the effect `postpeak-v1` is
  testing.
- **Why NO loses even where the model is worst:** the price already allows
  for it. At the morning call the NO on the three worst cities cost 0.647
  on average and won 58.5% of the time.
- **Two corrections from Codex on #512,** both in these figures: a city's
  record counts only outcomes settled before the call (the evening-before
  call is made before the previous day settles), and the NO is bought
  wherever the pick had a bid, whether or not it also had an ask.

**The six cities the rule names today, at the morning call** (the whole
record to 14 Nov; each city's own numbers):

| City | Side | Picks | Model's pick right | Mean cost | Trade won | Return per dollar | 90% |
|---|---|---|---|---|---|---|---|
| nyc | YES | 40 | 75.0% | 0.595 | 75.0% | +25.8% | +1.4% to +48.2% |
| miami | YES | 40 | 72.5% | 0.576 | 72.5% | +30.2% | +5.8% to +52.6% |
| helsinki | YES | 40 | 65.0% | 0.628 | 65.0% | +3.3% | -20.1% to +26.9% |
| buenos_aires | NO | 40 | 22.5% | 0.695 | 77.5% | +14.3% | -3.5% to +31.6% |
| sao_paulo | NO | 40 | 22.5% | 0.776 | 77.5% | -2.9% | -16.8% to +10.4% |
| beijing | NO | 39 | 15.4% | 0.772 | 84.6% | +13.1% | -1.8% to +27.5% |

- **On these six, on the record that chose them** (hindsight, not a rule):
  both legs +14.0% (+7.2% to +20.8%).
- **The same rule traded forward at the morning call:** YES leg
  +11.1% (-5.2% to +28.8%), NO leg -16.3% (-28.2% to -4.2%), both legs
  -2.6% (-13.7% to +8.6%).
- **The gap between those two lines is the selection.** Cities picked on a
  record look good on that record.
- **For scale:** YES on every city's top pick at the morning call returns
  -15.9%.

**The evening-before call is the one Hassan trades** (15 Nov: "We're trading
evening before / night before the trade date"). The six cities the rule names
there, on the whole record to 14 Nov:

| City | Side | Picks | Model's pick right | Mean cost | Trade won | Return per dollar | 90% |
|---|---|---|---|---|---|---|---|
| amsterdam | YES | 49 | 57.1% | 0.501 | 57.1% | +21.3% | -6.3% to +50.3% |
| miami | YES | 49 | 57.1% | 0.494 | 57.1% | +3.7% | -18.8% to +25.7% |
| helsinki | YES | 51 | 54.9% | 0.499 | 54.9% | +15.3% | -16.7% to +51.2% |
| busan | NO | 50 | 26.0% | 0.667 | 74.0% | +11.9% | -6.4% to +28.6% |
| sao_paulo | NO | 49 | 24.5% | 0.812 | 75.5% | -9.6% | -23.6% to +2.8% |
| buenos_aires | NO | 49 | 22.4% | 0.713 | 77.5% | +9.7% | -6.0% to +24.1% |

- **On these six, on the record that chose them** (hindsight, not a rule):
  YES leg +13.5% (-3.2% to +30.1%), NO leg +4.1% (-5.7% to +13.1%),
  both legs +8.8% (-0.5% to +16.9%).
- **The same rule traded forward at the evening-before call:** YES leg
  -8.1% (-25.1% to +8.6%), NO leg -7.2% (-20.5% to +6.8%),
  both legs -7.6% (-17.2% to +2.2%), on 248 trades.
- **Ranking the cities by their past YES return instead of their hit rate:**
  both legs +1.8% (-11.3% to +16.7%). Not distinguishable from zero.
- **A city's rank does not carry forward at this call:** the rank correlation
  between halves of the record is +0.06 for hit rate and -0.02 for
  return.
- **For scale at this call:** YES on every city's top pick returns
  -10.5%, NO on every city's top pick -4.9%.

## 11. The evening-before call, where Hassan trades (added 15 Nov)

Hassan, 15 Nov: "We're trading evening before / night before the trade date",
and that his own test of the three-and-three rule was "up +98% supposedly"
over 12-14 Nov. `tools/evening_rules_study.py` rebuilds
`data/eval/evening_rules_2026-11-15/result.json` from the 2,336 first
evening-before calls with a graded outcome and a recorded book (25 Sep - 14
Nov).

**Whether the market agrees with the model is what matters at this call.** The
market's favourite is the bucket the book prices highest. The model's pick is
the same bucket on 53.6% of calls.

| | Calls | Model's pick right | Market's favourite right |
|---|---|---|---|
| They agree | 1,252 | 50.6% | 50.6% |
| They disagree | 1,084 | 25.1% | 46.3% |

**Nine rules on that agreement.** All nine were written before any was
scored, and all were scored together; each is shown on the dates to 31 Oct and
on 1-14 Nov:

| Rule | Trades | Return per dollar | 90% | To 31 Oct | 1-14 Nov |
|---|---|---|---|---|---|
| They agree: YES on the shared pick | 1,252 | -3.9% | -9.0% to +1.0% | -3.8% | -4.3% |
| They disagree: YES on the market's favourite | 1,084 | -3.0% | -8.9% to +3.1% | -3.3% | -2.2% |
| They disagree: NO on the model's pick | 1,083 | -1.4% | -5.0% to +1.8% | -2.0% | +0.1% |
| They disagree: YES on the model's pick | 1,084 | -18.1% | -26.4% to -8.6% | -17.3% | -20.1% |
| They disagree: NO on the market's favourite | 1,083 | -5.6% | -10.9% to -0.3% | -4.8% | -7.5% |
| The model gives its pick 50% or more: YES | 77 | -2.9% | -25.4% to +16.7% | -6.8% | +6.7% |
| They agree and the model gives 50% or more: YES | 53 | +12.1% | -6.8% to +30.1% | +11.6% | +13.4% |
| YES on every market favourite | 2,336 | -3.5% | -7.2% to +0.5% | -3.6% | -3.3% |
| They agree: NO on the shared pick | 1,251 | -7.8% | -12.7% to -3.0% | -7.1% | -9.7% |

- **Do not buy YES on the model's pick when the market disagrees.** It lost
  -18.1% over 1,084 trades, with an interval wholly below zero in both
  halves.
- **One rule has a positive estimate:** YES when they agree and the model
  gives the bucket 50% or more, +12.1% over 53 trades. Its interval includes
  zero, it is the best of nine, and it fires 1.04 times a calendar day. At
  the return and spread it has shown it would need 285 trades for 80% power,
  about 275 days.
- **Every other rule sits between about -1% and -8%.** That is the cost of
  taking the book, below.

**The market is fairly priced the evening before; a taker starts behind.** On
the 2,334 calls where the favourite had both a bid and an ask:

| The market's favourite, YES | Return per dollar | 90% |
|---|---|---|
| At the midpoint, no fee | +1.9% | -2.0% to +6.0% |
| At the ask, with the fee | -3.4% | -7.1% to +0.5% |

The mean spread is 0.0258 and the mean fee 0.0120 a share.

**The three-and-three rule on 12-14 Nov, and how far three dates can swing.**
The rule of section 10 at this call, cities chosen on outcomes settled before
each call:

| Cities ranked by | 12-14 Nov trades | Won | Return per dollar | Lowest of 40 three-date runs | Median | Highest | Runs at +98% or more |
|---|---|---|---|---|---|---|---|
| Hit rate | 14 | 9 | +3.6% | -54.5% | -9.3% | +35.6% | 0 |
| Past YES return | 13 | 6 | -23.9% | -53.4% | -2.0% | +104.5% | 1 |

- **+98% over those three dates is not reproduced here.** The trades behind
  that figure were not available to check.
- **One trade swings by about its whole stake** (standard deviation 1.02 of a
  dollar), so three dates of this rule say little either way.

## 12. Hassan's Codex test, 12-14 Nov, checked trade by trade (added 15 Nov)

Hassan, 15 Nov: "im testing the 3 yes 3 no strategy in codex, from nov 12-14
its up +98% supposedly", with the 18 trades Codex listed
(`data/eval/codex_test_2026-11-12/trades.json`, as pasted).
`tools/codex_trade_check.py` checks them against the recorded book and the
venue's outcomes and rebuilds `data/eval/codex_test_2026-11-12/result.json`.
**Section 11 said the +98% was not reproduced. That was my own build of the
rule, with its own choice of cities, not these trades. With the list it is
reproduced (below), and these are the reasons it is not yet evidence.**

**What Hassan told me about the test, 15 Nov.** "Selection is night time
beirut time 23:30, frozen prediction forecasts for the next day." and "Theres
no ranking its just what historically was accurate and what wasnt." The first
is 21:30Z in November; both are used below.

**The listed trades check out.**

- **Prices.** 14 of the 15 listed prices are the recorded evening-before book
  (YES at the ask, NO at one less the bid), within three and a half cents.
  The other, Shanghai on 14 Nov at 80 cents, is the 14 Nov morning call's
  book; the evening-before ask was 0.52.
- **Outcomes.** The listed settled bucket is the venue's winner on
  15 of 15 trades the venue has settled. Three on 14 Nov (Austin, Buenos
  Aires, Miami) are not settled in our record.
- **Arithmetic.** Every listed share count and P&L follows from its stake and
  price (0 off by more than a cent). The list has no fee; the taker fee on
  these trades would be $1.38.
- **Staking.** Each day six trades of the equity divided by 7.5, from $30: that
  gives the listed stakes 4.00, 5.56 and 8.34. So 80% of the equity is
  staked every day and the result compounds. That convention adds a whole
  day's profit before the next day's stakes, but the next evening-before
  orders are placed before that day settles (Codex on #515: Shanghai's 13 Nov
  order was placed at 10:37Z on 12 Nov, before its 12 Nov position settled at
  16:37Z). A $30 account could not fund them all; see the settlement-aware
  replay below.

**What it returns, on the recorded evening-before book with the fee,
and what moves it,** under the list's own staking (each day's profit added
before the next day's stakes). P&L by day, then the equity from $30:

| Basis | P&L by day (12 / 13 / 14 Nov) | Equity | On $30 | Per dollar staked |
|---|---|---|---|---|
| Recorded evening-before book, with the fee | +11.16 / +19.56 / +6.78 | $67.51 | +125.0% | +35.5% |
| The list's own prices where it gives one, fee added | +10.78 / +19.42 / +1.59 | $61.78 | +105.9% | +30.3% |
| Shanghai on 14 Nov at its listed 80 cents | +11.16 / +19.56 / +1.60 | $62.32 | +107.7% | +30.6% |
| Buenos Aires on 14 Nov as a loss | +11.16 / +19.56 / -5.28 | $55.45 | +84.8% | +24.1% |
| Without the best trade | -2.34 / +13.15 / +4.56 | $45.37 | +51.2% | +20.5% |
| The two trades whose bucket is not the model's pick, on the model's pick | +11.16 / +19.56 / -21.31 | $39.41 | +31.4% | +8.9% |

- **Settlement-aware, the same trades.** Orders and settlements in time order,
  each order the realised equity divided by 7.5 and no more than the cash in
  hand: $61.72, +105.7% on $30. 3 of the 18 orders are cut to the cash
  in hand and one (Buenos Aires on 14 Nov) cannot be placed, so its outcome
  does not matter. The account is out of cash on 14 Nov.
- **The list's own P&L as written** is +11.73 on 12 Nov and +20.85 on 13 Nov,
  which is $30 to $62.58 after two days. Three of the six trades on 14 Nov
  have no listed price or P&L, so I cannot reproduce its exact +98%; the
  scenarios above bracket it at +85% to +125%.
- **One trade is 36.0% of the profit:** Shanghai YES on 12 Nov at 22 cents.
- **Two trades carry 14 Nov.** Helsinki (2 C) and Warsaw NO (7 C) are not the
  model's pick at the evening call: its pick was 3-4 C (0.438 against 0.426
  on 2 C) and 6 C (0.303 against 0.298 on 7 C). In both, Codex's bucket is the
  market's favourite and won. The other 16 of 18 are the model's pick. On the
  model's pick those two lose, and the three days return +31.4%, not
  +125.0%. How Codex chose those two buckets is the question to answer.
- **Three outcomes on 14 Nov are provisional.** Buenos Aires is the least
  certain: the list has 29 C, our last call that day had 28 C at 0.67.

**What existed at 23:30 Beirut.** Each city's evening-before call is made at
its own 18:00, so the calls of Austin, Buenos Aires, Houston, Miami are made after
21:30Z (Buenos Aires by 7 minutes, Miami by about 2 hours, Houston and Austin by
about 3 hours). 10 of the 18 listed trades use such a call. The 8 with a listed price
match that later book, and for Buenos Aires and Houston on 13 Nov the listed
bucket is the model's pick on the later call but not on the day-ahead call.
So the run, or at least its inputs, came after 23:30 Beirut; a run at 23:30
would have had those cities' day-ahead call (made at 09:00 their time). The
same trades on the latest call made by 21:30Z, the day-ahead book standing in
for the live one:

| Bucket | Orders | Won | Day-end staking | Settlement-aware, all orders at 23:30 Beirut | Per dollar staked (day-end) |
|---|---|---|---|---|---|
| The listed buckets | 18 | 14 | $70.53, +135.1% | $49.60, +65.3%, 5 orders cut to the cash | +37.6% |
| The model's pick at that call | 18 | 12 | $40.66, +35.5% | $41.04, +36.8%, 5 orders cut to the cash | +9.9% |

- **4 of the 18 listed buckets are not the model's pick at the call available then:**
  Buenos Aires and Houston on 13 Nov (the pick only on the later call), and
  Helsinki and Warsaw on 14 Nov (not the pick on any call). On the model's pick
  the three days still return a profit (+35.5% on day-end staking, +36.8%
  settlement-aware) against +135.1% and +65.3% on the listed buckets, and 14 Nov
  loses (-22.26 on day-end staking).
- **Prices here are the call's book, not the live book at 23:30.** Only the
  calls are recorded, so for a city whose call was hours earlier the price may
  have moved.

**The live book in your recording window.** Hassan, 15 Nov: "Codex is
selecting the 6 cities and im recording them 11 to 2am beirut time", which is
21:00Z to 00:00Z. The listed prices are the evening-before call's frozen
book, made at each city's own 18:00: Shanghai's at 10:37Z, ten hours before
the window opens, Buenos Aires', Miami's, Houston's and Austin's inside it or
just after. `live_capture` holds the live top of book every hour (at :36).
The same 18 trades at each capture inside the window (the 3 Nov 14 NO trades,
unpriced in the list, are priced here too):

| Capture | Orders | Day-end staking, from $30 | Per dollar staked (day-end) | Settlement-aware | Touch smaller than a $4 order |
|---|---|---|---|---|---|
| 21:36Z (23:36 Beirut) | 18 | $47.61, +58.7% | +20.4% | $30.70, +2.4%, 5 orders cut | 2 |
| 22:36Z (00:36 Beirut) | 18 | $47.79, +59.3% | +20.2% | $30.76, +2.5%, 6 orders cut | 4 |
| 23:36Z (01:36 Beirut) | 18 | $45.69, +52.3% | +18.7% | $31.31, +4.4%, 4 orders cut | 3 |

- **The list's prices give +125.0% on $30 and +35.5% per dollar staked**
  (day-end staking, on the evening call's books). At the live book of the
  window it is about +18.7% to +20.4% per dollar staked.
- **Shanghai on 12 Nov**, the trade behind 36% of the profit, is listed at
  0.22; its ask at the three captures was 0.45, 0.41 and 0.48. The ask rose
  between the evening call and your window; that bucket won.
- **The largest gaps between a listed price and the window's book:** Shanghai 11-12 YES: listed 0.22, in the window 0.41 to 0.48; Helsinki 11-13 YES: listed 0.60, in the window 0.66 to 0.73; Buenos Aires 11-13 NO: listed 0.86, in the window 0.87 to 0.91.
- **The settlement-aware account is nearly flat.** A $30 account that stakes
  80% of its equity each evening has almost no cash left for the next evening
  until positions settle (the first evening's positions settle between 16:37Z
  on their target day and 07:36Z the day after), so most later orders are cut. Per dollar
  staked is the better measure of the picks.
- **The touch is thin.** Several touches are as small as 5 shares, below what
  a $4 order at that price needs, so a real order would walk the book. The
  capture also holds the dollar depth within a few cents of the touch.

**The rule: "what historically was accurate".** On each date the cities are
ranked on their evening-before picks that had settled by 21:30Z, by hit rate
(at least 3 picks); YES on the three best, NO on the three worst. I cannot see
Codex's exact definition, so this tests the rule as described, at the
evening-before call, one dollar a trade at the touch with the fee, over all
47 dates it can trade (2026-09-29 to 2026-11-14):

| Hit rate over the last | Trades | Both legs | 90% | YES leg | NO leg |
|---|---|---|---|---|---|
| 5 days | 280 | +6.8% | -2.9% to +16.5% | +8.7% | +5.0% |
| 7 days | 281 | +3.9% | -7.7% to +15.9% | +1.5% | +6.3% |
| 10 days | 279 | -3.5% | -12.5% to +6.0% | -5.7% | -1.3% |
| 14 days | 280 | -12.0% | -20.5% to -3.9% | -20.2% | -3.7% |
| 21 days | 280 | -5.2% | -14.5% to +4.0% | -1.2% | -9.3% |
| 28 days | 279 | -7.6% | -17.8% to +2.8% | -9.0% | -6.3% |
| All history | 278 | -8.9% | -18.1% to +0.8% | -10.7% | -7.2% |

- **"All history" is the rule as Hassan describes it:** -8.9% a dollar over 278 trades
  (-18.1% to +0.8%). No window is separable from zero in the positive direction;
  14 days is wholly below it. I tried seven windows, so one clear winner
  would not have meant much either.
- **It does not reproduce the six cities.** On each day the six cities sit in
  the all-history table at the positions below (of 47), so some are mid-table
  where an accuracy ranking would not pick them. A ten-day window comes
  closest: all of them within the top and bottom seven, and the table's own
  top and bottom three hold 12 of the 18 assignments. If Codex's "historical
  accuracy" comes from another record (a longer one, or the platform's own
  hit-and-miss page), I need its source to test it.
  All history, 11-12: YES at 1, 2, 27 from the best, NO at 1, 9, 27 from the worst; 11-13: YES at 1, 3, 20 from the best, NO at 1, 7, 20 from the worst; 11-14: YES at 2, 3, 19 from the best, NO at 1, 18, 22 from the worst.
- **Ten days, with every tie broken at random 200 times:** both legs
  -1.8% on average (-5.2% to +1.9%); 20.5% of tie-breaks end above zero.
- **12-14 Nov is the best run of three dates in the record.** Of 45 runs of
  three consecutive dates, +40.8% (rank 1, 15 trades) is the highest; the
  median is -1.7% and 47% of runs are positive.
- **How long a test takes.** The spread of a date's mean return is 0.38. For 80%
  power to put the rule above zero: 8 dates if its true return is +35% a dollar,
  23 dates at +20%, 90 dates at +10%.

## 13. Hassan's six frozen cities: is it profitable, and the profit to date (added 15 Nov)

Hassan, 15 Nov: "You and codex are giving me different results and answers. I
need to fucking know. Is this strategy profitable. And how much profit did we
make to date. We will pick 6 cities from now on and freeze and trade on them
... BUY YES on predicted bucket: Shanghai, Helsinki, Amsterdam. BUY NO on
predicted bucket: Houston, Austin, Sao Paulo." He records the trades 23:00-02:00
Beirut. `tools/frozen6_study.py` rebuilds `data/eval/frozen6_2026-11-15/result.json`.

**The account is $30, six trades a day of equity / 7.5 (Hassan's experiment).**
An earlier version of this section put $100 on every trade; that was my
mistake, not his rule, and every dollar figure below is the $30 account.
Taker fee on the price paid, held to settlement, outcomes the venue's (the
list's for the three not settled in our record). Two ways to run the account:
"as the list stakes" adds each day's profit before the next day's stakes (what
the Codex list does); "only settled cash" gives each order no more than the cash
that has settled by its time (the next evening's orders go in before the day
settles, so a $30 account cannot fund them all).

**Profit to date: the 18 trades of the Codex test, 12-14 Nov, from $30.**

| Priced at | As the list stakes | Profit | Per dollar staked | Only settled cash | Profit |
|---|---|---|---|---|---|
| The evening call's frozen book (as listed) | $67.51, +125.0% | +$37.51 | +35.5% | $61.72, +105.7% | +$31.72 |
| The list's own prices where given | $61.78, +105.9% | +$31.78 | +30.3% | not run | |
| Live book 21:36Z (23:36 Beirut) | $47.61, +58.7% | +$17.61 | +20.4% | $30.70, +2.4% | +$0.70 |
| Live book 22:36Z (00:36 Beirut) | $47.79, +59.3% | +$17.79 | +20.2% | $30.76, +2.5% | +$0.76 |
| Live book 23:36Z (01:36 Beirut) | $45.69, +52.3% | +$15.69 | +18.7% | $31.31, +4.4% | +$1.31 |

These are the same figures as section 12 (`tools/codex_trade_check.py`).
At the prices in your recording window the three days made +$15.69 to +$17.79 as
the list stakes, and +$0.70 to +$1.31 on cash that had settled. Three days, 18
trades, and section 12 found 12-14 Nov to be the best run of three dates in the
record for the Codex rule.

**Is it profitable? Not shown.** The six frozen cities, the rule applied to
every evening with a graded outcome. Per dollar staked (the measure that does
not depend on the staking), and the $30 account run through the same trades
as the list stakes it:

| Priced at | Dates | Trades | Per dollar | 90% | $30 account | Same rule on every city, per dollar |
|---|---|---|---|---|---|---|
| The evening call's frozen book | 51 | 297 | +5.0% | -5.1% to +14.3% | $8.81, -70.6% | -7.7% |
| Live book 21:36Z (23:36 Beirut) | 33 | 194 | +1.4% | -10.5% to +12.5% | $4.48, -85.1% | -8.6% |
| Live book 22:36Z (00:36 Beirut) | 33 | 194 | -0.4% | -12.7% to +10.6% | $2.70, -91.0% | -9.6% |
| Live book 23:36Z (01:36 Beirut) | 33 | 194 | -2.0% | -13.7% to +9.2% | $1.87, -93.8% | -9.8% |

- **Per dollar, at the prices available in your window the six cities are
  break-even over their record:** +1.4%, -0.4% and -2.0%, each with an interval
  of about plus or minus 12 points. The frozen evening-call book (hours before
  your window for Shanghai, Helsinki and Amsterdam) gives +5.0%, and its
  interval also includes zero.
- **The $30 account run through the whole record does not survive.** Staking 80%
  of the equity a day, at the evening call's book the account ends at $8.81.
  The average day grew the equity by +3.9%, but 8 of 51 dates took 30% or more
  of it (the worst, all six trades lost, took 80%), and compounding a daily
  loss of that size costs more than the average day adds. On cash that had
  settled, every one of the four runs ends at $0.00 to the cent. This is one
  path of 33 to 51 dates; it says the stake rule is too large for these
  swings, not that the picks are worthless.
- **They do beat the average city** (-9.6% a dollar at the same prices, the same
  rule on all cities) **by about 8 to 10 points, but they were chosen on this
  record.** A city's accuracy rank does not carry into the next dates at this
  call (rank
  correlation +0.06), so the average city is the fair expectation until these six
  prove otherwise.
- **The record is lumpy.** At the evening call the six returned -7.1% per dollar to
  24 Oct and +22.4% after. Since 12 Nov (14 graded trades) they returned +35.6%
  at the 22:36Z book.

By city, per dollar, over each city's dates:

| City | Side | Call book: trades | Call book | 22:36Z book: trades | 22:36Z book |
|---|---|---|---|---|---|
| Amsterdam | YES | 49 | +21.3% | 32 | +9.7% |
| Helsinki | YES | 51 | +15.3% | 33 | -14.5% |
| Shanghai | YES | 50 | -3.5% | 33 | -8.2% |
| Austin | NO | 49 | -5.9% | 32 | +3.3% |
| Houston | NO | 49 | +12.2% | 32 | +22.8% |
| Sao Paulo | NO | 49 | -9.6% | 32 | -15.0% |

- **Three of the six lost money at the live book over the record** (Helsinki,
  Shanghai, Sao Paulo); Houston and Amsterdam carry it.
- **Houston's and Austin's evening forecasts are made at 00:37Z, 02:37 Beirut,
  after your window ends.** Sao Paulo's is at 21:37Z, 23:37 Beirut.
- **The touch is thin:** 33 of the 194 trades at the 22:36Z book have a
  touch smaller than a $4 order needs.
- **From 16 Nov** the forward ledger is `python tools/frozen6_study.py --ledger`
  (`docs/FROZEN6_PREREG.md`).
