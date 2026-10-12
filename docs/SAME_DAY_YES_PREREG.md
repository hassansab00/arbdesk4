# The same-day YES pilot (`sdyes-v1`), Jeddah and Istanbul: pre-registration

**Written 12 Oct 2026, before any forward decision of this candidate existed.**

The question is section 5 of the Codex action plan of 11 Oct: does newly available weather information
make a bucket more likely to be the day's final maximum than its executable YES ask says? The pilot
is Jeddah, with Istanbul for comparison. The combined rule is the one judged; each city is reported
beside it. It is scored as research, in shadow: no order is placed, and capital is Hassan's (Rule 6).

The rule's code is `tools/wxpredict/sdyes.py`. The frozen model is
`data/models/sdyes/sdyes-v1_remaining.txt.gz`. `tests/test_wxpredict_sdyes.py` holds both to this
file. The ledger entry is `sdyes_v1_same_day_yes` in `data/eval/wxpredict/exposure_ledger.json`.

## Where the candidate comes from (development data, not evidence)

`tools/wxpredict/sdyes_dev.py` was written before it was run. It names its choice rule in advance:
among the two candidates and THETA 0.02, 0.05, 0.10, 0.15 and 0.20, take the pair with the highest mean
net profit per entry date, with at least 20 entry dates. If no pair is positive, nothing is frozen.

