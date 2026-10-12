"""Why each city-day hit or missed: the day's record, after it settles.

Hassan, 12 Oct: each day has to teach the model which weather made a
prediction hit or miss, and show it. This writes, every night, one row a
settled city-day (public.derived_day_review), read by the city popup:

  the outcome   the winning bucket, the station's maximum and when it came,
                against the hour the maximum usually comes (the station's own
                days around the date in earlier years)
  the forecast  best_match's lead-1 maximum and its error (station - forecast),
                the seven models' and which came closest
  the weather   between 09 and 17 local, what the station reported against
                what the lead-1 forecast said: cloud, dew point, wind, rain.
                Each difference also as a z: against the same city's
                differences over the DAYS_HISTORY days before, so "much
                cloudier than forecast" means unusual for that city
  the calls     at each checkpoint of the day: the engine's top bucket and
                whether it won, wxrd-v2's (variant 'wxrd'), the market's

FACTS, NOT A FITTED EXPLANATION. The record says what differed and by how
much, ranks the differences by z, and does not attribute the error to them: a
difference that came with a miss is not shown to have caused it. Temperatures
are worded in the city's own unit.

    python tools/wxpredict/day_review.py [--days 3] [--dry-run]
"""
import argparse
import bisect
import collections
import datetime as dt
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common, features  # noqa: E402

VERSION = "day_review:v1"
JOB = "P7.7_day_review"
TABLE = "derived_day_review"
DAYS = 3                    # settled dates reviewed each night (a late settlement is reviewed again)
DAYS_HISTORY = 60           # the city's own differences the z is taken against
MIN_HISTORY = 20            # fewer days: no z
WINDOW_LOCAL = (9, 17)      # the afternoon the forecast's own aggregates cover (best_match_daily *_09_17)
RAIN_FC_MM = 0.5            # a forecast of rain: more than this by 17h
CHECKPOINTS = ("d1_eve", "morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
WHAT = {"cloud_pct": ("cloud", "%"), "td_c": ("dew point", "C"), "wind_kmh": ("wind", "km/h")}
# A difference is worded only when it is unusual for the city (|z| >= 1) AND
# this large: a 0.1 C dew point gap can be unusual for a city whose forecast
# usually misses by a degree, and "drier than forecast" would mislead.
MIN_DELTA = {"cloud": 15.0, "dew point": 1.0, "wind": 5.0}


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
def afternoon(rep, day, tz, window=WINDOW_LOCAL):
    """What the station reported between the window's hours on `day`: mean
    cloud (% of the sky, from the most covered layer's oktas), mean dew point,
    mean wind (km/h), whether it rained (a rain, shower, drizzle or thunder
    code, or measured precipitation), and how many reports. None with under
    six reports."""
    a = dt.datetime.combine(day, dt.time(window[0]), tz).timestamp()
    b = dt.datetime.combine(day, dt.time(window[1]), tz).timestamp()
    i0, i1 = bisect.bisect_left(rep.t, a), bisect.bisect_left(rep.t, b)
    recs = rep.rec[i0:i1]
    if len(recs) < 6:
        return None
    idx = {f: k for k, f in enumerate(features.Reports.FIELDS)}

    def mean(f, scale=1.0):
        v = [r[idx[f]] for r in recs if r[idx[f]] is not None]
        return statistics.fmean(v) * scale if v else None
    # the report's weather as features.WX_FLAGS' bits: rain, shower, thunder, snow, fog
    wet = [i for i, (name, _) in enumerate(features.WX_FLAGS) if name in ("rain", "shower", "thunder")]
    precip = [r[idx["precip_in"]] for r in recs if r[idx["precip_in"]] is not None]
    oktas = mean("sky_oktas")
    return {"cloud_pct": None if oktas is None else oktas / 8 * 100, "td_c": mean("td_c"),
            "wind_kmh": mean("wind_kt", 1.852),
            "rain": any(r[idx["wx"]][i] for r in recs for i in wet) or sum(precip) > 0,
            "reports": len(recs)}


def forecast_of(bm_row):
    """The lead-1 forecast's afternoon, in afternoon()'s terms."""
    num = features.num
    if not bm_row:
        return None
    return {"tmax_c": num(bm_row.get("tmax_c")), "cloud_pct": num(bm_row.get("cloud_09_17_pct")),
            "td_c": num(bm_row.get("td_09_17_c")), "wind_kmh": num(bm_row.get("wind_09_17_kmh")),
            "rain": (num(bm_row.get("precip_00_17_mm")) or 0) > RAIN_FC_MM}


def zscore(delta, history):
    """delta against the city's own past differences (None under MIN_HISTORY)."""
    h = [x for x in history if x is not None]
    if delta is None or len(h) < MIN_HISTORY:
        return None
    sd = statistics.pstdev(h)
    return None if sd == 0 else (delta - statistics.fmean(h)) / sd


def surprises(fc, obs, history):
    """[{what, forecast, observed, delta, z}] for cloud, dew point and wind,
    largest |z| first (a difference without a z last), and rain when the
    forecast and the station disagreed. history: {key: [past deltas]}."""
    out = []
    for key, (name, unit) in WHAT.items():
        f, o = fc.get(key), obs.get(key)
        if f is None or o is None:
            continue
        d = o - f
        out.append({"what": name, "unit": unit, "forecast": round(f, 1), "observed": round(o, 1),
                    "delta": round(d, 1), "z": None if (z := zscore(d, history.get(key, []))) is None
                    else round(z, 2)})
    out.sort(key=lambda s: (s["z"] is None, -abs(s["z"] or 0)))
    if fc.get("rain") is not None and obs.get("rain") is not None and fc["rain"] != obs["rain"]:
        out.append({"what": "rain", "unit": "", "forecast": fc["rain"], "observed": obs["rain"],
                    "delta": None, "z": None})
    return out


def in_unit(c, unit, delta=False):
    """A Celsius temperature (or difference) in the city's unit, one decimal."""
    return f"{c * 9 / 5 + (0 if delta else 32) if unit == 'F' else c:.1f}"


def sentence(error_c, peak_local_h, usual_h, found, unit="C"):
    """One line: how far the day was from the forecast, and the differences
    that were unusual for the city (|z| >= 1) and large (MIN_DELTA), in their
    direction. Temperatures in the city's unit."""
    if error_c is None:
        return "No day-ahead forecast to compare with."
    u = "F" if unit == "F" else "C"
    side = "warmer" if error_c > 0 else "cooler" if error_c < 0 else "as warm as"
    parts = [f"{in_unit(abs(error_c), u, delta=True)} {u} {side} than forecast" if error_c
             else "exactly as forecast"]
    for s in found:
        if s["what"] == "rain":
            parts.append("rain the forecast did not have" if s["observed"] else "no rain where the forecast had it")
        elif s["z"] is not None and abs(s["z"]) >= 1 and abs(s["delta"]) >= MIN_DELTA[s["what"]]:
            more = {"cloud": ("cloudier", "clearer"), "dew point": ("more humid", "drier"),
                    "wind": ("windier", "calmer")}[s["what"]]
            if s["unit"] == "C":
                seen = f"{in_unit(s['observed'], u)} vs {in_unit(s['forecast'], u)} {u}"
            else:
                seen = f"{s['observed']:g} vs {s['forecast']:g} {s['unit']}"
            parts.append(f"{more[0] if s['delta'] > 0 else more[1]} than forecast ({seen})")
    if peak_local_h is not None and usual_h is not None and abs(peak_local_h - usual_h) >= 2:
        parts.append(f"the maximum came {'late' if peak_local_h > usual_h else 'early'} "
                     f"({fmt_hour(peak_local_h)} vs {fmt_hour(usual_h)} usually)")
    return "; ".join(parts) + "."


def fmt_hour(h):
    return f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}"


