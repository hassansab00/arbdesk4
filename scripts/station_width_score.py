#!/usr/bin/env python3
"""The width around the corrected centre, scored forward (plan v2.3 P3.9 part 3).

WHAT IS COMPARED. For every market the venue has confirmed, the engine's LAST
pricing before the city's local day began, exactly as served, against the same
ladder re-priced with the stored station width (band_probabilities.
station_width_c, recorded since #230) around the SAME centre. Only the width
differs, and every row proves it: re-priced with the width it was served with,
the ladder must reproduce the stored probabilities to REPRODUCE_TOL, or the
market is skipped rather than scored. Measured 27 Sep on all 134 markets the
venue confirmed for 24-26 Sep, each one's last day-ahead ladder: every one
reproduced, median 4e-6, largest 3.6e-5 (centre_c and sigma_c are stored to
4 decimals).

SCORES, in the order plan v2.3 P7.3's contract sets: the venue ladder's
multiclass log loss (the winner's probability floored at PROB_FLOOR, the
engine's own), Brier, the top pick (ties broken by band id, as the tick does);
then, against the whole-day station maximum where one exists, CRPS and the 80%
interval's coverage. One immutable row per market in fact_station_width_score.

THE VERDICT, every night, over every row since FORWARD_FROM: the mean
log-loss gain per city-day (served minus width: positive favours the width)
and its 90% interval from a date-block bootstrap (all cities of a date resampled
together). "ready" needs the lower bound above zero over at least MIN_DATES
dates. The script reports; a person turns settings.station_width_pricing on.

FORWARD ONLY. FORWARD_FROM is the first night the width was fitted (28 Sep,
#230), after its design froze (#228): nothing scored here was seen when it was
designed. BOOT, SEED, MIN_DATES, REPRODUCE_TOL and the lookbacks are this
module's priors, logged on every run.

    python scripts/station_width_score.py [--dry-run] [--today YYYY-MM-DD]
"""
import argparse
import datetime as dt
import json
import math
import random
import sys
from zoneinfo import ZoneInfo

from probability_engine import PROB_FLOOR, clamp_prob, compute_band_probabilities

JOB = "P3.9_width_score"
FORWARD_FROM = "2026-09-28"
LOOKBACK_DAYS = 14          # a market confirmed later than this after its day is not looked for
PRICING_LOOKBACK_DAYS = 3   # the day-ahead price is looked for this far before the day
REPRODUCE_TOL = 1e-4
BOOT = 2000
SEED = 11
MIN_DATES = 7
LEVEL = 0.90
Z80 = 1.2816                # the 90th percentile of the standard normal: the 80% interval


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------
def local_midnight(day, timezone):
    """The UTC instant the local `day` began in `timezone`."""
    zone = ZoneInfo(timezone) if timezone else dt.timezone.utc
    d = dt.date.fromisoformat(str(day)[:10])
    return dt.datetime.combine(d, dt.time(0, 0), tzinfo=zone).astimezone(dt.timezone.utc)


def _at(v):
    t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def day_ahead_pricing(rows, day_start):
    """(computed_at, {band_id: row}) of the newest pricing before `day_start`
    at lead 1 or more, or None. A pricing is every band written with one
    computed_at (probability_engine stamps a city-day's ladder once)."""
    by_at = {}
    for r in rows:
        if r.get("lead_days") is None or int(r["lead_days"]) < 1:
            continue
        at = _at(r["computed_at"])
        if at >= day_start:
            continue
        by_at.setdefault(at, {})[str(r["band_id"])] = r
    if not by_at:
        return None
    at = max(by_at)
    return at, by_at[at]


def crps_normal(y, mu, sigma):
    z = (y - mu) / sigma
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return sigma * (z * (2 * cdf - 1) + 2 * pdf - 1 / math.sqrt(math.pi))


def _ladder(centre, sigma, unit, bands):
    """The engine's raw probabilities for this ladder, as it stores them."""
    return {str(b): round(clamp_prob(p), 6) for b, p in compute_band_probabilities(centre, sigma, unit, bands)}


