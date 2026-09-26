"""P7.2 stage 1: the remaining-day distribution (scripts/remaining_day.py) scored
walk-forward, THROUGH THE MODULE THAT SHIPS, against the floor-atom Gaussian
around max(R, the forecast's day maximum) - roughly what the engine's same-day
path prices from before its trajectory layer.

Each test month (Nov 2025 on) is fitted only on the days before it
(remaining_day.fit_hour). Scored on the whole degree in the city's unit
(log loss floored at 1e-6, the most likely whole degree's hit rate) and on
CRPS in C, bootstrap by date for the log-loss gain.

    python tools/experiments_p72_stage1.py \
        data/training/previous_runs/best_match_hourly_day1_utc.csv.gz obs_utc.csv.gz \
        labels.json units.json tz.json data/training/previous_runs/models_daily.csv.gz

obs_utc.csv.gz, labels.json, units.json, tz.json: as tools/experiments_p72_remaining_day.py.
labels.json must hold WHOLE days only (common.day_had_ended): on 26 Sep the
day's partial rows were 4.1 C short on average.

HOW THE FORM WAS CHOSEN (26 Sep, scratch runs of the same data and folds,
numpy): A (P(set) + lognormal rise) alone, B (floor atom around a ridge's rise)
alone, each with and without city effects, and their equal mix; bucket log loss
09/11/13/15h - A 1.924/1.669/1.290/0.700, B 1.805/1.644/1.298/0.741, mix
1.801/1.616/1.257/0.684 (city effects on; without them B was 1.882 at 09h).
The mix was picked on those months, so its edge over the better single form
there is not a confirmed result.

The first cut (PR #198: two-part form alone, sampled buckets) is in this file's
git history. RESULT: see docs/PLAN_PROGRESS.md, P7.2.
"""
import csv, gzip, json, math, random, sys, datetime as dt
from collections import defaultdict
from multiprocessing import Pool
from statistics import pstdev
from zoneinfo import ZoneInfo

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "scripts"))
import remaining_day as rd  # noqa: E402

HOURS = (9, 11, 13, 15)
FIRST_TEST = dt.date(2025, 11, 1)
UTC = dt.timezone.utc


