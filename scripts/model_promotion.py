#!/usr/bin/env python3
"""
Decide whether a fitted model has earned the right to move a price.

A FIT IS A CHALLENGER. scripts/weather_model.py fits each city's daily maximum
from its own morning conditions and scores it against persistence on held-out
days. That is a real test and it is not the test that matters, for three
reasons:

  THE BENCHMARK IS WRONG. Nothing on this desk prices off persistence. It
  prices off the public forecast, and the public forecast is good. Measured on
  this repo's own cities, persistence MAE runs 1.2 to 2.5 C - a model can beat
  that comfortably and still be worse than the number it would replace.

  THE DAYS ARE WRONG. Held-out days sit inside the training window. The fit
  chose its features knowing that season, and weather is autocorrelated enough
  that the days on either side of a held-out day carry most of its information.
  A FORWARD day is one nobody had seen when the prediction was made.

  THE LEADS ARE POOLED. A model sharp at lead 0 and useless on Friday averages
  to "fine". Every number here is per city AND per lead.

THE RULE. All five must hold, for one city and one lead, on the same forward
days:

  1 at least MIN_FORWARD_DAYS settled forward city-days
  2 MAE at least MIN_GAIN_C better than persistence
  3 MAE at least MIN_GAIN_C better than the public forecast AVAILABLE AT
    PREDICTION TIME - the nws_max_c stored on the prediction row, never one
    fetched afterwards
  4 a paired moving-block bootstrap over dates whose 90% interval for the
    improvement stays above zero, for both comparisons
  5 no stale-input, missing-anchor or version-attribution failure

MIN_GAIN_C is 0.10. Polymarket's buckets are 1 C wide and weather_model's own
floor for keeping a feature is 0.05 C; a centre that moves by less than a tenth
of a bucket cannot change which bucket a day lands in, so a gain below it is
real and useless.

WHY A BLOCK BOOTSTRAP. Weather is autocorrelated over days. Resampling single
days would treat one warm spell the model happened to call well as thirty
independent wins and return an interval far too narrow. Moving blocks of
consecutive dates keep the runs intact. PAIRED, because both models are scored
on the same day: what is resampled is the per-day DIFFERENCE, which takes the
day's own difficulty out of the comparison.

Writes one row per (ACTIVE city, lead) to derived_model_promotion, including
the leads the forward job has written nothing for - an absent row cannot be
told apart from a city nobody looked at. Only 'promoted' reaches
scripts/probability_engine.py, through v_model_promoted.

THE ROW IS THE CURRENT ANSWER, NOT A RECORD OF ONE, so it is written with
common.upsert_replace. Written with the ordinary ignore-duplicates upsert - as
it was until 2026-09-21 - the first run's verdict becomes permanent and every
corrected one after it is discarded by the database. See upsert_replace.

And a lead whose oldest prediction has not happened yet is in SHADOW, not
stale: `stale` means the fit or its predictions stopped arriving, and a lead
three days old has not stopped at anything. See decide().

  python scripts/model_promotion.py [--dry-run] [--min-days 30] [--draws 2000]
"""
import argparse
import datetime as dt
import random
import sys

from common import rest_all, upsert_replace, log_run, active_city_keys, drop_retired

# ---------------------------------------------------------------------------
# The rule, as constants, so changing the bar is a visible change.
# ---------------------------------------------------------------------------
MIN_FORWARD_DAYS = 30      # settled forward city-days before any verdict
MIN_GAIN_C = 0.10          # a tenth of a 1 C bucket; below this nothing moves
BOOT_DRAWS = 2000
BOOT_INTERVAL = 0.90       # 5th to 95th percentile

# A fit is weekly. Three missed runs and what would be promoted is not what is
# running, so there is nothing honest to promote.
FIT_MAX_AGE_DAYS = 21
# Forward prediction runs every few hours. Three days of silence means the
# model is not being applied, whatever its history says.
PREDICTION_MAX_AGE_DAYS = 3

TARGET = "max_c"