def _scores(probs, winner):
    p = max(probs.get(winner, 0.0), PROB_FLOOR)
    top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return {"p": p, "ll": -math.log(p),
            "brier": sum((q - (b == winner)) ** 2 for b, q in probs.items()),
            "hit": top == winner}


def score_market(market, unit, bands, pricing, station_max_c=None):
    """(row, None) or (None, why not). `bands` are the market's canonical bands
    (band_id, band_lo, band_hi, open_low, open_high); `pricing` is
    day_ahead_pricing's answer."""
    if pricing is None:
        return None, "no day-ahead pricing before the local day"
    at, by_band = pricing
    ids = {str(b["band_id"]) for b in bands}
    if set(by_band) != ids:
        return None, "the pricing does not cover the canonical ladder"
    winner = str(market["winning_band_id"])
    if winner not in ids:
        return None, "the winner is not on the ladder"
    rows = list(by_band.values())
    if any(r.get("raw_prob") is None for r in rows):
        return None, "a band of the pricing has no probability"
    widths = {r.get("station_width_c") for r in rows}
    if widths == {None}:
        return None, "no stored width on the pricing"
    if len(widths) != 1:
        return None, "the stored width differs across the ladder"
    if any(r.get("observed_floor_c") is not None for r in rows):
        return None, "the pricing carries an observed floor"
    if any(r.get("calibrated_prob") is not None and r.get("raw_prob") is not None
           and float(r["calibrated_prob"]) != float(r["raw_prob"]) for r in rows):
        return None, "a calibration map was applied"
    centre = {r.get("centre_c") for r in rows}
    sigma = {r.get("sigma_c") for r in rows}
    if len(centre) != 1 or len(sigma) != 1 or None in centre or None in sigma:
        return None, "the pricing has no single centre and width"
    centre, served, width = float(centre.pop()), float(sigma.pop()), float(widths.pop())
    if not (served > 0 and width > 0):
        return None, "a width is not positive"
    if served == width:
        return None, "the stored width priced (the switch was on)"
    stored = {b: float(r["raw_prob"]) for b, r in by_band.items()}
    replay = _ladder(centre, served, unit, bands)
    reproduce = max(abs(replay[b] - stored[b]) for b in ids)
    if reproduce > REPRODUCE_TOL:
        return None, "the served ladder does not reproduce from its centre and width"
    s = _scores(stored, winner)
    w = _scores(_ladder(centre, width, unit, bands), winner)
    row = {"market_id": str(market["market_id"]), "city_key": market["city_key"],
           "for_date": str(market["resolution_date"])[:10], "unit": unit, "priced_at": at.isoformat(),
           "lead_days": int(rows[0]["lead_days"]), "forecast_version": rows[0].get("forecast_version"),
           "centre_c": round(centre, 4), "served_sigma_c": round(served, 4), "station_width_c": round(width, 4),
           "n_bands": len(ids), "winner_band_id": winner, "reproduce_max_abs": round(reproduce, 8),
           "p_winner_served": round(s["p"], 6), "p_winner_width": round(w["p"], 6),
           "log_loss_served": round(s["ll"], 6), "log_loss_width": round(w["ll"], 6),
           "brier_served": round(s["brier"], 6), "brier_width": round(w["brier"], 6),
           "hit_served": s["hit"], "hit_width": w["hit"],
           "station_max_c": None, "crps_served": None, "crps_width": None,
           "cover80_served": None, "cover80_width": None}
    if station_max_c is not None:
        y = float(station_max_c)
        row.update({"station_max_c": round(y, 3),
                    "crps_served": round(crps_normal(y, centre, served), 6),
                    "crps_width": round(crps_normal(y, centre, width), 6),
                    "cover80_served": abs(y - centre) <= Z80 * served,
                    "cover80_width": abs(y - centre) <= Z80 * width})
    return row, None