def load(fc_path, obs_path, labels_path, units_path, tz_path, models_path):
    TZ = json.load(open(tz_path))
    unit = json.load(open(units_path))
    Y = {(c, str(d)[:10]): float(m) for c, d, m, *_ in json.load(open(labels_path))}
    fc = defaultdict(lambda: defaultdict(dict))
    with gzip.open(fc_path, "rt") as f:
        for r in csv.DictReader(f):
            if r["city_key"] not in TZ or r["t"] == "":
                continue
            t = dt.datetime.fromisoformat(r["utc"]).replace(tzinfo=UTC).astimezone(ZoneInfo(TZ[r["city_key"]]))
            fc[r["city_key"]][t.date().isoformat()][t.hour] = (
                float(r["t"]), float(r["cloud"]) if r["cloud"] else None, float(r["sw"]) if r["sw"] else None)
    obs = defaultdict(lambda: defaultdict(list))
    with gzip.open(obs_path, "rt") as f:
        for r in csv.DictReader(f):
            c = r["city_key"]
            if c not in TZ:
                continue
            t = dt.datetime.fromisoformat(r["utc"]).replace(tzinfo=UTC).astimezone(ZoneInfo(TZ[c]))
            obs[c][t.date().isoformat()].append((t.hour + t.minute / 60, float(r["temp_c"])))
    sp = defaultdict(list)
    with gzip.open(models_path, "rt") as f:
        for r in csv.DictReader(f):
            if r["lead_days"] == "1" and r["tmax_c"]:
                sp[(r["city_key"], r["for_date"])].append(float(r["tmax_c"]))
    spread = {k: pstdev(v) for k, v in sp.items() if len(v) >= 4}
    med = sorted(spread.values())[len(spread) // 2]
    return fc, obs, Y, unit, spread, med


def rows_for(H, fc, obs, Y, spread, med):
    out = []
    for c, days in obs.items():
        for d, series in days.items():
            y = Y.get((c, d))
            f = fc.get(c, {}).get(d)
            if y is None or not f:
                continue
            day = dt.date.fromisoformat(d)
            row = rd.features(series, f, H, day, spread.get((c, d), med))
            if row:
                row.update(city=c, date=day, y=y)
                out.append(row)
    return out


def phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def whole(v, u):
    return round(v * 9 / 5 + 32) if u == "F" else round(v)


def score(F, R, y, u):
    """(log p(truth bucket), top bucket == truth, CRPS C) for a CDF F."""
    truth = whole(y, u)
    edge = (lambda k: k - 0.5) if u != "F" else (lambda k: (k - 0.5 - 32) * 5 / 9)
    p = F(edge(truth + 1)) - F(edge(truth))
    ks = range(whole(R, u) - 1, whole(R, u) + 26)
    Fe = [F(edge(k)) for k in ks] + [F(edge(ks[-1] + 1))]
    top = ks[max(range(len(ks)), key=lambda i: Fe[i + 1] - Fe[i])]
    lo, step = min(R, y) - 0.5, 0.1
    crps = sum((F(lo + i * step) - (1.0 if lo + i * step >= y else 0.0)) ** 2 for i in range(260)) * step
    return math.log(max(p, 1e-6)), top == truth, crps


def run(args):
    H, paths = args
    fc, obs, Y, unit, spread, med = load(*paths)
    data = rows_for(H, fc, obs, Y, spread, med)
    months = sorted({(r["date"].year, r["date"].month) for r in data})
    res, versions = [], []
    for yy, mm in months:
        start = dt.date(yy, mm, 1)
        if start < FIRST_TEST:
            continue
        train = [r for r in data if r["date"] < start]
        test = [r for r in data if (r["date"].year, r["date"].month) == (yy, mm)]
        p = rd.fit_hour(train)
        if p is None or not test:
            continue
        versions.append(rd.version_of({H: p}))
        base_c = [max(r["R"], r["fc_day"]) for r in train]
        sig0 = max(0.5, sum(abs(b - r["y"]) for b, r in zip(base_c, train)) / len(train) * 1.25)
        for r in test:
            u = unit.get(r["city"], "C")
            d = rd.distribution(p, r["city"], r["x"], r["R"])
            m = score(lambda v: rd.cdf(d, v), r["R"], r["y"], u)
            c0 = max(r["R"], r["fc_day"])
            b = score(lambda v: 0.0 if v < r["R"] else phi((v - c0) / sig0), r["R"], r["y"], u)
            q10, q90 = rd.quantile(d, 0.1), rd.quantile(d, 0.9)
            res.append((r["date"], m, b, r["city"], r["x"][-1], q10 <= r["y"] <= q90, q90 - q10))
    by = defaultdict(list)
    for x in res:
        by[x[0]].append(x)
    days = list(by)
    rng = random.Random(1)
    gains = []
    for _ in range(300):
        smp = [rng.choice(days) for _ in days]
        v = [x[1][0] - x[2][0] for dd in smp for x in by[dd]]
        gains.append(sum(v) / len(v))
    gains.sort()
    n = len(res)
    sp = sorted(x[4] for x in res)
    cuts = (sp[n // 3], sp[2 * n // 3])
    terc = {}
    for name, keep in (("low", lambda v: v < cuts[0]), ("mid", lambda v: cuts[0] <= v < cuts[1]),
                       ("high", lambda v: v >= cuts[1])):
        xs = [x for x in res if keep(x[4])]
        terc[name] = {"n": len(xs), "spread_c": [round(min(x[4] for x in xs), 2), round(max(x[4] for x in xs), 2)],
                      "cover80": round(sum(x[5] for x in xs) / len(xs), 3),
                      "width80_c": round(sum(x[6] for x in xs) / len(xs), 2)}
    return {"interval80_by_disagreement": terc, "hour": H, "n": n, "days": len(days), "cities": len({x[3] for x in res}), "fits": len(versions),
            "model": {"logloss": round(-sum(x[1][0] for x in res) / n, 3),
                      "hit": round(sum(x[1][1] for x in res) / n, 4),
                      "crps": round(sum(x[1][2] for x in res) / n, 3)},
            "base": {"logloss": round(-sum(x[2][0] for x in res) / n, 3),
                     "hit": round(sum(x[2][1] for x in res) / n, 4),
                     "crps": round(sum(x[2][2] for x in res) / n, 3)},
            "logloss_gain": round(sum(x[1][0] - x[2][0] for x in res) / n, 3),
            "gain_ci95": [round(gains[7], 3), round(gains[292], 3)]}


if __name__ == "__main__":
    paths = sys.argv[1:7]
    with Pool(4) as pool:
        for out in pool.imap(run, [(H, paths) for H in HOURS]):
            print(json.dumps(out), flush=True)