def calls(checkpoint_rows, wxrd_rows, labels=None):
    """Per checkpoint, in the day's order: the engine's top bucket, its
    probability on the winner and whether it hit; the market's; wxrd-v2's;
    each top bucket's label. checkpoint_rows: fact_checkpoint_outcome joined
    with the served top; wxrd_rows: {checkpoint: variant row}; labels:
    {band_id: label}."""
    labels = labels or {}
    out = []
    for r in sorted(checkpoint_rows, key=lambda r: CHECKPOINTS.index(r["checkpoint"])
                    if r["checkpoint"] in CHECKPOINTS else 99):
        w = wxrd_rows.get(r["checkpoint"])
        win = r.get("winner_band_id")
        out.append({
            "checkpoint": r["checkpoint"],
            "engine_top": r.get("top_band_id"), "engine_p_win": r.get("model_prob_on_winner"),
            "engine_hit": r.get("hit"),
            "market_top": r.get("market_top_band_id"), "market_p_win": r.get("market_prob_on_winner"),
            "market_hit": r.get("market_hit"),
            "wxrd_top": w["top_band_id"] if w else None,
            "wxrd_p_win": (w["probs"] or {}).get(win) if w else None,
            "wxrd_hit": (w["top_band_id"] == win) if w and win else None,
        })
        for who in ("engine", "market", "wxrd"):
            out[-1][f"{who}_label"] = labels.get(out[-1][f"{who}_top"])
    return out


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def review_city(city, c, dates, sdays_all, bm, md, nightly, outcomes, wxrd, labels):
    """The rows of one city's settled dates."""
    from wxpredict import build_table
    st = (c.get("icao") or "").upper()
    tz = common.zone(c)
    rep = build_table.load_reports(st, nightly) if st else features.Reports([])
    sdays = sdays_all.get(st, {})
    rows = []
    for d in dates:
        day = dt.date.fromisoformat(d)
        cp = outcomes.get((city, d), [])
        if not cp:
            continue
        winner = next((r["winner_band_id"] for r in cp if r.get("winner_band_id")), None)
        fc = forecast_of(bm.get((city, 1, d)))
        obs = afternoon(rep, day, tz)
        history = collections.defaultdict(list)
        for k in range(1, DAYS_HISTORY + 1):
            pd_ = (day - dt.timedelta(days=k)).isoformat()
            pf, po = forecast_of(bm.get((city, 1, pd_))), afternoon(rep, day - dt.timedelta(days=k), tz)
            if pf and po:
                for key in WHAT:
                    if pf.get(key) is not None and po.get(key) is not None:
                        history[key].append(po[key] - pf[key])
        station = sdays.get(d)
        tmax_c = station[0] if station else None
        peak_h = station[2] if station else None
        usual = features.climatology(sdays, day)[3]
        err = None if tmax_c is None or not fc or fc.get("tmax_c") is None else tmax_c - fc["tmax_c"]
        models = {m: v[0] for m, v in (md.get((city, 1, d)) or {}).items() if v and v[0] is not None}
        closest = min(models, key=lambda m: abs(models[m] - tmax_c)) if models and tmax_c is not None else None
        found = surprises(fc or {}, obs or {}, history) if fc and obs else []
        rows.append({
            "city_key": city, "target_date": d, "unit": c.get("unit"),
            "winner_band_id": winner, "winner_label": labels.get(winner),
            "station_max_c": None if tmax_c is None else round(tmax_c, 2),
            "peak_local_hour": None if peak_h is None else round(peak_h, 2),
            "usual_peak_hour": None if usual is None else round(usual, 2),
            "forecast_max_c": None if not fc or fc.get("tmax_c") is None else round(fc["tmax_c"], 2),
            "forecast_error_c": None if err is None else round(err, 2),
            "models": {m: round(v, 2) for m, v in sorted(models.items())},
            "closest_model": closest,
            "afternoon": {"forecast": fc, "observed": obs},
            "surprises": found,
            "calls": calls(cp, wxrd.get((city, d), {}), labels),
            "why": sentence(err, peak_h, usual, found, c.get("unit")),
            "version": VERSION,
        })
    return rows