# The horizon the forward job publishes, so a lead with NO prediction at all
# is a visible row rather than an absent one.
#
# Leads 1 to 7 read `stale` on all 49 cities for two days and the table gave
# no way to tell the two possible reasons apart: predictions had been written
# and nothing had settled yet, or no prediction existed at that lead. Those
# want opposite responses - wait, versus go and find out why the forecast job
# is not writing that lead - and both arrived as the same word.
EXPECTED_LEADS = tuple(range(0, 8))


def block_len(n):
    """Moving-block length. n**(1/3) is the standard choice for a stationary
    block bootstrap of a mean - long enough to carry the autocorrelation,
    short enough to leave the resample some freedom."""
    return max(1, min(n, round(n ** (1.0 / 3.0))))


def bootstrap_interval(diffs, draws=BOOT_DRAWS, interval=BOOT_INTERVAL, seed=0):
    """(lo, hi) for the MEAN of `diffs`, by moving-block resampling.

    `diffs` must be in date order and paired - one per scored day, each the
    baseline's error minus the model's error on that day, so a positive mean
    is the model being better.

    The seed is fixed on purpose: a promotion that flips between runs because
    the random draws differed is not evidence, and an operator re-running this
    to check a surprising verdict has to get the same answer.
    """
    n = len(diffs)
    if n == 0:
        return None, None
    if n == 1:
        return diffs[0], diffs[0]
    L = block_len(n)
    starts = max(1, n - L + 1)
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = []
        while len(sample) < n:
            s = rng.randrange(starts)
            sample.extend(diffs[s:s + L])
        sample = sample[:n]
        means.append(sum(sample) / n)
    means.sort()
    lo_i = int((1 - interval) / 2 * draws)
    hi_i = min(draws - 1, int((1 + interval) / 2 * draws))
    return means[lo_i], means[hi_i]


def _mae(rows, key):
    return sum(abs(r[key] - r["observed"]) for r in rows) / len(rows)


def score(rows, min_days=MIN_FORWARD_DAYS, draws=BOOT_DRAWS, seed=0):
    """Both comparisons on the same days, with their bootstrap intervals.

    rows: date-ordered dicts with observed, predicted, public, persistence.
    """
    n = len(rows)
    model = _mae(rows, "predicted")
    public = _mae(rows, "public")
    pers = _mae(rows, "persistence")

    # Paired per-day differences: baseline error minus model error, so a
    # positive number is a day the model called better.
    d_public = [abs(r["public"] - r["observed"]) - abs(r["predicted"] - r["observed"])
                for r in rows]
    d_pers = [abs(r["persistence"] - r["observed"]) - abs(r["predicted"] - r["observed"])
              for r in rows]
    pub_lo, pub_hi = bootstrap_interval(d_public, draws, seed=seed)
    per_lo, per_hi = bootstrap_interval(d_pers, draws, seed=seed + 1)

    checks = [
        ("enough_forward_days", n >= min_days,
         f"{n} settled forward day(s), needs {min_days}"),
        ("beats_persistence_by_margin", (pers - model) >= MIN_GAIN_C,
         f"{pers - model:+.3f} C against persistence, needs {MIN_GAIN_C:+.2f}"),
        ("beats_public_forecast_by_margin", (public - model) >= MIN_GAIN_C,
         f"{public - model:+.3f} C against the public forecast, needs {MIN_GAIN_C:+.2f}"),
        ("improvement_interval_above_zero",
         (pub_lo is not None and pub_lo > 0 and per_lo is not None and per_lo > 0),
         f"90% interval {pub_lo:+.3f} to {pub_hi:+.3f} C against the public forecast, "
         f"{per_lo:+.3f} to {per_hi:+.3f} C against persistence"
         if pub_lo is not None else "no interval"),
    ]
    # The BINDING interval is the one that nearly failed: reporting the
    # comfortable one would make a marginal promotion look safe.
    binding = (pub_lo, pub_hi) if (pub_lo is None or per_lo is None or pub_lo <= per_lo) \
        else (per_lo, per_hi)
    return {
        "n_days": n,
        "model_mae_c": round(model, 4),
        "public_mae_c": round(public, 4),
        "persistence_mae_c": round(pers, 4),
        "gain_vs_public_c": round(public - model, 4),
        "gain_vs_persistence_c": round(pers - model, 4),
        "boot_lo_c": round(binding[0], 4) if binding[0] is not None else None,
        "boot_hi_c": round(binding[1], 4) if binding[1] is not None else None,
        "boot_draws": draws,
        "block_days": block_len(n),
        "checks": checks,
    }


