# The six frozen cities (`frozen6-v1`): pre-registration

**Written 15 Nov 2026, before any forward date of this rule existed.**
Hassan, 15 Nov: "We will pick 6 cities from now on and freeze and trade on them
that means each day contains the same cities now different ones." This file
fixes the rule so the record cannot be argued with afterwards. It is a record,
in shadow: capital is Hassan's (Rule 6). The measurement is
`tools/frozen6_study.py`; `tests/test_frozen6_study.py` holds it to this file.
The study behind it is section 13 of `docs/STRATEGY_STUDY_2026-11-15.md`.

## The rule

Each evening, for each of six cities, one trade on the model's evening-before
pick (the top bucket of the city's first `d1_eve` call for the target date):

| Side | Cities |
|---|---|
| YES | Shanghai, Helsinki, Amsterdam |
| NO | Houston, Austin, Sao Paulo |

- **One dollar a trade** for the measurement (the ledger also shows $100), held
  to settlement on the venue's winning bucket.
- **The price** is the live top of book at the hourly capture
  (`live_capture`, kind `books`, at :36) inside Hassan's recording window,
  23:00-02:00 Beirut (21:00Z-00:00Z): YES at the ask, NO at one less the bid,
  plus the taker fee (`strategy_study.cost`). **The official capture is 22:36Z**
  (00:36 Beirut); 21:36Z and 23:36Z are shown beside it. Hassan's own recorded
  prices are his; the ledger is the one reference.
- **Frozen:** the cities and the sides do not change. A change is
  `frozen6-v2`, scored only on dates after it is committed. It is never an edit
  to this file.

## Dates

- **Forward dates:** target dates from **16 Nov 2026**
  (`frozen6_study.FIRST_FORWARD_DATE`), recorded on the evening of 15 Nov.
- **A valid date:** all six trades are priced at the capture and settled.
- **Day by day:** `python tools/frozen6_study.py --ledger`. It reads the nightly
  mirror, so a date appears after its outcome is mirrored.

## Where it comes from (in-sample, not evidence)

The six cities were chosen on the record the study measures
(`docs/STRATEGY_STUDY_2026-11-15.md`, section 13). Over the 33 evenings the
live books cover (13 Oct - 14 Nov), at the 22:36Z book: -0.4% a dollar (90%
-12.7% to +10.6%), against -9.6% for the same rule on every city. A city's
accuracy rank does not carry into the next dates at this call (rank
correlation +0.06), so the average city is the fair expectation.

## When it is looked at

- **First look: 23 valid dates.** At the return and spread the record shows,
  that is what 80% power needs for a true return of +20% a dollar
  (`data/eval/codex_test_2026-11-12/result.json`, `form.sizing`). It can stop the
  test; it cannot accept.
- **Last look: 46 valid dates.** The test ends there.
- **Before a look, only counts are read** (`--ledger` shows the days and the
  count; it computes a verdict only when a look is due).

## Acceptance rule

At a look, the 90% interval of the return per dollar at the 22:36Z book,
resampling whole dates (`strategy_study.result`, seed 11):
- **Positive research evidence:** the interval is wholly above 0.
- **Failed:** the interval is wholly below 0. At the first look the test stops.
- **Insufficient evidence:** anything else. At the first look the test continues.

**What a verdict does.** Nothing changes by itself. Positive evidence is a
proposal to Hassan, who decides capital. A failure closes `frozen6-v1`.

## Known limits, stated before the data

- **The cities are in-sample,** as above.
- **Houston's and Austin's evening forecasts are made at 00:37Z, 02:37 Beirut,**
  after the window ends; Sao Paulo's at 21:37Z, 23:37 Beirut. The bucket is
  the evening call's whatever time it is read.
- **The capture is the hourly top of book, not Hassan's fill.** On the record,
  26 to 33 of 194 trades have a touch smaller than a $4 order, so a real order
  would walk the book.
- **Six cities on one date are not independent,** which is why the interval
  resamples whole dates.

## Result

(appended below at each look; nothing above changes)