def _interval(by_date, gain, boot, seed):
    """Point and LEVEL interval of the mean per city-day, dates resampled whole."""
    dates = sorted(by_date)
    rows = [r for d in dates for r in by_date[d]]
    point = sum(gain(r) for r in rows) / len(rows)
    rng = random.Random(seed)
    draws = []
    for _ in range(boot):
        xs = [r for _ in dates for r in by_date[dates[rng.randrange(len(dates))]]]
        draws.append(sum(gain(r) for r in xs) / len(xs))
    draws.sort()
    lo, hi = int((1 - LEVEL) / 2 * boot), int((1 + LEVEL) / 2 * boot) - 1
    return round(point, 4), round(draws[lo], 4), round(draws[hi], 4)


def summarise(rows, boot=BOOT, seed=SEED):
    """The verdict over every scored row: log-loss gain first (served minus
    width, per city-day), Brier and CRPS gains, hit and coverage rates."""
    if not rows:
        return {"markets": 0, "dates": 0, "verdict": "not yet", "why": "nothing scored yet"}
    f = lambda r, k: float(r[k])
    by_date = {}
    for r in rows:
        by_date.setdefault(str(r["for_date"])[:10], []).append(r)
    ll = _interval(by_date, lambda r: f(r, "log_loss_served") - f(r, "log_loss_width"), boot, seed)
    br = _interval(by_date, lambda r: f(r, "brier_served") - f(r, "brier_width"), boot, seed + 1)
    n = len(rows)
    out = {"markets": n, "dates": len(by_date), "from": min(by_date), "to": max(by_date),
           "log_loss_served": round(sum(f(r, "log_loss_served") for r in rows) / n, 4),
           "log_loss_width": round(sum(f(r, "log_loss_width") for r in rows) / n, 4),
           "log_loss_gain": ll[0], "log_loss_gain_90": [ll[1], ll[2]],
           "brier_gain": br[0], "brier_gain_90": [br[1], br[2]],
           "hit_served": round(sum(bool(r["hit_served"]) for r in rows) / n, 4),
           "hit_width": round(sum(bool(r["hit_width"]) for r in rows) / n, 4),
           "mean_served_sigma_c": round(sum(f(r, "served_sigma_c") for r in rows) / n, 4),
           "mean_station_width_c": round(sum(f(r, "station_width_c") for r in rows) / n, 4)}
    with_y = [r for r in rows if r.get("station_max_c") is not None]
    if with_y:
        by_date_y = {}
        for r in with_y:
            by_date_y.setdefault(str(r["for_date"])[:10], []).append(r)
        cr = _interval(by_date_y, lambda r: f(r, "crps_served") - f(r, "crps_width"), boot, seed + 2)
        out.update({"with_station_max": len(with_y), "crps_gain_c": cr[0], "crps_gain_90": [cr[1], cr[2]],
                    "cover80_served": round(sum(bool(r["cover80_served"]) for r in with_y) / len(with_y), 4),
                    "cover80_width": round(sum(bool(r["cover80_width"]) for r in with_y) / len(with_y), 4)})
    out["by_unit"] = {u: {"markets": len(xs), "log_loss_gain": round(
        sum(f(r, "log_loss_served") - f(r, "log_loss_width") for r in xs) / len(xs), 4)}
        for u in ("C", "F") for xs in [[r for r in rows if r["unit"] == u]] if xs}
    if len(by_date) < MIN_DATES:
        out.update(verdict="not yet", why=f"{len(by_date)} dates, needs {MIN_DATES}")
    elif ll[1] > 0:
        out.update(verdict="ready", why="the lower 90% bound of the log-loss gain is above zero")
    elif ll[2] < 0:
        out.update(verdict="the width scores worse", why="the upper 90% bound of the gain is below zero")
    else:
        out.update(verdict="not yet", why="the 90% interval of the gain includes zero")
    return out