def waiting_detail(pending, min_days=MIN_FORWARD_DAYS):
    """The sentence for a lead that has predictions and no settled day yet.

    It names the DATE. "no settled forward day" is true of a lead whose
    forecast job died three weeks ago and of a lead published for the first
    time this morning, and an operator has to do opposite things about them.
    """
    n = (pending or {}).get("unsettled", 0)
    first = (pending or {}).get("first_pending")
    if not first:
        return f"{n} prediction(s) outstanding and none carries a date"
    first_day = dt.date.fromisoformat(str(first))
    # A prediction for day D can first be scored on D+1, once the day has run
    # its course and the observation cache carries its maximum.
    scorable_on = first_day + dt.timedelta(days=1)
    # One settled day per calendar date at a given lead, so the floor cannot
    # be reached before this. Later if a day is missed; never earlier.
    floor_on = first_day + dt.timedelta(days=min_days)
    return (f"{n} prediction(s) outstanding at this lead, the oldest for {first_day}; "
            f"the first can be scored on {scorable_on} and the {min_days}-day floor "
            f"cannot be reached before {floor_on}")


def decide(scored, stale_reasons, dropped, min_days=MIN_FORWARD_DAYS, pending=None):
    """shadow | promoted | rejected | stale, and every rule beside it.

    STALE BEATS EVERYTHING, because a stale model has not been beaten - it has
    not been judged, and reporting "rejected" for something nobody measured is
    a claim about evidence that does not exist.

    A YOUNG HORIZON IS NOT A STALE ONE. This returned "stale" whenever nothing
    was scorable, and `stale` in this module means one specific thing: the fit
    or its forward predictions STOPPED ARRIVING. A lead whose predictions are
    arriving on time and whose oldest one is still in the future has not
    stopped at anything - it has not come due.

    The distinction is not cosmetic. Measured 2026-09-21, the forward job's
    first run was 09-19, so the oldest prediction at each lead was:

        lead 0  2026-09-19    98 settled city-days
        lead 1  2026-09-20    24 settled city-days
        lead 2  2026-09-21     0 - settles 09-22
        lead 7  2026-09-26     0 - settles 09-27

    Every one of leads 2 to 7 was reported `stale` on all 49 cities, and the
    board's own text for stale reads "either the fit or its forward
    predictions stopped arriving". Nothing had stopped. The horizon was three
    days old and the longest lead had not yet had a day to be wrong about.

    So: nothing to score AND the inputs are current -> shadow, with the date
    it can first be judged. Nothing to score AND no prediction at this lead at
    all -> stale, which is the honest word for it.
    """
    reasons = [{"rule": r, "held": False, "detail": d} for r, d in stale_reasons]
    for rule, count, detail in dropped:
        reasons.append({"rule": rule, "held": count == 0, "detail": detail})

    if stale_reasons:
        return "stale", reasons
    if scored is None:
        outstanding = (pending or {}).get("unsettled", 0)
        if outstanding > 0:
            return "shadow", reasons + [
                {"rule": "the_horizon_has_settled", "held": False,
                 "detail": waiting_detail(pending, min_days)}]
        return "stale", reasons + [
            {"rule": "anything_to_score", "held": False,
             "detail": "no forward prediction at this lead survived the drops above, "
                       "and none is outstanding - the forecast job is not writing it"}]

    reasons += [{"rule": r, "held": bool(ok), "detail": d} for r, ok, d in scored["checks"]]
    if scored["n_days"] < min_days:
        return "shadow", reasons
    return ("promoted" if all(ok for _, ok, _ in scored["checks"]) else "rejected"), reasons


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def load():
    """(predictions, outcomes, fits) - three reads, all paged."""
    preds = rest_all("derived_model_forecast", [
        ("select", "city_key,for_date,run_at,lead_days,predicted_max_c,nws_max_c,"
                   "prev_source,model_version,predicted_at"),
    ], order="city_key.asc,for_date.asc,run_at.asc", page_size=1000)

    obs = rest_all("derived_city_day_features", [
        ("select", "city_key,obs_date,max_c,n_obs"),
    ], order="city_key.asc,obs_date.asc", page_size=1000)

    fits = rest_all("derived_weather_model", [
        ("select", "city_key,target,fitted_at,model_version"),
        ("target", f"eq.{TARGET}"),
    ], order="city_key.asc", page_size=1000)

    # Retired cities keep their history and their last fit; they must not keep
    # earning a promotion off it. Filtering the OUTCOMES alone would be enough
    # - a prediction with nothing to score against cannot clear the forward-day
    # floor - but predictions and fits go too, so the report never names a city
    # the desk has stopped trading.
    active = active_city_keys()
    preds, retired = drop_retired(preds, active)
    obs, _ = drop_retired(obs, active)
    fits, _ = drop_retired(fits, active)
    if retired:
        print(f"{len(retired)} retired city/cities excluded: " + ", ".join(retired))
    return preds, obs, fits


