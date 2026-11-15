# The post-peak rule in shadow (`postpeak-v1`): pre-registration

**Written 15 Nov 2026, before any forward call of this rule was read.**
Hassan, 15 Nov, on the strategy study's recommendation to watch this one rule
in shadow: "Yes do whats recommended and necessary".

The question: one hour after the day's peak, when the model is nearly sure of
a bucket and the book still offers it below 0.97, does buying that YES make
money after the fee?

It is scored as research, in shadow. No order is placed, and capital is
Hassan's (Rule 6). The rule's code and the evaluator are
`tools/postpeak_forward.py`; `tests/test_postpeak_forward.py` holds both to
this file. Every number below is from
`data/eval/postpeak/design_2026-11-15.json`, which
`python tools/postpeak_forward.py --design data/eval/postpeak` rebuilds from
the mirror.

## Where the rule comes from (development data, not evidence)

`docs/STRATEGY_STUDY_2026-11-15.md` tried 132 edge rules, the market alone, the
top pick and city selection. All lost or could not be told from zero. This was
the only rule with a positive point estimate. The dates below chose it, so
they flatter it.

On the study's dates, 17 Oct - 14 Nov (29 calendar days, 1,345 first post-peak
calls):

| | Trades | Dates | Won | Mean cost | Return per dollar | 90% |
|---|---|---|---|---|---|---|
| The rule | 80 | 24 | 93.8% | 0.896 | +4.3% | -0.6% to +8.8% |
| Without its best date (21 Oct) | 73 | 23 | 93.2% | 0.899 | +2.4% | -2.2% to +6.8% |
| Asks of 0.50 or more only | 76 | 24 | 96.1% | 0.928 | +3.8% | -0.4% to +7.7% |
| Control: the same call, model below 0.90 | 482 | 29 | 63.5% | 0.662 | -13.8% | -22.3% to -5.5% |

- **It split by half.** +6.7% on the 55 trades to 31 Oct, -1.2% on the 25
  trades of 1-14 Nov (the study's holdout).
- **It lost 5 of 80.** Two of the five were at asks of 0.001 and 0.29, where
  the model said 0.97 and 0.93 and the book disagreed outright.
- **Of the 1,345 calls:** 695 had a top pick below 0.90, 546 were offered at
  0.97 or more, 24 had no ask, and 80 traded. Every trade could be graded.
- **The model said 0.942 on average and won 93.8%.** The edge, if there is
  one, is in the price, not in the model being surer than it should be.

## Hypothesis

At the first post-peak call of a city and day, buying the YES of the model's
top bucket at the recorded ask, when the model gives it 0.90 or more and the
ask is below 0.97, returns more than it costs after the taker fee.

## The rule, `postpeak-v1` (nothing else changes)

- **The call.** The first `postpeak_1h` row of a city and target date in
  `prediction_checkpoints`: the earliest `(decided_at, checkpoint_id)`.
- **The bucket.** The served ladder's top bucket (highest probability, the
  smallest bucket id on a tie).
- **The condition.** Its probability is **0.90** or more, and its best ask on
  the book recorded with that call (`prediction_checkpoints.market`) is above
  0 and below **0.97**.
- **The trade.** One dollar of YES at that ask plus the taker fee,
  0.05 x price x (1 - price) a share (`scripts/cost_model.py`), held to
  settlement. One trade at most per city and day.
- **The return.** Win / cost - 1 per trade.
- **The model is whatever was served.** On the study's dates that was wxrd3 on
  75 trades and the forecast path on 5. If the served model changes during the
  test, the result is reported by model beside the whole; the rule is not
  restarted.

**Rule 11.** Nothing is learned during the test. Both thresholds, the fee and
the look counts are fixed here. The thresholds were set on dates to 31 Oct and
the rule is scored from 16 Nov. A change to any of them is `postpeak-v2`,
scored only on dates after it is committed. It is never an edit to this file.

## Dates

- **Forward dates:** target dates from **16 Nov 2026**
  (`postpeak_forward.FIRST_FORWARD_DATE`).
- **15 Nov is neither development nor forward.** Some of its calls were made
  before this file was written. None was read.
- **A graded trade:** its call has a settled outcome in
  `fact_checkpoint_outcome` with a winner that is on the served ladder.

## Scores (fixed now)

**Primary: the return per dollar staked, one dollar a trade,** over the graded
trades. The 90% interval comes from resampling whole target dates (seed 11,
1,000 resamples), as in the study (`strategy_study.result`).

**Beside it, never used to choose a rule:**
- the result without its best date, and on asks of 0.50 or more only;
- by the model that served the call;
- the control: the same call and price limit where the model gave its top
  pick less than 0.90;
- the win rate beside the model's mean probability;
- every call left out, by reason, and every trade that could not be graded;
- the result with every ungraded trade counted as a loss.

## When it is looked at

The spread of the 80 study trades by date is a standard error of 2.84 points.
At that spread, 80% power to put a 90% interval above zero for a true return
of +4.3% needs 218.2 trades, rounded up to 220.

- **First look: 110 graded trades.** It can only stop the test. No acceptance
  is possible at it.
- **Last look: 220 graded trades.** The test ends there whatever it reads.
- **Each look reads whole dates:** every trade whose target date is no later
  than the first date by which that many graded trades exist. A look waits
  while a trade on those dates is unsettled and younger than 7 days.
- **Before a look, only counts are read**
  (`python tools/postpeak_forward.py --count`): calls, trades, how many are
  graded. No win, price or return.
- **At the study's rate** (2.76 trades a calendar day) the first look falls
  about 40 days after 16 Nov and the last about 80 days after it. That is not
  a deadline: the looks are counts of trades, not dates.

## Acceptance rule

**First look (110 trades).**
- **Failed, and the test stops:** the return per dollar is 0 or less.
- **Otherwise** it continues. Nothing else is concluded.

**Last look (220 trades).**
- **Positive research evidence:** the 90% interval is wholly above 0, **and**
  the return without the best date is still above 0.
- **Failed:** the 90% interval is wholly below 0, or the return is 0 or less.
- **Insufficient evidence:** anything else.

**What a verdict does.** Nothing changes by itself.
- Positive evidence is a proposal to Hassan. It still needs the size on offer
  at those asks, which is not measured (below). Any allocation is his.
- A failure or an insufficient result closes `postpeak-v1`. It is recorded
  here and in `docs/PLAN_PROGRESS.md`.

## Known limits, stated before the data

- **The size at the ask is not measured.** `prediction_checkpoints.market`
  records each bucket's best ask, best bid and last price, and no size. One
  dollar a trade assumes at least that much was offered. A real order of any
  size does no better than this result.
- **One trade can swing the result.** A dollar at an ask of 0.001 that won
  would return about 950 times its stake. The study's one such trade lost. The
  last look's second condition (still positive without the best date) is there
  for that, and the result on asks of 0.50 or more is reported beside it.
- **Latency is not modelled.** The book is the one recorded with the call. An
  order placed after the call would meet a later book.
- **The development dates flatter the rule,** and the +4.3% the trade count
  was sized on is their estimate. If the true return is smaller, 220 trades
  will most likely read "insufficient evidence". That closes the rule; it does
  not show the rule is worthless.
- **The served model may change** (a refit, a promotion, the same-day station
  price on the forecast path). The rule is on what was served.
- **Dates are not independent across cities:** one weather system or one venue
  outage touches many cities. The interval resamples whole dates for that
  reason.

## Result

(appended below after each look; nothing above changes)
