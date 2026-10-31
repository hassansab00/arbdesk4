# Pre-day model development and checkpoint selection

The objective is a winning-bucket forecast before the target day starts.
Noon and expected-before-peak labels do not prove the final station high was
still ahead. Keep conditional intraday accuracy separate from pre-day evidence;
a hindsight filter based on the eventual high is not a live entry rule.

## Shared checkpoint selection

Changing the accuracy horizon now changes the parent checkpoint used by the
city cards and Was it right?. Overall stays an explicit aggregate and identifies
the checkpoint retained on the cards. It never becomes an invalid frozen-card
request. Compare Frozen pick, target date and the city-local recorded time.
The separate live forecast may change between scheduled captures.

Manual records remain offline. Missing original receipts stay missing: this UI
repair creates no historical calls and revises no saved outcomes or predictions.

## Offline research recipe

`tools/wxpredict/early_daymax.py` fits one pooled, regularized band classifier
across cities. It targets the venue's actual winning bucket, including open
tails, rather than an invented final temperature. City, station, season,
forecast spread and past-only weather errors are features of this shared model.

The explicit input allowlist excludes outcomes, target-day observations/running
maxima, market prices and serving probabilities. The builder never loads
target-day station reports. It reuses shared forecast-availability and
whole-past-day rules to reconstruct one 18:01 city-local D-1 row per event.
An event must have a valid recorded start at or before that decision.
This is not a production freeze receipt. Historical provider publication
schedules, label availability and first publication of each band are unproved.
Contract changes and active-city selection also limit archive comparability.

Train only on earlier dates and evaluate on a later fixed window. The command
requires explicit dates and refuses overlaps, duplicate city-days, insufficient
development dates, malformed ladders and invalid distributions. Event weights
sum to one regardless of ladder size. It uses fixed model settings rather than
searching the evaluation outcomes for a preferred configuration.

Use `requirements.txt` and `requirements.wxpredict.txt`:

```bash
python tools/wxpredict/early_daymax.py \
  --from-date FIRST_TRAIN_DATE --fit-last LAST_TRAIN_DATE \
  --eval-first FIRST_EVALUATION_DATE --eval-last LAST_EVALUATION_DATE \
  --out /tmp/pre-day-development.json
```

The local output includes all-call coverage, the raw ensemble-point comparator,
same-row complete-market comparisons and per-city results. Both sides of a
market comparison use the same calls; missing quotes remain reported through
the denominator. The output also records source blob identities, recipe hashes,
input hash and fitted model hash. Keep archive-derived results private unless
their publication is explicitly authorized.

This recipe is development only and always reports `changes_serving: false`
and `eligible_for_promotion: false`. It has no database writes, serving registry
changes or scheduled job. A retrospective result is neither independent future
validation nor an executable-price backtest. It does not change live weights.

## Acceptance work remains separate

A candidate needs genuinely received pre-day inputs and a frozen full ladder,
then subsequent settlement and scoring on dates outside its training history.
Compare against the unchanged served baseline and same-time market. Report
coverage, missing/late calls, model identity and uncertainty. Do not substitute
noon/post-high success for this pre-day decision. Require an executable-ask
evaluation before considering capital.

The variant CLI test fixture is isolated from production registry pointers so a
newly registered research model cannot expand its synthetic variant list.
This changes the test, not registry selection or serving behavior.