It ran on the walk-forward dump with the remaining-day model (`walkforward.py --remaining --dump`,
12 Oct table; every probability out of sample) against the archived books. The window was:
- Jeddah and Istanbul, the event day, decisions 10:00-16:00 local, at the table's instants;
- event dates up to 9 Oct (the ledger's `untouched_from` is 10 Oct). The books cover
  **13 Sep - 8 Oct: 26 Jeddah and 25 Istanbul city-days, 578 tradeable books with a decision**.

Results are $10 tickets, every level priced with the taker fee, and one entry per city-day held to
settlement. The full result, with the 95% intervals and the touch stress, is
`data/eval/wxpredict/sdyes_dev_2026-10-12.json`.

| who | THETA | entries | entry dates | won | mean p | cost a share | net $ | $ per entry date (90%) |
|---|---|---|---|---|---|---|---|---|
| remaining-day model | 0.02 | 48 | 26 | 0.17 | 0.39 | 0.293 | -226.89 | -8.73 (-12.98 to -3.92) |
| remaining-day model | 0.05 | 43 | 24 | 0.14 | 0.42 | 0.291 | -233.93 | -9.75 (-14.09 to -4.67) |
| **remaining-day model** | **0.10** | **41** | **24** | **0.17** | **0.43** | **0.258** | **+52.19** | **+2.17 (-8.49 to +15.69)** |
| remaining-day model | 0.15 | 32 | 21 | 0.16 | 0.45 | 0.228 | +27.78 | +1.32 (-8.18 to +15.17) |
| remaining-day model | 0.20 | 23 | 15 | 0.22 | 0.50 | 0.230 | +70.21 | +4.68 (-8.89 to +24.38) |
| fusion with the market | 0.02 | 31 | 22 | 0.29 | 0.40 | 0.328 | -41.35 | -1.88 (-5.80 to +2.56) |
| fusion with the market | 0.10 | 16 | 15 | 0.00 | 0.32 | 0.167 | -92.03 | -6.14 (-8.45 to -4.02) |
| market-only (control) | 0.10 | 17 | 16 | 0.00 | 0.32 | 0.153 | -102.68 | -6.42 (-8.56 to -4.53) |
| market tempered (control) | 0.10 | 16 | 15 | 0.00 | 0.32 | 0.156 | -98.23 | -6.55 (-8.79 to -4.57) |
| rd1, S10 (incumbent, own checkpoints) | 0.10 | 19 | 11 | 0.11 | 0.31 | 0.109 | +32.76 | +2.98 (-12.63 to +25.67) |

**The rule chose the remaining-day model alone at THETA 0.10.** It was the only candidate with a
positive mean over at least 20 entry dates, but the evidence for it is weak:
- Its +$52.19 comes from cheap winning buckets. Without its best date it is **-$97.34**; without its
  better city (Jeddah, +$95.49) it is **-$43.31**.
- One share at the touch of the same entries made **-0.0777** on average.
- At THETA 0.02 and 0.05 the same model **lost with intervals wholly below zero**.
- On its entries it said 0.43 on average, and the bucket won **0.17** of the time.

**The diagnostic behind that (dev, read once, no choice made on it).** Over Jeddah's and Istanbul's
decisions 10-16h, 1 May - 9 Oct (1,964 decisions, 152 dates, the same dump):
- **Calibration.** Both the model and the market's own price are well calibrated in every
  probability band.
- **Log loss on the winner.** The market's is lower: 0.7465 against the model's 0.8373.
- **Where they disagree.** On the 1,218 bucket-decisions where the model is at least 0.10 above the
  market, the model said 0.473, the market 0.247, and the bucket won **0.295** of the time. The market
  was nearer.

**What is expected.** These dates chose the candidate, so they flatter it. The development evidence
leans against it. The forward test is run because the rule fixed in advance says so, and because it
costs no capital. It also yields the pilot's first forward ledger, recorded at the minute.

## Hypothesis

At the pilot's decisions, buying the YES of the bucket that `sdyes-v1` chooses, at the executable asks
after the taker fee, makes money held to settlement.

## The candidate, `sdyes-v1` (nothing else changes)

**The model.** The remaining-day model (`tools/wxpredict/remaining.py`, phase 3A, `INC_FROM_HOUR` 9,
K 16), fitted once by `tools/wxpredict/sdyes_fit.py`:
- trained on every eligible table row (all 47 cities, event dates 15 Jul 2025 - 11 Oct 2026,
  318,607 rows);
- from the 12 Oct table (gzip sha256 `c0f9bb312c9a…`), with `walkforward.SEED` 20261011, the
  deterministic parameters and lightgbm 4.7.0;
- saved as `data/models/sdyes/sdyes-v1_remaining.txt.gz`. The model text's sha256 is
  `f6f72d55e83f0fb45677a34677cb6e367c67afa9e3525b0055936d37eb0f2e0e`, and a second fit produced the
  identical file.

Each bucket's probability is `remaining.bucket_probs` over the event's ladder, from the running
maximum at the decision.

**The decisions.** For each event date D from **13 Oct 2026**, in each city, decisions are made at the
training table's instants of the event day: local H:00 plus 60 s, for H = 10 to 16.
- Each decision's row is made by `tools/wxpredict/live_reader.py` in **`arrival`** mode. That is the
  live record, as the hourly tick captured it by t, under the training's own rules (a report 20 min
  after its reading, the forecasts' publication allowance).
- The row is made by `features.decision_row`, the function the table is built with, so the model
  sees the inputs it was trained on.
- The minute reports (`focus_obs_reports`) are recorded but not used by v1.

**The books.** Each decision is judged on the books of the first error-free books poll answered from
**t + 60 s** to **t + 300 s**:
- the poll is a `focus_obs_polls` row with source `clob_books`, status 200 and no error;
- each bucket's book is its newest `focus_book` row captured at or before that answer;
- without such a poll the decision is skipped: **no fresh book**.

**The entry.** The rule is `sdyes.walk_yes` and `sdyes.choose`:
- **Which levels.** YES only. Walk the bucket's asks (the 10 recorded levels) for a ticket of
  **$10**, buying a level while p - (price + taker fee at that price) >= **THETA 0.10**. Never go past
  25 cents from the touch; the touch itself must qualify.
- **Minimum order.** The book's own `min_order_size`; 5 shares where it has none.
- **One entry per city-day.** The first decision at which any bucket qualifies. At that decision,
  take the bucket with the largest edge (p minus cost a share), the lowest bucket on a tie.
- **Settlement.** Held to settlement on the venue's winning bucket. The fee is the platform's cost
  model, 0.05 · p · (1 - p) a share.

**The controls, on the same decisions, books and rule:**
- **market-only:** each bucket's price from the same books (`live_reader.live_price`: the midpoint,
  an empty bid as 0 and an empty ask as 1), normalised over the ladder;
- **incumbent:** S10 `rd1`. This is the newest `s10_shadow_checkpoints` row of the city-day decided at
  or before t, with its bucket probabilities. A decision with none has no incumbent row, and is counted.

**Recorded for every decision**, entered or not, by the evaluator:
- the row's hash, `sdyes-v1` and the model's sha256;
- each bucket's p, touch, cost a share, shares and whether it qualified;
- the entry, or the reason it was skipped. The reasons are: no row (a gap in the record), no fresh
  book, the ladder incomplete in the record, no bucket qualified, below the minimum order, and already
  entered that city-day.

The evaluator writes this to `data/eval/wxpredict/sdyes/`. It is built and tested before the first
look and run from the repository's record. **No new schedule** (Rule 7).

