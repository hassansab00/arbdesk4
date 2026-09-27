"""Plan v2.3 P3.9 part 3: the width of the distribution actually served.

P3.9 replaced the day-ahead centre with the station-corrected combination and
kept the engine's own sigma, which was fitted to the RAW forecast's errors.
This measures, on committed inputs only, whether a width fitted to the
corrected combination's own out-of-sample errors prices the venue's ladder
better. Research: nothing here prices.

THE CANDIDATES ARE FIXED HERE, before any score was seen (P7.3 v2.3 contract):
  W0      the engine's own day-ahead sigma: its last pricing before the city's
          local day began (data/mirror/band_probabilities) - what is served now
  W1      1.2533 x the pooled mean |error| of the corrected combination
  W1rms   the pooled root-mean-square error
  W2      W1 per city, shrunk to the pool: (sum|e| + K mae_pool) / (n + K), K = 10
  W3      EMOS-style a + b x spread (the corrected sources' SD), a and b by
          grid search for the lowest mean CRPS
Every fitted width learns from the combination's errors on the TRAIN_DAYS days
before the scored day, each of those errors itself out of sample (the
combination for day t is fitted on the WINDOW_DAYS days before t). The centre
is the corrected combination throughout (what is served since 27 Sep); only
the width differs. Scored with probability_engine.compute_band_probabilities
on the venue's own ladder, against the venue's winner, and by CRPS and 80%
coverage against the station maximum.

Inputs (all committed): data/training/previous_runs/models_daily.csv.gz (the
seven models' day-before maxima), data/replay/inputs_2026-09-26 (venue
markets, bands, winners; whole-day station labels; zones), and
data/mirror/band_probabilities (the engine's pricings). Output: a Markdown
report on stdout.

    PYTHONPATH=scripts python tools/p39_served_width.py > docs/P39_SERVED_WIDTH_2026-09-27.md
"""
import csv
import datetime as dt
import glob
import gzip
import json
import math
import random
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.path.insert(0, "scripts")
import station_correction as sc                      # noqa: E402
from probability_engine import compute_band_probabilities   # noqa: E402

LEAD = 1
TRAIN_DAYS = 30
K_CITY = 10
MAE_TO_SIGMA = 1.2533
SCORE_FROM, SCORE_TO = "2026-09-13", "2026-09-25"
# From 17 Sep the served width sat at the level it has now (the per-date table
# shows it; 28 Sep's day-ahead markets, priced 27 Sep 16:36Z, average 1.531 C).
# 13-16 Sep served wider (14 Sep: 6.1 C). This cut is chosen from the served
# width's own history, not from any outcome.
TODAY_LIKE_FROM = "2026-09-17"
A_GRID = [round(0.2 + 0.1 * i, 2) for i in range(24)]        # 0.2 .. 2.5
B_GRID = [round(0.1 * i, 2) for i in range(21)]              # 0.0 .. 2.0
BOOT = 4000
SEED = 11
I = "data/replay/inputs_2026-09-26/"


def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def crps_normal(y, mu, sigma):
    z = (y - mu) / sigma
    pdf = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    return sigma * (z * (2 * norm_cdf(z) - 1) + 2 * pdf - 1 / math.sqrt(math.pi))


def load():
    labels = {(c, str(d)[:10]): float(m) for c, d, m, *_ in json.load(gzip.open(I + "labels_whole.json.gz"))}
    fc = defaultdict(dict)                                   # (city, day) -> {model: tmax} at LEAD
    pairs = []
    with gzip.open("data/training/previous_runs/models_daily.csv.gz", "rt") as f:
        for r in csv.DictReader(f):
            if not r["tmax_c"]:
                continue
            lead = int(r["lead_days"])
            key = (r["city_key"], r["for_date"])
            if lead == LEAD:
                fc[key][r["model"]] = float(r["tmax_c"])
            if key in labels and lead in sc.LEADS:
                pairs.append((r["city_key"], r["for_date"], r["model"], lead, float(r["tmax_c"]), labels[key]))
    inp = json.load(gzip.open(I + "replay_inputs.json.gz"))
    markets = {m[0]: {"city": m[1], "date": str(m[2]), "winner": m[3], "unit": m[4], "tz": m[5]}
               for m in inp["markets"]}
    bands = defaultdict(list)
    for b in inp["bands"]:
        bands[b[1]].append({"band_id": b[0], "band_lo": b[2], "band_hi": b[3], "open_low": b[4], "open_high": b[5]})
    return labels, fc, pairs, markets, bands