# --------------------------------------------------------------------------
# reading and writing
# --------------------------------------------------------------------------
def _chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today")
    args = ap.parse_args(argv)
    from common import day_had_ended, log_run, rest_all, upsert

    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    since = max(FORWARD_FROM, (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat())
    markets = rest_all("v_venue_market_resolution",
                       [("select", "market_id,city_key,resolution_date,winning_band_id"),
                        ("resolution_state", "eq.confirmed"), ("winning_band_id", "not.is.null"),
                        ("resolution_date", f"gte.{since}")],
                       order="resolution_date.asc,market_id.asc")
    done = {str(r["market_id"]) for r in rest_all(
        "fact_station_width_score", [("select", "market_id"), ("for_date", f"gte.{since}")],
        order="market_id.asc")}
    todo = [m for m in markets if str(m["market_id"]) not in done]
    cities = {c["city_key"]: c for c in rest_all("cities", [("select", "city_key,unit,timezone")],
                                                 order="city_key.asc")}

    bands = {}
    for batch in _chunks([str(m["market_id"]) for m in todo], 100):
        for b in rest_all("v_canonical_bands",
                          [("select", "band_id,market_id,band_lo,band_hi,open_low,open_high"),
                           ("market_id", f"in.({','.join(batch)})")], order="band_id.asc"):
            bands.setdefault(str(b["market_id"]), []).append(b)
    prices = {}
    if todo:
        first = min(str(m["resolution_date"])[:10] for m in todo)
        start = (dt.date.fromisoformat(first) - dt.timedelta(days=PRICING_LOOKBACK_DAYS)).isoformat()
        band_market = {str(b["band_id"]): mid for mid, bs in bands.items() for b in bs}
        for batch in _chunks(sorted(band_market), 100):
            for r in rest_all("band_probabilities",
                              [("select", "band_id,computed_at,raw_prob,calibrated_prob,centre_c,sigma_c,"
                                          "station_width_c,lead_days,observed_floor_c,forecast_version"),
                               ("band_id", f"in.({','.join(batch)})"), ("computed_at", f"gte.{start}"),
                               ("lead_days", "gte.1")],
                              order="band_id.asc,computed_at.asc,prob_id.asc"):
                prices.setdefault(band_market[str(r["band_id"])], []).append(r)
    labels = {}
    if todo:
        for r in rest_all("derived_city_day_features",
                          [("select", "city_key,obs_date,max_c,computed_at"), ("obs_date", f"gte.{since}"),
                           ("max_c", "not.is.null")], order="city_key.asc,obs_date.asc"):
            tz = (cities.get(r["city_key"]) or {}).get("timezone")
            if day_had_ended(r["obs_date"], r.get("computed_at"), tz):
                labels[(r["city_key"], str(r["obs_date"])[:10])] = r["max_c"]

    rows, skipped = [], {}
    for m in todo:
        city = cities.get(m["city_key"]) or {}
        day = str(m["resolution_date"])[:10]
        pricing = day_ahead_pricing(prices.get(str(m["market_id"]), []), local_midnight(day, city.get("timezone")))
        row, why = score_market(m, city.get("unit") or "C", bands.get(str(m["market_id"]), []), pricing,
                                labels.get((m["city_key"], day)))
        if row is None:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            rows.append(row)

    written = 0
    if rows and not args.dry_run:
        written = upsert("fact_station_width_score", rows, "market_id")   # a fact: written once
    scored = rest_all("fact_station_width_score",
                      [("select", "for_date,unit,served_sigma_c,station_width_c,log_loss_served,log_loss_width,"
                                  "brier_served,brier_width,hit_served,hit_width,station_max_c,crps_served,"
                                  "crps_width,cover80_served,cover80_width"),
                       ("for_date", f"gte.{FORWARD_FROM}")], order="for_date.asc,market_id.asc")
    if args.dry_run:
        scored = scored + rows
    detail = {"today": today.isoformat(), "since": since, "confirmed": len(markets), "already_scored": len(done),
              "scored_tonight": len(rows), "skipped": skipped, "summary": summarise(scored),
              "priors": {"forward_from": FORWARD_FROM, "lookback_days": LOOKBACK_DAYS,
                         "pricing_lookback_days": PRICING_LOOKBACK_DAYS, "reproduce_tol": REPRODUCE_TOL,
                         "boot": BOOT, "seed": SEED, "min_dates": MIN_DATES, "level": LEVEL},
              "dry_run": args.dry_run}
    print(json.dumps(detail, indent=2, default=str))
    if not args.dry_run:
        log_run(JOB, "ok", written, detail)
    return detail


if __name__ == "__main__":
    main()
    sys.exit(0)
