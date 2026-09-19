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

Writes one row per (city, lead) to derived_model_promotion. Only 'promoted'
reaches scripts/probability_engine.py, through v_model_promoted.

  python scripts/model_promotion.py [--dry-run] [--min-days 30] [--draws 2000]
"""
import argparse
import datetime as dt
import random
import sys

from common import rest_all, upsert, log_run

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


def decide(scored, stale_reasons, dropped, min_days=MIN_FORWARD_DAYS):
    """shadow | promoted | rejected | stale, and every rule beside it.

    STALE BEATS EVERYTHING, because a stale model has not been beaten - it has
    not been judged, and reporting "rejected" for something nobody measured is
    a claim about evidence that does not exist.
    """
    reasons = [{"rule": r, "held": False, "detail": d} for r, d in stale_reasons]
    for rule, count, detail in dropped:
        reasons.append({"rule": rule, "held": count == 0, "detail": detail})

    if stale_reasons:
        return "stale", reasons
    if scored is None:
        return "stale", reasons + [
            {"rule": "anything_to_score", "held": False,
             "detail": "no settled forward day survived the drops above"}]

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
                                            "no_anchor": 0, "no_version": 0})
        if str(day) >= str(today) or (city, str(day)) not in observed:
            d["unsettled"] += 1
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
                                args.min_days)
        tally[state] = tally.get(state, 0) + 1
        row = {
            "city_key": city, "lead_days": lead, "target": TARGET, "state": state,
            "model_version": (fit or {}).get("model_version"),
            "first_day": rows[0]["day"] if rows else None,
            "last_day": rows[-1]["day"] if rows else None,
            "reasons": reasons,
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        for k in ("n_days", "model_mae_c", "public_mae_c", "persistence_mae_c",
                  "gain_vs_public_c", "gain_vs_persistence_c", "boot_lo_c",
                  "boot_hi_c", "boot_draws", "block_days"):
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

    if args.dry_run:
        print("--dry-run: nothing written")
        return 0
    if rows_out:
        upsert("derived_model_promotion", rows_out, "city_key,lead_days,target")
    log_run("model_promotion", "ok", len(rows_out), tally)
    return 0


if __name__ == "__main__":
    sys.exit(main())
