#!/usr/bin/env python3
"""The same-day lane of the forecast evaluation contract, fec-v1
(docs/FORECAST_EVALUATION_CONTRACT.md): the incumbent S10 (`rd1`) against
Challenger A (`rd2`, the readings received by the decision time), scored on the
venue's own ladders and winners (data/training/market_history), refitted at
each cutoff on identical training rows, with a date-clustered bootstrap.

    python tools/fec_same_day.py --period dev      # test months Jan-Jul 2026
    python tools/fec_same_day.py --period holdout  # 1 Aug - 25 Sep 2026, run once
    python tools/fec_same_day.py --period holdout --lag-min 0   # sensitivity

Inputs (committed; their sha256 is written into the output):
  data/training/previous_runs/best_match_hourly_day1_utc.csv.gz  day-before hourly forecast
  data/training/previous_runs/models_daily.csv.gz                the models' spread
  data/replay/inputs_2026-09-26/obs_utc.csv.gz                   station readings (valid times)
  data/replay/inputs_2026-09-26/labels_whole_repaired.json.gz    station maxima, training labels
  data/replay/inputs_2026-09-26/units.json, tz.json
  data/training/market_history/events.csv.gz, bands.csv.gz, prices.csv.gz  the venue
  data/eval/fec_v1/stations.json                                 the observed station per city

Writes data/eval/fec_v1/sd_<period>_lag<m>.json (the summary) and
sd_<period>_lag<m>_rows.csv.gz (one row per city-day-hour, both models and the
market). The same inputs give the same bytes.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import random
import sys
import tempfile
from collections import defaultdict
from multiprocessing import Pool
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import experiments_p72_stage1 as E  # noqa: E402  (the loaders the S10 fit uses)
import remaining_day as rd          # noqa: E402

CONTRACT = "fec-v1"
UTC = dt.timezone.utc
HOURS = rd.HOURS
TICK_MINUTE_UTC = 36
DEV_MONTHS = [(2026, m) for m in range(1, 8)]
HOLDOUT = (dt.date(2026, 8, 1), dt.date(2026, 9, 25))
BOOT, SEED = 1000, 11
FLOOR = 1e-6

P = lambda *a: os.path.join(ROOT, *a)
INPUTS = {
    "fc": P("data/training/previous_runs/best_match_hourly_day1_utc.csv.gz"),
    "models": P("data/training/previous_runs/models_daily.csv.gz"),
    "obs": P("data/replay/inputs_2026-09-26/obs_utc.csv.gz"),
    "labels": P("data/replay/inputs_2026-09-26/labels_whole_repaired.json.gz"),
    "units": P("data/replay/inputs_2026-09-26/units.json"),
    "tz": P("data/replay/inputs_2026-09-26/tz.json"),
    "events": P("data/training/market_history/events.csv.gz"),
    "bands": P("data/training/market_history/bands.csv.gz"),
    "prices": P("data/training/market_history/prices.csv.gz"),
    "stations": P("data/eval/fec_v1/stations.json"),
}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
def decision_time(day, H, tz):
    """(local fractional hour, UTC datetime) of the hourly tick inside local
    hour H of `day`, or None when the :36 UTC tick does not fall in that hour."""
    zone = ZoneInfo(tz)
    start = dt.datetime.combine(day, dt.time(H, 0), zone).astimezone(UTC)
    tick = start.replace(minute=TICK_MINUTE_UTC, second=0, microsecond=0)
    if tick < start:
        tick += dt.timedelta(hours=1)
    local = tick.astimezone(zone)
    if local.date() != day or local.hour != H:
        return None
    return H + local.minute / 60, tick


def score(probs, winner, order):
    """(log loss, top-1 hit, multiclass Brier) of a {band: p} ladder."""
    pw = probs.get(winner, 0.0)
    top = max(order, key=lambda b: (probs.get(b, 0.0), -order.index(b)))
    brier = sum((probs.get(b, 0.0) - (1.0 if b == winner else 0.0)) ** 2 for b in order)
    return -math.log(max(pw, FLOOR)), top == winner, brier


def market_ladder(series_of, bands, t_unix):
    """{band: normalised p} from each bucket's first hourly price in
    [t, t + 1 h], or None when any bucket has none or the raw sum is off."""
    raw = {}
    for b in bands:
        pts = series_of.get(b["band_id"])
        if not pts:
            return None
        v = next((p for ts, p in pts if t_unix <= ts <= t_unix + 3600), None)
        if v is None:
            return None
        raw[b["band_id"]] = v
    s = sum(raw.values())
    if not 0.9 <= s <= 1.1:
        return None
    return {k: v / s for k, v in raw.items()}


def cluster_boot(pairs, seed=SEED, n=BOOT):
    """pairs: [(date, value)]. Mean and 90%/95% intervals, dates resampled."""
    by = defaultdict(list)
    for d, v in pairs:
        by[d].append(v)
    days = sorted(by)
    sums = {d: (sum(by[d]), len(by[d])) for d in days}
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        s = c = 0
        for _ in days:
            a, b = sums[rng.choice(days)]
            s += a
            c += b
        means.append(s / c)
    means.sort()
    tot = sum(v for _, v in pairs) / len(pairs)
    q = lambda p: round(means[min(n - 1, max(0, int(p * n)))], 4)
    return {"mean": round(tot, 4), "ci90": [q(0.05), q(0.95)], "ci95": [q(0.025), q(0.975)],
            "dates": len(days), "n": len(pairs)}


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_venue():
    icao = json.load(open(INPUTS["stations"]))["icao"]
    events, excluded = {}, defaultdict(int)
    with gzip.open(INPUTS["events"], "rt") as f:
        rows = list(csv.DictReader(f))
    # a 'main' listing wins over an 'arch-' twin for the same city-day
    rows.sort(key=lambda r: (r["listing"] != "main", r["event_id"]))
    for r in rows:
        key = (r["city_key"], r["date"])
        if key in events:
            continue
        if r["closed"] != "1":
            excluded["not_resolved"] += 1
            continue
        if not r["station_icao"] or r["station_icao"] != icao.get(r["city_key"]):
            excluded["settlement_station_not_the_observed_station"] += 1
            continue
        events[key] = {"event_id": r["event_id"], "unit": r["unit"], "bands": []}
    by_event = {v["event_id"]: v for v in events.values()}
    with gzip.open(INPUTS["bands"], "rt") as f:
        for r in csv.DictReader(f):
            e = by_event.get(r["event_id"])
            if e is None:
                continue
            e["bands"].append({
                "band_id": f"{r['event_id']}:{r['band_index']}", "band_index": int(r["band_index"]),
                "band_lo": float(r["band_lo"]) if r["band_lo"] else None,
                "band_hi": float(r["band_hi"]) if r["band_hi"] else None,
                "open_low": r["open_low"] == "1", "open_high": r["open_high"] == "1",
                "winner": r["winner"] == "1"})
    out = {}
    for key, e in events.items():
        e["bands"].sort(key=lambda b: b["band_index"])
        winners = [b["band_id"] for b in e["bands"] if b["winner"]]
        if len(winners) != 1:
            excluded["not_exactly_one_winner"] += 1
            continue
        e["winner"] = winners[0]
        out[key] = e
    return out, dict(excluded)


def load_prices(event_ids):
    series = defaultdict(list)
    with gzip.open(INPUTS["prices"], "rt") as f:
        for r in csv.DictReader(f):
            if r["event_id"] in event_ids:
                series[f"{r['event_id']}:{r['band_index']}"].append((int(r["t"]), float(r["p"])))
    for v in series.values():
        v.sort()
    return series


def _labels_file():
    out = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    with gzip.open(INPUTS["labels"], "rt") as f:
        out.write(f.read())
    out.close()
    return out.name


# ---------------------------------------------------------------------------
# the rows, shared with worker processes by fork
# ---------------------------------------------------------------------------
G = {}


def build(lag_min):
    fc, obs, Y, unit, spread, med = E.load(INPUTS["fc"], INPUTS["obs"], _labels_file(),
                                           INPUTS["units"], INPUTS["tz"], INPUTS["models"])
    tz = json.load(open(INPUTS["tz"]))
    lag = lag_min / 60
    rows = defaultdict(list)          # H -> [row]
    counts = defaultdict(int)
    for c, days in obs.items():
        for d, series in days.items():
            f = fc.get(c, {}).get(d)
            if not f:
                counts["no_forecast"] += 1
                continue
            day = dt.date.fromisoformat(d)
            y = Y.get((c, d))
            sp = spread.get((c, d), med)
            for H in HOURS:
                dtm = decision_time(day, H, tz[c])
                if dtm is None:
                    counts["no_tick_in_hour"] += 1
                    continue
                t, tick = dtm
                avail = rd.available(series, t, lag)
                inc = rd.features(avail, f, H, day, sp)
                ch = rd.features_at(avail, f, t, day, sp)
                if inc is None or ch is None:
                    counts[f"no_row_{'both' if inc is None and ch is None else ('rd1' if inc is None else 'rd2')}"] += 1
                    continue
                rows[H].append({"city": c, "date": day, "y": y, "t": t, "tick": int(tick.timestamp()),
                                "inc": inc, "ch": ch, "unit": unit.get(c, "C")})
    return rows, dict(counts)


def _fit(task):
    H, cutoff, which = task
    tr = [r for r in G["rows"][H] if r["date"] < cutoff and r["y"] is not None]
    feed = [{"city": r["city"], "x": r[which]["x"], "R": r[which]["R"], "y": r["y"], "date": r["date"]} for r in tr]
    return task, rd.fit_hour(feed)


def _score(task):
    import probability_engine as pe
    H, lo, hi, cutoff = task
    pi, pc = G["params"].get((H, cutoff, "inc")), G["params"].get((H, cutoff, "ch"))
    out = []
    if not pi or not pc:
        return out
    for r in G["rows"][H]:
        if not lo <= r["date"] <= hi or r["y"] is None:
            continue
        v = G["venue"].get((r["city"], r["date"].isoformat()))
        if v is None:
            continue
        bands, order = v["bands"], [b["band_id"] for b in v["bands"]]
        rec = {"city": r["city"], "date": r["date"].isoformat(), "hour": H, "t": round(r["t"], 3),
               "unit": v["unit"], "y_c": r["y"], "obs_age_h": round(r["ch"]["obs_age_h"], 3),
               "R_inc": r["inc"]["R"], "R_ch": r["ch"]["R"]}
        for name, p, feat in (("inc", pi, r["inc"]), ("ch", pc, r["ch"])):
            d = rd.distribution(p, r["city"], feat["x"], feat["R"])
            probs = dict(rd.ladder_probabilities(d, v["unit"], bands, pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP))
            ll, hit, brier = score(probs, v["winner"], order)
            med, q10, q90 = rd.median(d), rd.quantile(d, 0.1), rd.quantile(d, 0.9)
            rec.update({f"{name}_ll": round(ll, 6), f"{name}_hit": int(hit), f"{name}_brier": round(brier, 6),
                        f"{name}_med": round(med, 3), f"{name}_cover": int(q10 <= r["y"] <= q90),
                        f"{name}_width": round(q90 - q10, 3)})
        m = market_ladder(G["prices"], bands, r["tick"])
        if m is not None:
            ll, hit, brier = score(m, v["winner"], order)
            rec.update({"mkt_ll": round(ll, 6), "mkt_hit": int(hit), "mkt_brier": round(brier, 6)})
        out.append(rec)
    return out


def summarise(recs):
    def block(rs):
        n = len(rs)
        if not n:
            return {"n": 0}
        gain = cluster_boot([(r["date"], r["inc_ll"] - r["ch_ll"]) for r in rs])
        out = {"n": n, "dates": len({r["date"] for r in rs}), "cities": len({r["city"] for r in rs}),
               "logloss": {"rd1": round(sum(r["inc_ll"] for r in rs) / n, 4),
                           "rd2": round(sum(r["ch_ll"] for r in rs) / n, 4)},
               "gain_rd1_minus_rd2": gain}
        for m in ("inc", "ch"):
            k = sum(r[f"{m}_hit"] for r in rs)
            out[m] = {"top1": round(k / n, 4), "top1_wilson95": wilson(k, n),
                      "brier": round(sum(r[f"{m}_brier"] for r in rs) / n, 4),
                      "mae_c": round(sum(abs(r[f"{m}_med"] - r["y_c"]) for r in rs) / n, 4),
                      "bias_c": round(sum(r[f"{m}_med"] - r["y_c"] for r in rs) / n, 4),
                      "cover80": round(sum(r[f"{m}_cover"] for r in rs) / n, 4),
                      "width80_c": round(sum(r[f"{m}_width"] for r in rs) / n, 3)}
        mk = [r for r in rs if "mkt_ll" in r]
        if mk:
            out["market_subset"] = {"n": len(mk), "dates": len({r["date"] for r in mk}),
                                    "logloss": {"rd1": round(sum(r["inc_ll"] for r in mk) / len(mk), 4),
                                                "rd2": round(sum(r["ch_ll"] for r in mk) / len(mk), 4),
                                                "market": round(sum(r["mkt_ll"] for r in mk) / len(mk), 4)},
                                    "top1": {"rd1": round(sum(r["inc_hit"] for r in mk) / len(mk), 4),
                                             "rd2": round(sum(r["ch_hit"] for r in mk) / len(mk), 4),
                                             "market": round(sum(r["mkt_hit"] for r in mk) / len(mk), 4)},
                                    "gain_rd2_minus_market": cluster_boot([(r["date"], r["mkt_ll"] - r["ch_ll"]) for r in mk])}
        return out
    by_hour = {str(H): block([r for r in recs if r["hour"] == H]) for H in HOURS}
    after_hour = [r for r in recs if r["obs_age_h"] < (r["t"] - math.floor(r["t"]))]
    return {"pooled": block(recs), "by_hour": by_hour,
            "rows_with_a_reading_after_the_hour": block(after_hour),
            "by_city": {c: {"n": b["n"], "gain": b["gain_rd1_minus_rd2"]["mean"]}
                        for c in sorted({r["city"] for r in recs})
                        for b in [block([r for r in recs if r["city"] == c])]}}


def verdict(s):
    """fec-v1 §7, applied to the pooled block."""
    p = s["pooled"]
    g = p["gain_rd1_minus_rd2"]
    checks = {
        "gain_ci90_above_0": g["ci90"][0] > 0,
        "at_least_20_dates": p["dates"] >= 20,
        "no_hour_ci90_below_0": all(b.get("n", 0) == 0 or b["gain_rd1_minus_rd2"]["ci90"][1] >= 0
                                    for b in s["by_hour"].values()),
        "top1_not_lower_beyond_wilson": p["ch"]["top1"] >= p["inc"]["top1_wilson95"][0],
        "cover80_in_0.75_0.88": 0.75 <= p["ch"]["cover80"] <= 0.88,
    }
    if all(checks.values()):
        v = "accepted"
    elif g["ci90"][1] < 0:
        v = "rejected"
    else:
        v = "insufficient evidence" if not checks["gain_ci90_above_0"] else "not accepted"
    return v, checks


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=["dev", "holdout"], required=True)
    ap.add_argument("--lag-min", type=float, default=10.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out-dir", default=P("data/eval/fec_v1"))
    a = ap.parse_args(argv)

    rows, counts = build(a.lag_min)
    venue, excluded = load_venue()
    G["rows"], G["venue"] = rows, venue
    if a.period == "dev":
        windows = [(dt.date(y, m, 1), (dt.date(y, m + 1, 1) if m < 12 else dt.date(y + 1, 1, 1)) - dt.timedelta(days=1))
                   for y, m in DEV_MONTHS]
    else:
        windows = [HOLDOUT]
    cutoffs = sorted({lo for lo, _ in windows})
    G["prices"] = load_prices({v["event_id"] for (c, d), v in venue.items()
                               if any(lo <= dt.date.fromisoformat(d) <= hi for lo, hi in windows)})
    tasks = [(H, c, w) for H in HOURS for c in cutoffs for w in ("inc", "ch")]
    with Pool(a.workers) as pool:
        G["params"] = {t: p for t, p in pool.map(_fit, tasks)}
    # a NEW pool: workers are forked with the state they are given, and the
    # parameters did not exist when the first pool was made
    with Pool(a.workers) as pool:
        recs = [r for part in pool.map(_score, [(H, lo, hi, lo) for H in HOURS for lo, hi in windows]) for r in part]
    recs.sort(key=lambda r: (r["date"], r["city"], r["hour"]))
    s = summarise(recs)
    v, checks = verdict(s)
    tag = f"sd_{a.period}_lag{int(a.lag_min)}"
    versions = {f"{H}:{c.isoformat()}:{w}": (rd.version_of({H: p}, prefix="rd1" if w == "inc" else rd.VERSION_PREFIX_AT) if p else None)
                for (H, c, w), p in sorted(G["params"].items())}
    out = {"contract": CONTRACT, "period": a.period, "windows": [[lo.isoformat(), hi.isoformat()] for lo, hi in windows],
           "receipt_lag_min": a.lag_min, "as_of": "approximate (valid time <= decision - lag; no historical receipt times)",
           "decision": f"local H:MM, the :{TICK_MINUTE_UTC} UTC tick, H in {HOURS[0]}..{HOURS[-1]}",
           "inputs_sha256": {k: sha(v) for k, v in sorted(INPUTS.items())},
           "row_counts": counts, "venue_excluded": excluded, "venue_city_days": len(venue),
           "fits": versions, "summary": s, "verdict": v if a.period == "holdout" else "development only",
           "checks": checks}
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, tag + ".json"), "w") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    import io
    cols = sorted({k for r in recs for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in recs:
        w.writerow(r)
    with open(os.path.join(a.out_dir, tag + "_rows.csv.gz"), "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
            gz.write(buf.getvalue().encode("utf-8"))
    p = s["pooled"]
    print(json.dumps({"period": a.period, "lag_min": a.lag_min, "n": p.get("n"), "dates": p.get("dates"),
                      "logloss": p.get("logloss"), "gain": p.get("gain_rd1_minus_rd2"),
                      "top1": {m: p[m]["top1"] for m in ("inc", "ch")} if p.get("n") else None,
                      "verdict": out["verdict"], "checks": checks}, indent=1))


if __name__ == "__main__":
    main()