def served_sigma(markets, bands):
    """{market_id: sigma} of the engine's last pricing before the local day began."""
    band_market = {b["band_id"]: mid for mid, bs in bands.items() for b in bs}
    start = {}
    for mid, m in markets.items():
        zone = ZoneInfo(m["tz"])                           # each market carries its city's zone
        start[mid] = dt.datetime.combine(dt.date.fromisoformat(m["date"]), dt.time(0), zone)
    best = {}
    for path in sorted(glob.glob("data/mirror/band_probabilities/*.csv.gz")):
        with gzip.open(path, "rt") as f:
            for r in csv.DictReader(f):
                mid = band_market.get(r["band_id"])
                if mid is None or not r["sigma_c"] or not r["lead_days"] or int(r["lead_days"]) < 1:
                    continue
                t = dt.datetime.fromisoformat(r["computed_at"])
                if t >= start[mid]:
                    continue
                if mid not in best or t > best[mid][0]:
                    best[mid] = (t, float(r["sigma_c"]))
    return {mid: s for mid, (t, s) in best.items()}


def main():
    labels, fc, pairs, markets, bands = load()
    served = served_sigma(markets, bands)
    by_day = defaultdict(list)
    for p in pairs:
        by_day[p[1]].append(p)
    days_all = sorted(by_day)
    fits = {}

    def table_for(day):
        if day not in fits:
            lo = (dt.date.fromisoformat(day) - dt.timedelta(days=sc.WINDOW_DAYS)).isoformat()
            fits[day] = sc.fit([p for d in days_all if lo <= d < day for p in by_day[d]])
        return fits[day]

    # every city-day's out-of-sample corrected combination and its error
    oos = {}
    first_needed = (dt.date.fromisoformat(SCORE_FROM) - dt.timedelta(days=TRAIN_DAYS)).isoformat()
    for (city, day), models in sorted(fc.items()):
        if not (first_needed <= day <= SCORE_TO):
            continue
        out = sc.combine(models, table_for(day), city, LEAD)
        if out is None:
            continue
        oos[(city, day)] = (out[0], out[1])

    def widths(city, day):
        lo = (dt.date.fromisoformat(day) - dt.timedelta(days=TRAIN_DAYS)).isoformat()
        tr = [(c, oos[(c, d)][0], oos[(c, d)][1], labels[(c, d)]) for (c, d) in oos
              if lo <= d < day and (c, d) in labels]
        if len(tr) < 200:
            return None
        errs = [y - mu for _c, mu, _s, y in tr]
        mae = sum(abs(e) for e in errs) / len(errs)
        rms = math.sqrt(sum(e * e for e in errs) / len(errs))
        mine = [abs(y - mu) for c, mu, _s, y in tr if c == city]
        w2 = MAE_TO_SIGMA * (sum(mine) + K_CITY * mae) / (len(mine) + K_CITY)
        best = None
        for a in A_GRID:
            for b in B_GRID:
                crps = sum(crps_normal(y, mu, a + b * (s or 0.0)) for _c, mu, s, y in tr) / len(tr)
                if best is None or crps < best[0]:
                    best = (crps, a, b)
        return {"W1": MAE_TO_SIGMA * mae, "W1rms": rms, "W2": w2, "W3": (best[1], best[2])}

    rows = []
    skipped = defaultdict(int)
    for mid, m in sorted(markets.items(), key=lambda kv: (kv[1]["date"], kv[1]["city"])):
        city, day = m["city"], m["date"]
        if not (SCORE_FROM <= day <= SCORE_TO):
            continue
        if mid not in served:
            skipped["no engine pricing before the day"] += 1
            continue
        if (city, day) not in oos:
            skipped["no corrected combination (fewer than MIN_SOURCES)"] += 1
            continue
        if (city, day) not in labels:
            skipped["no whole-day station label"] += 1
            continue
        ladder = bands[mid]
        if sum(1 for b in ladder if b["band_id"] == m["winner"]) != 1:
            skipped["winner not on the ladder"] += 1
            continue
        w = widths(city, day)
        if w is None:
            skipped["fewer than 200 training errors"] += 1
            continue
        mu, spread = oos[(city, day)]
        y = labels[(city, day)]
        sig = {"W0": served[mid], "W1": w["W1"], "W1rms": w["W1rms"], "W2": w["W2"],
               "W3": max(0.25, w["W3"][0] + w["W3"][1] * (spread or 0.0))}
        out = {"day": day, "city": city, "unit": m["unit"]}
        for k, s in sig.items():
            probs = dict(compute_band_probabilities(mu, s, m["unit"], ladder))
            pw = max(probs.get(m["winner"], 0.0), 1e-6)
            top = max(sorted(probs), key=probs.get)
            q = 1.2816 * s
            out[k] = {"sigma": s, "ll": -math.log(pw), "brier": sum((p - (b == m["winner"])) ** 2 for b, p in probs.items()),
                      "hit": top == m["winner"], "crps": crps_normal(y, mu, s), "cover80": abs(y - mu) <= q}
        rows.append(out)

    keys = ["W0", "W1", "W1rms", "W2", "W3"]
    dates = sorted({r["day"] for r in rows})
    n = len(rows)
    print("# P3.9 part 3: the width around the served centre (27 Sep 2026)\n")
    print("Generated by `tools/p39_served_width.py` from committed inputs only. Day-ahead (lead 1), the "
          "station-corrected combination as the centre (what is served since 27 Sep), each width scored on the "
          "venue's own ladder and winner, and by CRPS and 80% coverage against the station maximum. "
          "Candidates were fixed in the tool's header before any score was seen.\n")
    print(f"City-days scored: {n}, over {len(dates)} dates ({dates[0]} to {dates[-1]}); "
          f"{sum(1 for r in rows if r['unit'] == 'F')} in F. Left out: "
          + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none") + ".\n")
    print("| width | mean sigma C | log loss | Brier | top-1 hit | CRPS C | 80% coverage |")
    print("|---|---|---|---|---|---|---|")
    for k in keys:
        mean = lambda f: sum(f(r[k]) for r in rows) / n
        print(f"| {k} | {mean(lambda x: x['sigma']):.3f} | {mean(lambda x: x['ll']):.4f} | "
              f"{mean(lambda x: x['brier']):.4f} | {mean(lambda x: x['hit']):.3f} | "
              f"{mean(lambda x: x['crps']):.4f} | {mean(lambda x: x['cover80']):.3f} |")
    print()
    rng = random.Random(SEED)
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["day"]].append(r)
    def gains(ds, label):
        sub = [r for d in ds for r in by_date[d]]
        print(f"Gain against W0 (the width the engine served), {label}: {len(sub)} city-days over {len(ds)} dates. "
              f"Per city-day; positive is better. Day-block bootstrap, {BOOT} resamples of the dates.\n")
        print("| width | log-loss gain | 90% interval | CRPS gain C | 90% interval | top-1 hit W0 -> width |")
        print("|---|---|---|---|---|---|")
        for k in keys[1:]:
            gl, gc = [], []
            for _ in range(BOOT):
                pick = [ds[rng.randrange(len(ds))] for _ in ds]
                xs = [r for d in pick for r in by_date[d]]
                gl.append(sum(r["W0"]["ll"] - r[k]["ll"] for r in xs) / len(xs))
                gc.append(sum(r["W0"]["crps"] - r[k]["crps"] for r in xs) / len(xs))
            gl.sort(); gc.sort()
            lo, hi = int(0.05 * BOOT), int(0.95 * BOOT) - 1
            point_l = sum(r["W0"]["ll"] - r[k]["ll"] for r in sub) / len(sub)
            point_c = sum(r["W0"]["crps"] - r[k]["crps"] for r in sub) / len(sub)
            h0 = sum(r["W0"]["hit"] for r in sub) / len(sub)
            hk = sum(r[k]["hit"] for r in sub) / len(sub)
            print(f"| {k} | {point_l:+.4f} | [{gl[lo]:+.4f}, {gl[hi]:+.4f}] | {point_c:+.4f} | "
                  f"[{gc[lo]:+.4f}, {gc[hi]:+.4f}] | {h0:.3f} -> {hk:.3f} |")
        print()

    gains(dates, "every scored date")
    gains([d for d in dates if d >= TODAY_LIKE_FROM], f"from {TODAY_LIKE_FROM}, when the served width was at today's level")
    print("The width served changes nightly (the skill and post-process refits). Mean width per scored date:\n")
    print("| date | city-days | W0 served | W1 | W2 | W3 |")
    print("|---|---|---|---|---|---|")
    for d in dates:
        sub = by_date[d]
        cells = " | ".join(f"{sum(r[k]['sigma'] for r in sub) / len(sub):.3f}" for k in ("W0", "W1", "W2", "W3"))
        print(f"| {d} | {len(sub)} | {cells} |")
    print()
    for unit in ("C", "F"):
        sub = [r for r in rows if r["unit"] == unit]
        if not sub:
            continue
        cells = " | ".join(f"{k} {sum(r[k]['ll'] for r in sub) / len(sub):.4f}" for k in keys)
        print(f"- {unit} ({len(sub)} city-days), log loss: {cells}")


if __name__ == "__main__":
    main()