def assemble(preds, obs, fits, today=None):
    """One scorable row per (city, lead, day), plus what had to be dropped.

    THE NEWEST RUN WINS for a given (city, for_date, lead). The forward job
    writes a row per NWS run, so several rows share a lead; taking whichever
    PostgREST returned first would price against an arbitrary one of them.
    """
    today = today or dt.date.today()
    observed = {(r["city_key"], str(r["obs_date"])): float(r["max_c"])
                for r in obs if r.get("max_c") is not None and (r.get("n_obs") or 0) >= 12}
    fit_by_city = {r["city_key"]: r for r in fits}

    newest = {}
    for p in preds:
        if p.get("lead_days") is None or p.get("predicted_max_c") is None:
            continue
        key = (p["city_key"], int(p["lead_days"]), str(p["for_date"]))
        cur = newest.get(key)
        if cur is None or str(p["run_at"]) > str(cur["run_at"]):
            newest[key] = p

    groups, drops = {}, {}
    for (city, lead, day), p in newest.items():
        g = groups.setdefault((city, lead), [])
        d = drops.setdefault((city, lead), {"unsettled": 0, "no_public": 0,
                                            "no_anchor": 0, "no_version": 0,
                                            "first_pending": None, "last_pending": None})
        if str(day) >= str(today) or (city, str(day)) not in observed:
            d["unsettled"] += 1
            # WHICH day is outstanding, not just how many. A lead reporting
            # nothing to score is waiting on a specific date, and that date is
            # the difference between "come back tomorrow" and "the forecast
            # job stopped writing this lead three weeks ago".
            if d["first_pending"] is None or str(day) < d["first_pending"]:
                d["first_pending"] = str(day)
            if d["last_pending"] is None or str(day) > d["last_pending"]:
                d["last_pending"] = str(day)
            continue
        # Persistence is the observed maximum of the day before, from the same
        # cache the outcome comes from - not the prev_max_c the prediction
        # carried, which may itself be a chained guess.
        prev_day = (dt.date.fromisoformat(str(day)) - dt.timedelta(days=1)).isoformat()
        if (city, prev_day) not in observed:
            d["unsettled"] += 1
            continue
        if p.get("nws_max_c") is None:
            d["no_public"] += 1
            continue
        if not p.get("prev_source"):
            d["no_anchor"] += 1
            continue
        if not p.get("model_version"):
            d["no_version"] += 1
            continue
        g.append({
            "day": str(day),
            "observed": observed[(city, str(day))],
            "predicted": float(p["predicted_max_c"]),
            "public": float(p["nws_max_c"]),
            "persistence": observed[(city, prev_day)],
            "model_version": p["model_version"],
            "predicted_at": p.get("predicted_at"),
        })
    for rows in groups.values():
        rows.sort(key=lambda r: r["day"])
    return groups, drops, fit_by_city