def run(days=DAYS, dry_run=False, now=None):
    t0 = time.time()
    ops = common.ops()
    now = now or dt.datetime.now(dt.timezone.utc)
    first = (now.date() - dt.timedelta(days=days + 1)).isoformat()
    last = (now.date() - dt.timedelta(days=1)).isoformat()
    outcome_rows = ops.rest_all("fact_checkpoint_outcome", [
        ("select", "checkpoint_id,city_key,target_date,checkpoint,winner_band_id,hit,model_prob_on_winner,"
                   "market_top_band_id,market_hit,market_prob_on_winner"),
        ("target_date", f"gte.{first}"), ("target_date", f"lte.{last}")], order="checkpoint_id.asc")
    tops = {r["checkpoint_id"]: r["top_band_id"] for r in ops.rest_all("prediction_checkpoints", [
        ("select", "checkpoint_id,top_band_id"), ("target_date", f"gte.{first}"), ("target_date", f"lte.{last}")],
        order="checkpoint_id.asc")}
    outcomes = collections.defaultdict(list)
    for r in outcome_rows:
        outcomes[(r["city_key"], str(r["target_date"]))].append(dict(r, top_band_id=tops.get(r["checkpoint_id"])))
    wxrd = collections.defaultdict(dict)
    for r in ops.rest_all("variant_shadow_checkpoints", [
            ("select", "city_key,target_date,checkpoint,top_band_id,probs"), ("variant", "eq.wxrd"),
            ("target_date", f"gte.{first}"), ("target_date", f"lte.{last}")], order="shadow_id.asc"):
        wxrd[(r["city_key"], str(r["target_date"]))][r["checkpoint"]] = r
    band_ids = sorted({b for v in outcomes.values() for r in v
                       for b in (r.get("winner_band_id"), r.get("top_band_id"), r.get("market_top_band_id")) if b}
                      | {r["top_band_id"] for v in wxrd.values() for r in v.values() if r.get("top_band_id")})
    labels = {}
    for i in range(0, len(band_ids), 200):
        for b in ops.rest_all("bands", [("select", "band_id,band_label"),
                                        ("band_id", f"in.({','.join(band_ids[i:i + 200])})")], order="band_id.asc"):
            labels[b["band_id"]] = b["band_label"]
    from wxpredict import build_table
    sdays, _ = build_table.load_station_daily()
    bm, md = build_table.load_daily_forecasts()
    nightly = common.nightly_reports()
    cities = common.active_cities()
    dates = sorted({d for (_, d) in outcomes})
    rows = []
    for city, c in sorted(cities.items()):
        rows += review_city(city, c, dates, sdays, bm, md, nightly, outcomes, wxrd, labels)
    if rows and not dry_run:
        ops.upsert_replace(TABLE, rows, "city_key,target_date")
    detail = {"version": VERSION, "dates": dates, "rows": len(rows),
              "with_forecast_error": sum(r["forecast_error_c"] is not None for r in rows),
              "with_afternoon": sum(r["afternoon"]["observed"] is not None for r in rows),
              "seconds": round(time.time() - t0, 1)}
    if not dry_run:
        ops.log_run(JOB, "ok" if rows else "partial", len(rows), detail)
    return detail, rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    detail, rows = run(args.days, args.dry_run)
    print(json.dumps(detail, indent=1))
    if args.dry_run:
        print(json.dumps(rows[:3], indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