**Rule 11.** Nothing is learned during the evaluation:
- the model, THETA and every limit above are fixed;
- the model was fitted on dates up to 11 Oct and is scored from 13 Oct;
- THETA was chosen on dates up to 8 Oct;
- every decision row carries the version and the model's hash.

A change to any of them is `sdyes-v2`, scored only on dates after it is committed. It is never an
edit to this file.

## Dates

- **Forward dates:** event dates from 13 Oct 2026 (`sdyes.FIRST_FORWARD_DATE`, `2026-10-13`). 10-12 Oct are neither development nor forward:
  no rule was scored on them, and the minute books began on 12 Oct 16:04Z.
- **A valid date:** a settled event date on which at least one city had at least one decision with a
  row and a fresh book.
- **Before the first look, only counts are read:** decisions, skips by reason, valid dates. No profit,
  probability or score is computed or reported.

## Scores (fixed now)

**Primary: realised net profit after the fee, per valid date.**
- Both cities are summed, and a date without an entry counts as 0.
- The 90% and 95% intervals come from resampling whole dates (both cities and every decision of a
  date together; seed 20261012, 4,000 resamples).

**Beside it:**
- **The paired difference with the market-only control:** the candidate minus the control, per valid
  date, with the same intervals.
- **Counts:** entries, entry dates, shares, dollars spent and fees.
- **Robustness diagnostics**, with uncertainty and never used to pick a new winner:
  - each city alone, and without the strongest city;
  - without the largest winning date;
  - the incumbent's result on its matched decisions;
  - latency at 300 s (the first fresh books from t + 300 to t + 600 s);
  - the weather regime: days whose maximum was reached before 13:00 local, against after.
- **Weather accuracy, separately:** log loss and Brier on the winner for the candidate, the market
  and rd1 at the same decisions. It does not stand in for the trading result.
- **Everything left out, by reason.**

## When it is looked at

- **The first look** is the first day on which at least **20** valid dates have settled. If every
  date from 13 Oct is valid, that is about 2 Nov.
- If that look reads "insufficient evidence", there is one more look at **40** valid dates, and then
  the test stops. Nothing is looked at in between.

## Acceptance rule

At the first look (or the second):
- **Positive research evidence:** the combined rule's net profit per valid date has a 90% interval
  above 0. The paired difference with the market-only control is reported beside it.
- **Failed:** the 90% interval is wholly below 0.
- **Insufficient evidence:** anything else, including a positive mean whose interval crosses 0.

**What a verdict does.** Nothing changes by itself.
- Positive evidence still needs usable depth, capital capacity and operational reliability, measured
  separately (the action plan's sections 6 and 8). Any allocation is Hassan's decision.
- A failure is recorded here, and the event record is used to name what failed before any `sdyes-v2`.

## Known limits, stated before the data

- **The development evidence leans against the candidate** (above). It is frozen because the rule
  fixed before the run says so, not because the development dates support it.
- **The development books differ from the forward ones.**
  - Development: 2-hourly archived snapshots, with the depth rebuilt from tiers and the first tier
    priced at the touch (optimistic). The +1-cent stress changed THETA 0.10's mean per entry date from
    +2.17 to +1.64. The decision was up to an hour before the book. The minimum order was assumed to be
    5 shares.
  - Forward: the recorded 10 levels a side, a minute after the decision, with the book's own minimum
    order.
- **Execution is not modelled beyond the latency allowance.**
  - Queue position, partial fills and a book moving between the poll's answer and an order are not
    modelled beyond the 60-300 s allowance; the 300 s diagnostic bounds it.
  - The fee is the platform's cost model, not re-read per market.
- **Live rows must equal the table's.** The parity check of the live reader runs on 16 Oct. If it finds
  a difference that changes a feature of the pilot cities, the affected decisions are reported both
  ways, with the cause named. The rule does not change.
- **Settlement and measurement.**
  - On the table's venue days, the station's maximum fell in the winning bucket on 179 of 179 Jeddah
    days and 183 of 183 Istanbul days.
  - In the 10-16h rows, the winner was never below the running maximum's bucket (1,024 Jeddah rows,
    987 Istanbul). Small on history, not zero forward.
- **The two cities are not independent.** Both are UTC+3, and their decisions fall in the same hours.
  The intervals resample whole dates for that reason.
- **The minute reports arrive first, but v1 does not use them.** Measured on 12 Oct:
  - Jeddah's reports come hourly at :00, Istanbul's half-hourly at :20 and :50;
  - our first receipt was a median 1.5 and 12.2 minutes after the reading.

  A version that decides on each new report is a later version, on later dates.

## Result

(appended below after the first look; nothing above changes)