def stale_checks(city, rows, fit, now=None):
    """Rule 5, as a list of (rule, detail) for every failure."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    if fit is None:
        out.append(("fit_exists", f"no fitted model for {city}"))
    else:
        age = (now - _ts(fit.get("fitted_at"))).days if fit.get("fitted_at") else None
        if age is None:
            out.append(("fit_is_current", "the fit has no fitted_at"))
        elif age > FIT_MAX_AGE_DAYS:
            out.append(("fit_is_current",
                        f"the fit is {age} days old, over {FIT_MAX_AGE_DAYS}"))
    if rows:
        newest = max((_ts(r["predicted_at"]) for r in rows if r.get("predicted_at")),
                     default=None)
        if newest is None:
            out.append(("predictions_are_current", "no prediction carries a timestamp"))
        elif (now - newest).days > PREDICTION_MAX_AGE_DAYS:
            out.append(("predictions_are_current",
                        f"the newest prediction is {(now - newest).days} days old, "
                        f"over {PREDICTION_MAX_AGE_DAYS}"))
        versions = {r["model_version"] for r in rows}
        if fit is not None and fit.get("model_version") and \
                fit["model_version"] not in versions:
            out.append(("scored_what_is_running",
                        f"every scored day came from {sorted(versions)[:2]}, and the fit "
                        f"now running is {fit['model_version']}"))
    return out


def _ts(value):
    if value is None:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


SCORE_COLUMNS = ("n_days", "model_mae_c", "public_mae_c", "persistence_mae_c",
                 "gain_vs_public_c", "gain_vs_persistence_c", "boot_lo_c",
                 "boot_hi_c", "boot_draws", "block_days")


def retired_rows(active, computed_at):
    """Correction rows for city-leads that hold a verdict and are no longer ours.

    load() drops retired cities before anything is scored, which is right: a
    city the desk has stopped trading must not go on earning a promotion. The
    consequence nobody looked at is that its rows are then never written
    again. They keep the state and the computed_at they last had, forever,
    and per-city freshness goes on reporting the table overdue on account of a
    city that is not being traded.

    NOTHING IS DELETED. The row is rewritten to say what is actually true
    about it: this city is retired, so this verdict is not being maintained.
    """
    try:
        existing = rest_all("derived_model_promotion",
                            [("select", "city_key,lead_days,target")],
                            order="city_key.asc,lead_days.asc,target.asc",
                            page_size=1000)
    except Exception as e:
        print(f"  ! could not read derived_model_promotion to mark retired cities "
              f"({e}) - their rows keep their old timestamp", file=sys.stderr)
        return []

    out = []
    for r in existing:
        if r["city_key"] in active:
            continue
        row = {
            "city_key": r["city_key"], "lead_days": r["lead_days"],
            "target": r.get("target") or TARGET, "state": "stale",
            "model_version": None, "first_day": None, "last_day": None,
            "reasons": [{"rule": "city_is_active", "held": False,
                         "detail": f"{r['city_key']} is retired from the board, so its "
                                   f"fit is no longer scored and this verdict is no "
                                   f"longer maintained"}],
            "computed_at": computed_at,
        }
        row.update({k: None for k in SCORE_COLUMNS})
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-days", type=int, default=MIN_FORWARD_DAYS)
    ap.add_argument("--draws", type=int, default=BOOT_DRAWS)
    args = ap.parse_args()

    preds, obs, fits = load()
    groups, drops, fit_by_city = assemble(preds, obs, fits)

    if not preds:
        print("derived_model_forecast is empty - nothing has been predicted forward yet, "
              "so nothing can be promoted. Run Actions -> Weather Model, then Model Forecast.")
        log_run("model_promotion", "ok", 0, {"predictions": 0, "promoted": 0})
        return 0

    # EVERY ACTIVE CITY AT EVERY LEAD GETS A ROW, including the ones the
    # forecast job wrote nothing for. A (city, lead) absent from `groups` used
    # to be absent from the table, and an absent row is indistinguishable from
    # a city nobody has looked at. The verdict for "no prediction at this lead"
    # is `stale`, which is correct and, crucially, visible.
    active = active_city_keys()
    for city in sorted(active):
        for lead in EXPECTED_LEADS:
            groups.setdefault((city, lead), [])

    rows_out, tally = [], {"promoted": 0, "shadow": 0, "rejected": 0, "stale": 0}
    for (city, lead), rows in sorted(groups.items()):
        fit = fit_by_city.get(city)
        d = drops.get((city, lead), {})
        dropped = [
            ("has_an_anchor", d.get("no_anchor", 0),
             f"{d.get('no_anchor', 0)} day(s) dropped: the prediction does not say what "
             f"yesterday's maximum was"),
            ("attributable_to_a_fit", d.get("no_version", 0),
             f"{d.get('no_version', 0)} day(s) dropped: no model_version, so the "
             f"coefficients behind them are unknown"),
            ("public_forecast_recorded", d.get("no_public", 0),
             f"{d.get('no_public', 0)} day(s) dropped: no nws_max_c stored at prediction time"),
        ]
        scored = score(rows, args.min_days, args.draws) if rows else None
        state, reasons = decide(scored, stale_checks(city, rows, fit), dropped,
                                args.min_days, pending=d)
        tally[state] = tally.get(state, 0) + 1
        row = {
            "city_key": city, "lead_days": lead, "target": TARGET, "state": state,
            "model_version": (fit or {}).get("model_version"),
            "first_day": rows[0]["day"] if rows else None,
            "last_day": rows[-1]["day"] if rows else None,
            "reasons": reasons,
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        for k in SCORE_COLUMNS:
            row[k] = (scored or {}).get(k)
        rows_out.append(row)

        mark = {"promoted": "PROMOTED", "rejected": "rejected",
                "stale": "stale", "shadow": "shadow"}[state]
        if scored:
            print(f"{city:<14} lead {lead}  {mark:<9} n={scored['n_days']:<4} "
                  f"model {scored['model_mae_c']:.2f}C  public {scored['public_mae_c']:.2f}C  "
                  f"persistence {scored['persistence_mae_c']:.2f}C")
        else:
            print(f"{city:<14} lead {lead}  {mark}")
        for r in reasons:
            if not r["held"]:
                print(f"               · {r['rule']}: {r['detail']}")

    print(f"\n{tally['promoted']} promoted, {tally['shadow']} in shadow, "
          f"{tally['rejected']} rejected, {tally['stale']} stale "
          f"across {len(rows_out)} city-lead pair(s).")
    if tally["promoted"] == 0:
        print("Nothing is promoted, so no fitted model is moving a price. That is the "
              "correct default, not a failure.")

    # COVERAGE, PER LEAD, OVER THE ACTIVE BOARD. The tally above counts states;
    # it cannot tell you that lead 5 was scored on zero cities, because zero
    # scored cities and forty-eight waiting ones both land in `stale`/`shadow`.
    print(f"\ncoverage across {len(active)} active city/cities:")
    for lead in sorted({lead for _, lead in groups}):
        at_lead = [r for r in rows_out if r["lead_days"] == lead
                   and r["city_key"] in active]
        scored_at = [r for r in at_lead if (r.get("n_days") or 0) > 0]
        days = max((r.get("n_days") or 0) for r in at_lead) if at_lead else 0
        print(f"  lead {lead}: {len(at_lead)} city/cities with a verdict, "
              f"{len(scored_at)} with a settled day, most days on any city {days}")
    uncovered = sorted(active - {r["city_key"] for r in rows_out})
    if uncovered:
        print("  ! no verdict at any lead for: " + ", ".join(uncovered))

    rows_out += retired_rows(active, dt.datetime.now(dt.timezone.utc).isoformat())

    if args.dry_run:
        print("--dry-run: nothing written")
        return 0
    if rows_out:
        # REPLACE, not ignore-duplicates. This table holds one row per standing
        # question and the row is the answer as of this run; a write that
        # ignores the duplicate freezes the first answer and discards every
        # correct one after it. See common.upsert_replace for the measurement.
        upsert_replace("derived_model_promotion", rows_out, "city_key,lead_days,target")
    log_run("model_promotion", "ok", len(rows_out), tally)
    return 0


if __name__ == "__main__":
    sys.exit(main())
