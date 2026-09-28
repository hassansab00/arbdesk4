"""Where the market beats the model, and whether the model knows anything the
market's price does not. Research: nothing here prices or trades.

WHY
---
Every live comparison so far says the market prices the venue's ladder better
than the desk (fact_checkpoint_outcome, 24-27 Sep: morning log loss 1.846 vs
1.140), but on a handful of days. tools/market_history.py fetched the venue's
own record: every "Highest temperature" event since Dec 2025, its ladder, its
winner, and the hourly price of every bucket. With it, the question is asked
on every city-day the market and the desk's day-before forecasts both cover.

THE QUESTIONS AND THE CANDIDATES ARE FIXED HERE, BEFORE ANY SCORE WAS SEEN
--------------------------------------------------------------------------
Cutoffs (the city's wall clock, day D):
  d0_00  00:00 on D. The model's inputs (the seven models' day-before runs,
         hours 00-17 local, lead 1) are out by then (data/training/
         previous_runs/README.md): nothing later than the cutoff is used.
  d0_08  08:00 on D. The same model; the market has had eight more hours
         (overnight runs, early readings). Conservative for the model.
The market's ladder at a cutoff: each bucket's last hourly price at or before
it and at most MAX_AGE_H old; a city-day counts only when every bucket has
one. Prices are floored at PRICE_FLOOR (the venue's lowest tick) and the
ladder is normalised to 1 (its sum before that is reported as the overround).

The model (what the engine serves day-ahead since 27 Sep, rebuilt from the
committed record with the engine's own code): scripts/station_correction.py's
`fit` on the WINDOW_DAYS before D (each model's lead-1 00-17 maximum against
the station's whole-day maximum), `combine` for D, the width from `fit_width`
on the combination's own out-of-sample errors of the WIDTH_DAYS before D, and
probability_engine.compute_band_probabilities on the venue's ladder. No
reading of day D is used (no floor).

Truth: the venue's winner only (exactly one bucket resolved YES). Events on a
settlement station other than the city's current one (the ICAO at the end of
the event's settlement page, `station_icao`) are left out and counted: the model is trained on
the current station.

Q1  Who prices the ladder better: log loss on the winner (primary), Brier,
    top-pick hit; the paired difference per city-day, its mean and a 90%
    interval from a bootstrap over dates (BOOT resamples, seed SEED).
Q2  Where the model loses - centre or width: distance in buckets from each
    side's median bucket to the winner (mean |d| and mean d), each side's
    stated spread (SD of the bucket index), and realised / stated variance
    (1 = calibrated; above 1 = too sure).
Q3  Does the model add anything to the price. Walk-forward by calendar month:
    fitted on every earlier month, scored on the month, from the first month
    with MIN_TRAIN city-days behind it:
      market      the normalised ladder
      recal       market^a, renormalised (the market's own over/under-confidence)
      pooled      market^a x model^b, renormalised (log-linear pooling: a
                  conditional logit over the ladder, fitted by Newton)
      anchor      market + w (model - market), w in [0, 1] (market_anchor.py)
    Gain = market log loss - candidate log loss per city-day, mean and 90%
    date-bootstrap interval. The model adds information only if pooled beats
    recal with an interval above 0.
Q4  Where: Q3's pooled-vs-recal gain by unit, by month, and by how far the
    model's mean bucket sits from the market's (terciles fixed on the
    training data of each fold would be cleaner; here terciles are over all
    scored rows and say where, not how much to trust).
Q5  Is the market itself calibrated: every bucket at each cutoff, binned by
    its normalised price; realised rate, mean price, Wilson 95% interval.

Inputs, all committed: data/training/market_history/ (market_history.py),
data/training/previous_runs/models_daily.csv.gz, data/replay/
inputs_2026-09-26/{labels_whole.json.gz,tz.json}, data/mirror/cities.

    PYTHONPATH=scripts python tools/market_vs_model.py > docs/MODEL_VS_MARKET_2026-09-28.md
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
import station_correction as sc                              # noqa: E402
from probability_engine import compute_band_probabilities   # noqa: E402

MH = "data/training/market_history/"
PR = "data/training/previous_runs/models_daily.csv.gz"
RI = "data/replay/inputs_2026-09-26/"
CUTOFFS = {"d0_00": 0, "d0_08": 8}
MAX_AGE_H = 3
PRICE_FLOOR = 0.0005
MODEL_FLOOR = 1e-6                    # probability_engine's PROB_FLOOR
LEAD = 1
FIELD = "tmax_00_17_c"
WIDTH_DAYS = sc.WIDTH_DAYS
MIN_TRAIN = 1000
A_BOUNDS = (0.25, 4.0)
B_BOUNDS = (0.0, 2.0)
BOOT = 4000
SEED = 11
PRICE_BINS = [0, .02, .05, .10, .20, .30, .40, .50, .60, .70, .80, .90, 1.0001]


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def read_csv(path):
    with gzip.open(path, "rt", newline="") as f:
        yield from csv.DictReader(f)


def load_cities():
    path = sorted(glob.glob("data/mirror/cities/cities-*.csv.gz"))[-1]
    return {r["city_key"]: r for r in read_csv(path)}, path


def load_events(tz, cities):
    """{event_id: {...}} of the scorable events, and the counts left out."""
    out, left = {}, defaultdict(int)
    main_days = {(r["city_key"], r["date"]) for r in read_csv(MH + "events.csv.gz") if r["listing"] == "main"}
    for r in read_csv(MH + "events.csv.gz"):
        city = r["city_key"]
        if city not in tz:
            left["city not active"] += 1
            continue
        if r["closed"] != "1":
            left["not closed"] += 1
            continue
        if r["listing"] == "arch" and (city, r["date"]) in main_days:
            left["arch twin of a main listing"] += 1
            continue
        if r["station_icao"] != (cities[city]["icao"] or "").upper():
            left["other settlement station"] += 1
            continue
        out[r["event_id"]] = {"city": city, "date": r["date"], "unit": r["unit"], "bands": []}
    for r in read_csv(MH + "bands.csv.gz"):
        e = out.get(r["event_id"])
        if e is None:
            continue
        e["bands"].append({"band_id": int(r["band_index"]),
                           "band_lo": float(r["band_lo"]) if r["band_lo"] else None,
                           "band_hi": float(r["band_hi"]) if r["band_hi"] else None,
                           "open_low": r["open_low"] == "1", "open_high": r["open_high"] == "1",
                           "winner": r["winner"]})
    for eid in list(out):
        e = out[eid]
        wins = [b["band_id"] for b in e["bands"] if b["winner"] == "1"]
        if len(wins) != 1 or any(b["winner"] not in ("0", "1") for b in e["bands"]):
            left["not exactly one winner"] += 1
            del out[eid]
            continue
        e["bands"].sort(key=lambda b: b["band_id"])
        e["winner"] = wins[0]
        zone = ZoneInfo(tz[e["city"]])
        day = dt.date.fromisoformat(e["date"])
        e["cut"] = {k: int(dt.datetime.combine(day, dt.time(h), zone).timestamp()) for k, h in CUTOFFS.items()}
    return out, dict(left)


def market_ladders(events):
    """{(event_id, cutoff): (probs by band index, overround)} for complete ladders."""
    best = {}
    for r in read_csv(MH + "prices.csv.gz"):
        e = events.get(r["event_id"])
        if e is None:
            continue
        t = int(r["t"])
        for k, c in e["cut"].items():
            if c - MAX_AGE_H * 3600 <= t <= c:
                key = (r["event_id"], k, int(r["band_index"]))
                if key not in best or t >= best[key][0]:
                    best[key] = (t, float(r["p"]))
    out = {}
    for eid, e in events.items():
        for k in CUTOFFS:
            ps = [best.get((eid, k, b["band_id"])) for b in e["bands"]]
            if any(p is None for p in ps):
                continue
            raw = [max(PRICE_FLOOR, p[1]) for p in ps]
            s = sum(raw)
            out[(eid, k)] = ([p / s for p in raw], s)
    return out


def load_model_inputs():
    labels = {(c, str(d)[:10]): float(m) for c, d, m, *_ in json.load(gzip.open(RI + "labels_whole.json.gz"))}
    fc = defaultdict(dict)
    pairs = []
    for r in read_csv(PR):
        if int(r["lead_days"]) != LEAD or not r[FIELD]:
            continue
        key = (r["city_key"], r["for_date"])
        fc[key][r["model"]] = float(r[FIELD])
        if key in labels:
            pairs.append((key[0], key[1], r["model"], LEAD, float(r[FIELD]), labels[key]))
    return labels, fc, pairs


class Model:
    """The served day-ahead recipe, fitted only on days before the one priced."""

    def __init__(self, labels, fc, pairs):
        self.labels, self.fc = labels, fc
        self.by_day = defaultdict(list)
        for p in pairs:
            self.by_day[p[1]].append(p)
        self.days = sorted(self.by_day)
        self.tables, self.oos, self.widths = {}, {}, {}

    def table(self, day):
        if day not in self.tables:
            lo = (dt.date.fromisoformat(day) - dt.timedelta(days=sc.WINDOW_DAYS)).isoformat()
            self.tables[day] = sc.fit([p for d in self.days if lo <= d < day for p in self.by_day[d]])
        return self.tables[day]

    def centre(self, city, day):
        key = (city, day)
        if key not in self.oos:
            models = self.fc.get(key)
            out = sc.combine(models, self.table(day), city, LEAD) if models else None
            self.oos[key] = out[0] if out else None
        return self.oos[key]

    def width(self, city, day):
        if day not in self.widths:
            d0 = dt.date.fromisoformat(day)
            errors = defaultdict(list)
            for i in range(1, WIDTH_DAYS + 1):
                d = (d0 - dt.timedelta(days=i)).isoformat()
                for c in {k[0] for k in self.by_day_cities(d)}:
                    mu = self.centre(c, d)
                    y = self.labels.get((c, d))
                    if mu is not None and y is not None:
                        errors[(LEAD, c)].append(y - mu)
            self.widths[day] = sc.fit_width(errors)
        return sc.width_for(self.widths[day], city, LEAD)

    def by_day_cities(self, day):
        return [(p[0],) for p in self.by_day.get(day, [])]

    def ladder(self, e):
        mu = self.centre(e["city"], e["date"])
        if mu is None:
            return None
        sigma = self.width(e["city"], e["date"])
        if sigma is None:
            return None
        probs = compute_band_probabilities(mu, sigma, e["unit"], e["bands"])
        raw = [max(MODEL_FLOOR, p) for _b, p in probs]
        s = sum(raw)
        return [p / s for p in raw], mu, sigma


# --------------------------------------------------------------------------
# scores
# --------------------------------------------------------------------------

def logloss(p, w):
    return -math.log(p[w])


def brier(p, w):
    return sum((q - (1.0 if i == w else 0.0)) ** 2 for i, q in enumerate(p))


def top(p):
    return max(range(len(p)), key=lambda i: p[i])


def median_index(p):
    c = 0.0
    for i, q in enumerate(p):
        c += q
        if c >= 0.5:
            return i
    return len(p) - 1


def moments(p):
    m = sum(i * q for i, q in enumerate(p))
    return m, sum((i - m) ** 2 * q for i, q in enumerate(p))


def pool(mkt, mdl, a, b):
    lg = [a * math.log(m) + b * math.log(d) for m, d in zip(mkt, mdl)]
    top_ = max(lg)
    ex = [math.exp(v - top_) for v in lg]
    s = sum(ex)
    return [v / s for v in ex]


def fit_pool(rows, with_model=True):
    """(a, b) maximising the log likelihood of market^a x model^b over the
    ladder, by Newton from (1, 0), clipped to the bounds."""
    a, b = 1.0, 0.0
    for _ in range(50):
        g = [0.0, 0.0]
        h = [[0.0, 0.0], [0.0, 0.0]]
        for mkt, mdl, w in rows:
            x = [math.log(m) for m in mkt]
            y = [math.log(d) for d in mdl] if with_model else [0.0] * len(mkt)
            p = pool(mkt, mdl, a, b if with_model else 0.0)
            ex = sum(pi * xi for pi, xi in zip(p, x))
            ey = sum(pi * yi for pi, yi in zip(p, y))
            g[0] += x[w] - ex
            g[1] += y[w] - ey
            vxx = sum(pi * (xi - ex) ** 2 for pi, xi in zip(p, x))
            vyy = sum(pi * (yi - ey) ** 2 for pi, yi in zip(p, y))
            vxy = sum(pi * (xi - ex) * (yi - ey) for pi, xi, yi in zip(p, x, y))
            h[0][0] -= vxx
            h[1][1] -= vyy
            h[0][1] -= vxy
        h[1][0] = h[0][1]
        if not with_model:
            step = (-g[0] / h[0][0], 0.0) if h[0][0] else (0.0, 0.0)
        else:
            det = h[0][0] * h[1][1] - h[0][1] ** 2
            if abs(det) < 1e-12:
                break
            step = (-(h[1][1] * g[0] - h[0][1] * g[1]) / det, -(-h[1][0] * g[0] + h[0][0] * g[1]) / det)
        a_new = min(max(a + step[0], A_BOUNDS[0]), A_BOUNDS[1])
        b_new = min(max(b + step[1], B_BOUNDS[0]), B_BOUNDS[1]) if with_model else 0.0
        if abs(a_new - a) < 1e-7 and abs(b_new - b) < 1e-7:
            a, b = a_new, b_new
            break
        a, b = a_new, b_new
    return a, b


def fit_anchor(rows):
    best = None
    for i in range(101):
        w = i / 100
        ll = sum(logloss([m + w * (d - m) for m, d in zip(mkt, mdl)], win) for mkt, mdl, win in rows)
        if best is None or ll < best[0]:
            best = (ll, w)
    return best[1]


def boot(per_row, seed=SEED):
    """Mean and 90% interval of the per-row values, resampling dates."""
    by_date = defaultdict(list)
    for d, v in per_row:
        by_date[d].append(v)
    dates = sorted(by_date)
    n_all = sum(len(v) for v in by_date.values())
    mean = sum(sum(v) for v in by_date.values()) / n_all
    rng = random.Random(seed)
    sums = {d: (sum(v), len(v)) for d, v in by_date.items()}
    stats = []
    for _ in range(BOOT):
        s = n = 0
        for _i in range(len(dates)):
            a, b = sums[dates[rng.randrange(len(dates))]]
            s += a
            n += b
        stats.append(s / n)
    stats.sort()
    return mean, stats[int(0.05 * BOOT)], stats[int(0.95 * BOOT)]


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def f(x, n=3):
    return f"{x:.{n}f}"


def iv(t, n=3, sign=True):
    m, lo, hi = t
    s = "+" if sign else ""
    return f"{m:{s}.{n}f} [{lo:{s}.{n}f}, {hi:{s}.{n}f}]"


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def main():
    tz = json.load(open(RI + "tz.json"))
    cities, cities_path = load_cities()
    events, left = load_events(tz, cities)
    ladders = market_ladders(events)
    labels, fc, pairs = load_model_inputs()
    model = Model(labels, fc, pairs)
    last_fc = max(d for _c, d in fc)

    rows = defaultdict(list)           # cutoff -> [row]
    no_model = defaultdict(int)
    for eid, e in sorted(events.items(), key=lambda kv: (kv[1]["date"], kv[1]["city"])):
        if e["date"] > last_fc:
            continue
        m = model.ladder(e)
        for k in CUTOFFS:
            if (eid, k) not in ladders:
                continue
            if m is None:
                no_model[k] += 1
                continue
            mkt, over = ladders[(eid, k)]
            rows[k].append({"eid": eid, "city": e["city"], "date": e["date"], "unit": e["unit"],
                            "month": e["date"][:7], "w": e["winner"], "mkt": mkt, "mdl": m[0],
                            "over": over, "mu": m[1], "sigma": m[2]})

    p = print
    p("# Model against market, on the venue's own record (28 Sep 2026)")
    p()
    p("Generated by `tools/market_vs_model.py` from committed inputs only "
      "(`data/training/market_history/`, `data/training/previous_runs/`, "
      f"`{RI}`, `{cities_path}`). The questions, cutoffs and candidates are fixed in the "
      "tool's docstring, written before any score was computed. Research only: nothing here prices.")
    p()
    p("## The sample")
    p()
    p(f"- Scorable events (active city, resolved, exactly one winner, settled on the city's current station): "
      f"**{len(events):,}**. Left out: " + ", ".join(f"{k} {v:,}" for k, v in sorted(left.items())) + ".")
    for k in CUTOFFS:
        rs = rows[k]
        if not rs:
            p(f"- `{k}`: no rows.")
            continue
        dates = sorted({r["date"] for r in rs})
        over = sorted(r["over"] for r in rs)
        p(f"- `{k}`: **{len(rs):,}** city-days with a complete market ladder and a model price, "
          f"{len(dates)} dates ({dates[0]} to {dates[-1]}), {len({r['city'] for r in rs})} cities "
          f"(C {sum(r['unit'] == 'C' for r in rs):,}, F {sum(r['unit'] == 'F' for r in rs):,}); "
          f"no model price {no_model[k]:,}. Overround median {f(over[len(over) // 2])}.")
    p(f"- The model's inputs end {last_fc}; later events are not scored.")
    p()

    p("## Q1. Who prices the ladder better")
    p()
    p("Mean over city-days; the difference is model minus market (negative log loss difference = model "
      "better), with its 90% interval from a bootstrap over dates.")
    p()
    p("| cutoff | n | log loss model / market | difference | Brier model / market | top pick model / market |")
    p("|---|---|---|---|---|---|")
    for k in CUTOFFS:
        rs = rows[k]
        if not rs:
            continue
        d = boot([(r["date"], logloss(r["mdl"], r["w"]) - logloss(r["mkt"], r["w"])) for r in rs])
        llm = sum(logloss(r["mdl"], r["w"]) for r in rs) / len(rs)
        llk = sum(logloss(r["mkt"], r["w"]) for r in rs) / len(rs)
        bm = sum(brier(r["mdl"], r["w"]) for r in rs) / len(rs)
        bk = sum(brier(r["mkt"], r["w"]) for r in rs) / len(rs)
        hm = sum(top(r["mdl"]) == r["w"] for r in rs) / len(rs)
        hk = sum(top(r["mkt"]) == r["w"] for r in rs) / len(rs)
        p(f"| {k} | {len(rs):,} | {f(llm)} / {f(llk)} | {iv(d)} | {f(bm)} / {f(bk)} | "
          f"{100 * hm:.1f}% / {100 * hk:.1f}% |")
    p()
    for unit in ("C", "F"):
        p(f"**{unit} only:** " + "; ".join(
            f"`{k}` n {len([r for r in rows[k] if r['unit'] == unit]):,}, log loss "
            + f(sum(logloss(r['mdl'], r['w']) for r in rows[k] if r['unit'] == unit)
                / max(1, len([r for r in rows[k] if r['unit'] == unit])))
            + " / "
            + f(sum(logloss(r['mkt'], r['w']) for r in rows[k] if r['unit'] == unit)
                / max(1, len([r for r in rows[k] if r['unit'] == unit])))
            for k in CUTOFFS if rows[k]) + ".")
    p()

    p("## Q2. Centre or width")
    p()
    p("Distance in buckets from each side's median bucket to the winner (positive = the winner was "
      "higher), each side's stated spread (SD of the bucket index), and realised / stated variance "
      "around each side's mean bucket (1 = calibrated, above 1 = too sure).")
    p()
    p("| cutoff | side | mean abs distance | mean distance | median bucket = winner | stated SD | realised / stated variance |")
    p("|---|---|---|---|---|---|---|")
    for k in CUTOFFS:
        rs = rows[k]
        if not rs:
            continue
        for side in ("mdl", "mkt"):
            dist = [r["w"] - median_index(r[side]) for r in rs]
            mom = [moments(r[side]) for r in rs]
            real = sum((r["w"] - m) ** 2 for r, (m, _v) in zip(rs, mom)) / len(rs)
            stated = sum(v for _m, v in mom) / len(rs)
            p(f"| {k} | {'model' if side == 'mdl' else 'market'} | {f(sum(abs(x) for x in dist) / len(rs))} | "
              f"{f(sum(dist) / len(rs), 3)} | {100 * sum(x == 0 for x in dist) / len(rs):.1f}% | "
              f"{f(math.sqrt(stated))} | {f(real / stated)} |")
    p()

    p("## Q3. Does the model add anything to the price")
    p()
    p("Walk-forward by calendar month: every candidate is fitted on the months before and scored on the "
      "month. Gain = market log loss minus the candidate's, per city-day (positive = better than the "
      "market), 90% interval over dates.")
    p()
    q4_rows = {}
    for k in CUTOFFS:
        rs = rows[k]
        if not rs:
            continue
        months = sorted({r["month"] for r in rs})
        scored, fits = [], []
        for i, mo in enumerate(months):
            train = [(r["mkt"], r["mdl"], r["w"]) for r in rs if r["month"] < mo]
            if len(train) < MIN_TRAIN:
                continue
            a_r, _ = fit_pool(train, with_model=False)
            a_p, b_p = fit_pool(train, with_model=True)
            w_a = fit_anchor(train)
            fits.append((mo, len(train), a_r, a_p, b_p, w_a))
            for r in rs:
                if r["month"] != mo:
                    continue
                ll_m = logloss(r["mkt"], r["w"])
                ll_r = logloss(pool(r["mkt"], r["mdl"], a_r, 0.0), r["w"])
                ll_p = logloss(pool(r["mkt"], r["mdl"], a_p, b_p), r["w"])
                ll_a = logloss([m + w_a * (d - m) for m, d in zip(r["mkt"], r["mdl"])], r["w"])
                scored.append(dict(r, ll_m=ll_m, ll_r=ll_r, ll_p=ll_p, ll_a=ll_a))
        q4_rows[k] = scored
        if not scored:
            p(f"`{k}`: fewer than {MIN_TRAIN} training city-days in every fold.")
            continue
        p(f"**`{k}`** - {len(scored):,} scored city-days, months {fits[0][0]} to {fits[-1][0]}.")
        p()
        p("| candidate | gain over market | gain over recal |")
        p("|---|---|---|")
        p(f"| recal (market^a) | {iv(boot([(r['date'], r['ll_m'] - r['ll_r']) for r in scored]), 4)} | |")
        p(f"| pooled (market^a x model^b) | {iv(boot([(r['date'], r['ll_m'] - r['ll_p']) for r in scored]), 4)} | "
          f"{iv(boot([(r['date'], r['ll_r'] - r['ll_p']) for r in scored]), 4)} |")
        p(f"| anchor (market + w (model - market)) | "
          f"{iv(boot([(r['date'], r['ll_m'] - r['ll_a']) for r in scored]), 4)} | |")
        p()
        p("| fold (scored month) | training city-days | recal a | pooled a | pooled b | anchor w |")
        p("|---|---|---|---|---|---|")
        for mo, n, a_r, a_p, b_p, w_a in fits:
            p(f"| {mo} | {n:,} | {f(a_r)} | {f(a_p)} | {f(b_p)} | {w_a:.2f} |")
        p()

    p("## Q4. Where the model adds (pooled over recal, same folds)")
    p()
    for k, scored in q4_rows.items():
        if not scored:
            continue
        p(f"**`{k}`**")
        p()
        p("| slice | n | gain of pooled over recal |")
        p("|---|---|---|")
        for unit in ("C", "F"):
            s = [r for r in scored if r["unit"] == unit]
            if s:
                p(f"| unit {unit} | {len(s):,} | {iv(boot([(r['date'], r['ll_r'] - r['ll_p']) for r in s]), 4)} |")
        for mo in sorted({r["month"] for r in scored}):
            s = [r for r in scored if r["month"] == mo]
            p(f"| month {mo} | {len(s):,} | {iv(boot([(r['date'], r['ll_r'] - r['ll_p']) for r in s]), 4)} |")
        gaps = sorted(abs(moments(r["mdl"])[0] - moments(r["mkt"])[0]) for r in scored)
        t1, t2 = gaps[len(gaps) // 3], gaps[2 * len(gaps) // 3]
        for name, lo, hi in (("model near the market", -1, t1), ("middle", t1, t2), ("model far from the market", t2, 1e9)):
            s = [r for r in scored if lo < abs(moments(r["mdl"])[0] - moments(r["mkt"])[0]) <= hi]
            if s:
                p(f"| {name} (mean-bucket gap in ({f(max(lo, 0), 2)}, {f(min(hi, 99), 2)}]) | {len(s):,} | "
                  f"{iv(boot([(r['date'], r['ll_r'] - r['ll_p']) for r in s]), 4)} |")
        p()

    p("## Q5. Is the market calibrated")
    p()
    p("Every bucket of every scored ladder, by its normalised price at the cutoff.")
    p()
    for k in CUTOFFS:
        rs = rows[k]
        if not rs:
            continue
        p(f"**`{k}`**")
        p()
        p("| price bin | buckets | mean price | realised | Wilson 95% |")
        p("|---|---|---|---|---|")
        for lo, hi in zip(PRICE_BINS, PRICE_BINS[1:]):
            cells = [(q, i == r["w"]) for r in rs for i, q in enumerate(r["mkt"]) if lo <= q < hi]
            if not cells:
                continue
            n = len(cells)
            k_ = sum(1 for _q, y in cells if y)
            a, b = wilson(k_, n)
            p(f"| [{lo:.2f}, {min(hi, 1):.2f}) | {n:,} | {f(sum(q for q, _ in cells) / n, 4)} | "
              f"{f(k_ / n, 4)} | [{f(a, 4)}, {f(b, 4)}] |")
        p()


if __name__ == "__main__":
    main()
