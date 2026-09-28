"""Plan v2.4 P3.10 part 7: do the stations around the settlement airport know
something the market's price does not, during the day?

Hassan, 28 Sep: "we need our predictive model to win the single max temp
winner" and, for the next build, new information first. On the venue's record
the market beats our model at every checkpoint (docs/MODEL_VS_MARKET_2026-09-28.md),
reacts to the settlement station's own new readings within minutes
(docs/MARKET_REACTION_2026-09-28.md), and already prices even the US one-minute
readings (docs/ONE_MINUTE_READINGS_2026-09-28.md). What a trader watching the
settlement station does not see is the air around it: stations nearby that are
warmer than usual relative to it, or warming faster, may say where its
afternoon is going. This asks whether they do, against the market. Research
only: nothing here trades.

FIXED HERE, BEFORE ANY DATA WAS FETCHED
---------------------------------------
Stations. For each active city: its settlement station S (cities mirror, ICAO)
  and candidates from IEM's ASOS/METAR networks named in NETWORKS: stations
  with a four-letter id (three characters in the US networks, which use FAA
  ids: ORD for KORD; corrected before any reading was fetched), 10-150 km
  from S, the NEAREST_FETCH nearest. After
  fetching, the NEIGHBOURS nearest with a report in at least MIN_COVERAGE of
  the period's hours are the city's neighbours. A city with fewer than
  MIN_NEIGHBOURS is left out and named.
Readings. IEM's routine and special METARs (tmpf), 2025-12-01 to 2026-09-27,
  converted to C. A report counts at time t only if valid at or before
  t - LAG_MIN (the time to publish), and within STALE_MIN of t - LAG_MIN.
Checkpoints. 10:00, 12:00 and 14:00 local on the event's day.
The market. Each bucket's FIRST price at or after the checkpoint, at most
  AFTER_MIN later (never one before: #242); a city-day counts only when every
  bucket has one; floored at 0.0005 and normalised (market_vs_model).
Features, per city-day and checkpoint (C):
  level  = mean over neighbours of (T_N - T_S) at the checkpoint, minus the
           median of the same over the city's BASELINE_DAYS earlier days that
           have it at that checkpoint (at least BASELINE_MIN of them).
           Positive: the air around is warmer than usual relative to S.
  trend  = mean over neighbours of the rise over the last TREND_H hours minus
           S's rise over the same hours (each from its report nearest to
           t - TREND_H, within 45 min). Positive: the air around warms faster.
  Placebo: the same two, from the readings 24 hours earlier. They should add
           nothing; if they do, the pipeline is fooling itself.
The model. q_i proportional to m_i^a x exp((b1 level + b2 trend) c_i), m the
  market's ladder, c_i the bucket's centre in C minus the market's mean (an
  open tail's centre as if it were one more ordinary bucket). The baseline is
  recal, b1 = b2 = 0 (the market's own under-confidence). a, b1, b2 are fitted
  by maximum likelihood (Newton) on every earlier calendar month of the same
  checkpoint with at least MIN_TRAIN city-days; a month without that is not
  scored.
The question, per checkpoint: log loss of recal minus log loss with the
  neighbours, per city-day, with a 90% bootstrap over dates. The neighbours add
  information only if its lower bound is above zero. Beside it: the market's
  own log loss, the top pick's hit rate (the winner, Hassan's measure) of the
  market and of the model, and the placebo's gain.
  A trading test after costs is a later part, written down before it is run,
  and only for a checkpoint that passes here.

    python tools/p310_neighbours.py stations     # -> data/training/neighbours/candidates.csv
    python tools/p310_neighbours.py fetch        # -> data/training/neighbours/metar.csv.gz (resumable)
    python tools/p310_neighbours.py study > docs/NEIGHBOUR_STATIONS_2026-09-28.md
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_vs_model as mvm       # noqa: E402

OUT = "data/training/neighbours/"
CANDIDATES = OUT + "candidates.csv"
METAR = OUT + "metar.csv.gz"
CACHE = OUT + "cache/"
GEO = "https://mesonet.agron.iastate.edu/geojson/network/{}.geojson"
ASOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UA = {"User-Agent": "arbdesk4-research"}
START, END = dt.date(2025, 12, 1), dt.date(2026, 9, 27)

# The networks searched for each settlement station: its country's by ICAO
# prefix, a US station's state and the states around it.
NETWORKS = {
    "EH": ["NL__ASOS"], "LT": ["TR__ASOS"], "Z": ["CN__ASOS"], "SA": ["AR__ASOS"], "RK": ["KR__ASOS"],
    "FA": ["ZA__ASOS"], "EF": ["FI__ASOS"], "OE": ["SA__ASOS"], "OP": ["PK__ASOS"], "WM": ["MY__ASOS"],
    "EG": ["GB__ASOS"], "VI": ["IN__ASOS"], "LE": ["ES__ASOS"], "RP": ["PH__ASOS"], "MM": ["MX__ASOS"],
    "LI": ["IT__ASOS"], "UU": ["RU__ASOS"], "ED": ["DE__ASOS"], "MP": ["PA__ASOS"], "LF": ["FR__ASOS"],
    "SB": ["BR__ASOS"], "WS": ["SG__ASOS", "MY__ASOS"], "LL": ["IL__ASOS"], "RJ": ["JP__ASOS"],
    "CY": ["CA_ON_ASOS"], "EP": ["PL__ASOS"], "NZ": ["NZ__ASOS"],
    "KATL": ["GA_ASOS"], "KAUS": ["TX_ASOS"], "KORD": ["IL_ASOS", "IN_ASOS", "WI_ASOS"],
    "KDAL": ["TX_ASOS"], "KBKF": ["CO_ASOS"], "KHOU": ["TX_ASOS"], "KLAX": ["CA_ASOS"],
    "KMIA": ["FL_ASOS"], "KLGA": ["NY_ASOS", "NJ_ASOS", "CT_ASOS"], "KSFO": ["CA_ASOS"], "KSEA": ["WA_ASOS"],
}
MIN_KM, MAX_KM = 10, 150
NEAREST_FETCH = 8
NEIGHBOURS = 4
MIN_NEIGHBOURS = 2
MIN_COVERAGE = 0.70
LAG_MIN = 10
STALE_MIN = 90
TREND_H = 2
CHECKPOINTS = {"h10": 10, "h12": 12, "h14": 14}
AFTER_MIN = 60
BASELINE_DAYS = 30
BASELINE_MIN = 10
MIN_TRAIN = 1000
BOOT = 4000
SEED = 11


# --------------------------------------------------------------------------
# Stations
# --------------------------------------------------------------------------

def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def networks_for(icao):
    icao = icao.upper()
    return NETWORKS.get(icao) or NETWORKS.get(icao[:2]) or NETWORKS.get(icao[:1]) or []


def get(url, tries=5, parse=json.loads):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r:
                return parse(r.read().decode())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{url}: {err}")


def iem_id(icao):
    """IEM's id for a station: US networks use the FAA id (KORD -> ORD)."""
    icao = icao.upper()
    return icao[1:] if len(icao) == 4 and icao.startswith("K") else icao


def usable(sid, us):
    return (len(sid) == 3 and sid.isalnum()) if us else (len(sid) == 4 and sid.isalpha())


def candidates(settlement, stations, us=False):
    """[(station, km)] the NEAREST_FETCH nearest stations MIN_KM-MAX_KM from S.
    settlement: (IEM id, lat, lon); stations: {id: (lat, lon)}."""
    s, lat, lon = settlement
    out = []
    for sid, (la, lo) in stations.items():
        if sid == s or not usable(sid, us):
            continue
        d = km(lat, lon, la, lo)
        if MIN_KM <= d <= MAX_KM:
            out.append((sid, round(d, 1)))
    out.sort(key=lambda x: (x[1], x[0]))
    return out[:NEAREST_FETCH]


def cmd_stations():
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    nets = {}
    rows = []
    for city in sorted(tz):
        c = cities[city]
        icao = (c["icao"] or "").upper()
        pool = {}
        for n in networks_for(icao):
            if n not in nets:
                feats = get(GEO.format(n))["features"]
                nets[n] = {f["id"]: (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]) for f in feats}
            pool.update(nets[n])
        us = icao.startswith("K")
        sid0 = iem_id(icao)
        lat, lon = pool.get(sid0, (float(c["latitude"]), float(c["longitude"])))
        rows.append([city, sid0, "settlement", 0.0])
        for sid, d in candidates((sid0, lat, lon), pool, us):
            rows.append([city, sid, "candidate", d])
    os.makedirs(OUT, exist_ok=True)
    with open(CANDIDATES, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["city_key", "station", "role", "km"])
        w.writerows(rows)
    print(f"{len(rows)} rows, {sum(1 for r in rows if r[2] == 'candidate')} candidates, "
          f"{len({r[0] for r in rows})} cities -> {CANDIDATES}")


# --------------------------------------------------------------------------
# Readings
# --------------------------------------------------------------------------

def parse_asos(text):
    """[(station, epoch minute, temp C)] from IEM's onlycomma output."""
    out = []
    lines = text.splitlines()
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 3 or parts[2] in ("M", ""):
            continue
        try:
            t = dt.datetime.strptime(parts[1], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
            c = (float(parts[2]) - 32) * 5 / 9
        except ValueError:
            continue
        out.append((parts[0], int(t.timestamp()) // 60, round(c, 1)))
    return out


def fetch_station(station):
    """A station's readings over the period, cached one station a file (IEM refused nine at once: 503)."""
    path = CACHE + station + ".csv.gz"
    if not os.path.exists(path):
        q = [("station", station), ("data", "tmpf"), ("year1", START.year), ("month1", START.month),
             ("day1", START.day), ("year2", END.year), ("month2", END.month), ("day2", END.day),
             ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("missing", "M"), ("trace", "T"),
             ("direct", "no"), ("report_type", "3"), ("report_type", "4")]
        try:
            rows = get(f"{ASOS}?{urllib.parse.urlencode(q)}", parse=parse_asos)
        except RuntimeError as e:                  # left out this run; a re-run asks again
            print(f"{station}: unreached ({str(e)[-80:]})", file=sys.stderr)
            return []
        with gzip.open(path + ".tmp", "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["station", "minute", "temp_c"])
            w.writerows(rows)
        os.replace(path + ".tmp", path)
        print(f"{station}: {len(rows):,} readings", file=sys.stderr)
        time.sleep(2)
    with gzip.open(path, "rt") as f:
        return list(csv.DictReader(f))


def cmd_fetch():
    by_city = defaultdict(list)
    for r in csv.DictReader(open(CANDIDATES)):
        by_city[r["city_key"]].append(r["station"])
    os.makedirs(CACHE, exist_ok=True)
    for city, stations in sorted(by_city.items()):
        for st in stations:
            fetch_station(st)
    # the committed file: each city's settlement station and chosen neighbours
    hours = (END - START).days * 24 + 24
    chosen = {}
    cand = defaultdict(list)
    for r in csv.DictReader(open(CANDIDATES)):
        cand[r["city_key"]].append(r)
    for city, rs in sorted(cand.items()):
        got = defaultdict(set)
        for st in [r["station"] for r in rs]:
            for r in fetch_station(st):
                got[r["station"]].add(int(r["minute"]) // 60)
        s = rs[0]["station"]
        nb = [r["station"] for r in rs[1:] if len(got.get(r["station"], ())) >= MIN_COVERAGE * hours][:NEIGHBOURS]
        chosen[city] = {"settlement": s, "neighbours": nb,
                        "coverage": {st: round(len(v) / hours, 3) for st, v in got.items()}}
    with gzip.open(METAR + ".tmp", "wt", newline="") as out:
        w = csv.writer(out)
        w.writerow(["city_key", "station", "minute", "temp_c"])
        for city in sorted(cand):
            for st in [chosen[city]["settlement"]] + chosen[city]["neighbours"]:
                for r in fetch_station(st):
                    w.writerow([city, r["station"], r["minute"], r["temp_c"]])
    os.replace(METAR + ".tmp", METAR)
    json.dump(chosen, open(OUT + "chosen.json", "w"), indent=1, sort_keys=True)
    print(f"-> {METAR}, {OUT}chosen.json", file=sys.stderr)


# --------------------------------------------------------------------------
# The study
# --------------------------------------------------------------------------

def load_readings():
    """{(city, station): (sorted minutes, temps)}"""
    by = defaultdict(list)
    with gzip.open(METAR, "rt") as f:
        for r in csv.DictReader(f):
            by[(r["city_key"], r["station"])].append((int(r["minute"]), float(r["temp_c"])))
    out = {}
    for k, v in by.items():
        v.sort()
        out[k] = ([m for m, _t in v], [t for _m, t in v])
    return out


def reading_at(series, minute, window=STALE_MIN):
    """The latest temp valid at or before `minute`, within `window` minutes; else None."""
    import bisect
    ms, ts = series
    i = bisect.bisect_right(ms, minute) - 1
    if i < 0 or minute - ms[i] > window:
        return None
    return ts[i]


def reading_near(series, minute, window=45):
    """The temp nearest `minute`, within `window` minutes; else None."""
    import bisect
    ms, ts = series
    i = bisect.bisect_left(ms, minute)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(ms) and abs(ms[j] - minute) <= window and (best is None or abs(ms[j] - minute) < best[0]):
            best = (abs(ms[j] - minute), ts[j])
    return None if best is None else best[1]


def raw_features(readings, city, chosen, t_minute):
    """(level_raw, trend) at t, or None: S and at least MIN_NEIGHBOURS neighbours reporting."""
    s = chosen["settlement"]
    at = t_minute - LAG_MIN
    ts = readings.get((city, s))
    if ts is None:
        return None
    t_s = reading_at(ts, at)
    t_s0 = reading_near(ts, at - TREND_H * 60)
    if t_s is None:
        return None
    lv, tr = [], []
    for n in chosen["neighbours"]:
        ser = readings.get((city, n))
        if ser is None:
            continue
        t_n = reading_at(ser, at)
        if t_n is None:
            continue
        lv.append(t_n - t_s)
        t_n0 = reading_near(ser, at - TREND_H * 60)
        if t_n0 is not None and t_s0 is not None:
            tr.append((t_n - t_n0) - (t_s - t_s0))
    if len(lv) < MIN_NEIGHBOURS or len(tr) < MIN_NEIGHBOURS:
        return None
    return statistics.mean(lv), statistics.mean(tr)


def centres_c(bands, unit):
    """Each bucket's centre in C, an open tail's as if one more ordinary bucket."""
    regular = [b for b in bands if b["band_lo"] is not None and b["band_hi"] is not None]
    width = statistics.median(b["band_hi"] - b["band_lo"] for b in regular) if regular else 1.0
    out = []
    for b in bands:
        if b["band_lo"] is None:
            x = b["band_hi"] - width / 2
        elif b["band_hi"] is None:
            x = b["band_lo"] + width / 2
        else:
            x = (b["band_lo"] + b["band_hi"]) / 2
        out.append((x - 32) * 5 / 9 if unit == "F" else x)
    return out


def model(m, c, a, b, x):
    """q proportional to m^a exp((b . x) c), c centred on the market's mean."""
    mu = sum(mi * ci for mi, ci in zip(m, c))
    s = sum(bi * xi for bi, xi in zip(b, x))
    lg = [a * math.log(mi) + s * (ci - mu) for mi, ci in zip(m, c)]
    top = max(lg)
    ex = [math.exp(v - top) for v in lg]
    z = sum(ex)
    return [v / z for v in ex]


def fit(rows, n_feat):
    """(a, b) maximising the likelihood of model() by Newton from (1, 0...), a in [0.25, 4]."""
    k = 1 + n_feat
    pre = []
    for m, c, x, w in rows:
        mu = sum(mi * ci for mi, ci in zip(m, c))
        pre.append(([[math.log(mi)] + [xi * (ci - mu) for xi in x] for mi, ci in zip(m, c)], w))
    th = [1.0] + [0.0] * n_feat
    for _ in range(60):
        g = [0.0] * k
        h = [[0.0] * k for _ in range(k)]
        for feats, w in pre:
            lg = [sum(t * f for t, f in zip(th, fi)) for fi in feats]
            top = max(lg)
            ex = [math.exp(v - top) for v in lg]
            z = sum(ex)
            q = [v / z for v in ex]
            e = [sum(qi * f[j] for qi, f in zip(q, feats)) for j in range(k)]
            for j in range(k):
                g[j] += feats[w][j] - e[j]
                for l in range(j, k):
                    v = sum(qi * (f[j] - e[j]) * (f[l] - e[l]) for qi, f in zip(q, feats))
                    h[j][l] -= v
                    if l != j:
                        h[l][j] -= v
        step = solve(h, [-x for x in g])
        if step is None:
            break
        new = [th[j] + step[j] for j in range(k)]
        new[0] = min(max(new[0], mvm.A_BOUNDS[0]), mvm.A_BOUNDS[1])
        done = max(abs(new[j] - th[j]) for j in range(k)) < 1e-7
        th = new
        if done:
            break
    return th[0], th[1:]


def solve(a, b):
    """Gaussian elimination; None if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(m[r][i]))
        if abs(m[p][i]) < 1e-12:
            return None
        m[i], m[p] = m[p], m[i]
        for r in range(n):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [x - f * y for x, y in zip(m[r], m[i])]
    return [m[i][n] / m[i][i] for i in range(n)]


def boot(per_row, seed=SEED):
    by = defaultdict(list)
    for d, v in per_row:
        by[d].append(v)
    dates = sorted(by)
    sums = {d: (sum(v), len(v)) for d, v in by.items()}
    n_all = sum(n for _s, n in sums.values())
    mean = sum(s for s, _n in sums.values()) / n_all
    rng = random.Random(seed)
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


def ladders_after(events, times):
    """{(event_id, checkpoint): [first price after, per bucket]} for complete ladders."""
    best = {}
    for r in mvm.read_csv(mvm.MH + "prices.csv.gz"):
        eid = r["event_id"]
        if eid not in events:
            continue
        t = int(r["t"])
        for k in CHECKPOINTS:
            td = times[(eid, k)]
            if td <= t <= td + AFTER_MIN * 60:
                key = (eid, k, int(r["band_index"]))
                if key not in best or t < best[key][0]:
                    best[key] = (t, float(r["p"]))
    out = {}
    for eid, e in events.items():
        for k in CHECKPOINTS:
            ps = [best.get((eid, k, b["band_id"])) for b in e["bands"]]
            if all(p is not None for p in ps):
                out[(eid, k)] = [p[1] for p in ps]
    return out


def with_baseline(raw):
    """{key: (level, trend)} with level less the city's median over its BASELINE_DAYS earlier days.
    raw: {(city, date, checkpoint): (level_raw, trend)}."""
    by = defaultdict(list)
    for (city, date, k), v in raw.items():
        by[(city, k)].append((date, v))
    out = {}
    for (city, k), vs in by.items():
        vs.sort()
        for i, (date, (lv, tr)) in enumerate(vs):
            lo = (dt.date.fromisoformat(date) - dt.timedelta(days=BASELINE_DAYS)).isoformat()
            prior = [v[0] for d, v in vs[:i] if d >= lo]
            if len(prior) >= BASELINE_MIN:
                out[(city, date, k)] = (lv - statistics.median(prior), tr)
    return out


def cmd_study():
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    events, _left = mvm.load_events(tz, cities)
    chosen = json.load(open(OUT + "chosen.json"))
    readings = load_readings()
    times = {}
    for eid, e in events.items():
        zone = ZoneInfo(tz[e["city"]])
        day = dt.date.fromisoformat(e["date"])
        for k, h in CHECKPOINTS.items():
            times[(eid, k)] = int(dt.datetime.combine(day, dt.time(h), zone).timestamp())
    lad = ladders_after(events, times)
    raw, raw_pl = {}, {}
    for (eid, k) in lad:
        e = events[eid]
        ch = chosen.get(e["city"])
        if not ch or len(ch["neighbours"]) < MIN_NEIGHBOURS:
            continue
        t_min = times[(eid, k)] // 60
        f = raw_features(readings, e["city"], ch, t_min)
        if f is not None:
            raw[(e["city"], e["date"], k)] = f
        f = raw_features(readings, e["city"], ch, t_min - 24 * 60)
        if f is not None:
            raw_pl[(e["city"], e["date"], k)] = f
    feat, feat_pl = with_baseline(raw), with_baseline(raw_pl)
    p = print
    left_out = sorted(c for c in tz if len((chosen.get(c) or {}).get("neighbours", [])) < MIN_NEIGHBOURS)
    p("# Do the stations around the settlement airport know more than the market? (28 Sep 2026)")
    p()
    p("Generated by `tools/p310_neighbours.py` from committed inputs only (the venue's record, "
      "`data/training/neighbours/`). The stations, checkpoints, features, model and the pass rule are fixed "
      "in the tool's docstring, written before any reading was fetched. Research only.")
    p()
    p(f"- Cities with at least {MIN_NEIGHBOURS} neighbours: {len(tz) - len(left_out)} of {len(tz)}; "
      f"left out: {', '.join(left_out) or 'none'}.")
    nb = [len(chosen[c]["neighbours"]) for c in tz if c not in left_out]
    kms = [float(r["km"]) for r in csv.DictReader(open(CANDIDATES))
           if r["role"] == "candidate" and r["station"] in (chosen.get(r["city_key"]) or {}).get("neighbours", [])]
    p(f"- Neighbours per city: median {statistics.median(nb)}; distance to the settlement station median "
      f"{statistics.median(kms):.0f} km (range {min(kms):.0f}-{max(kms):.0f}).")
    p()
    p("| checkpoint | city-days scored | market log loss | recal | with the neighbours | gain over recal [90%] "
      "| placebo gain over recal [90%] | top pick: market | top pick: with the neighbours |")
    p("|---|---|---|---|---|---|---|---|---|")
    fits_out = []
    for k in CHECKPOINTS:
        rows = []
        for (eid, kk), ps in lad.items():
            if kk != k:
                continue
            e = events[eid]
            key = (e["city"], e["date"], k)
            if key not in feat or key not in feat_pl:
                continue
            m = [max(mvm.PRICE_FLOOR, x) for x in ps]
            s = sum(m)
            m = [x / s for x in m]
            w = [b["band_id"] for b in e["bands"]].index(e["winner"])
            rows.append({"date": e["date"], "month": e["date"][:7], "m": m, "c": centres_c(e["bands"], e["unit"]),
                         "x": list(feat[key]), "xp": list(feat_pl[key]), "w": w})
        scored = []
        for mo in sorted({r["month"] for r in rows}):
            train = [r for r in rows if r["month"] < mo]
            if len(train) < MIN_TRAIN:
                continue
            a0, _ = fit([(r["m"], r["c"], [], r["w"]) for r in train], 0)
            a1, b1 = fit([(r["m"], r["c"], r["x"], r["w"]) for r in train], 2)
            a2, b2 = fit([(r["m"], r["c"], r["xp"], r["w"]) for r in train], 2)
            fits_out.append((k, mo, len(train), a0, a1, b1, b2))
            for r in rows:
                if r["month"] != mo:
                    continue
                q0 = model(r["m"], r["c"], a0, [], [])
                q1 = model(r["m"], r["c"], a1, b1, r["x"])
                q2 = model(r["m"], r["c"], a2, b2, r["xp"])
                scored.append(dict(r, ll_m=-math.log(r["m"][r["w"]]), ll_0=-math.log(q0[r["w"]]),
                                   ll_1=-math.log(q1[r["w"]]), ll_2=-math.log(q2[r["w"]]),
                                   hit_m=max(range(len(r["m"])), key=lambda i: r["m"][i]) == r["w"],
                                   hit_1=max(range(len(q1)), key=lambda i: q1[i]) == r["w"]))
        if not scored:
            p(f"| {k} | 0 | | | | | | | |")
            continue
        g = boot([(r["date"], r["ll_0"] - r["ll_1"]) for r in scored])
        gp = boot([(r["date"], r["ll_0"] - r["ll_2"]) for r in scored])
        n = len(scored)
        p(f"| {k} | {n:,} | {statistics.mean(r['ll_m'] for r in scored):.4f} | "
          f"{statistics.mean(r['ll_0'] for r in scored):.4f} | {statistics.mean(r['ll_1'] for r in scored):.4f} | "
          f"{g[0]:+.4f} [{g[1]:+.4f}, {g[2]:+.4f}] | {gp[0]:+.4f} [{gp[1]:+.4f}, {gp[2]:+.4f}] | "
          f"{100 * sum(r['hit_m'] for r in scored) / n:.1f}% | {100 * sum(r['hit_1'] for r in scored) / n:.1f}% |")
    p()
    p("Gain: log loss of recal minus log loss with the features, per city-day (positive: the neighbours help). "
      "The placebo is the same features from the readings 24 hours earlier.")
    p()
    p("## The fits, each on the months before the month it scores")
    p()
    p("| checkpoint | month | city-days trained on | a (recal) | a, b_level, b_trend (neighbours) | "
      "b_level, b_trend (placebo) |")
    p("|---|---|---|---|---|---|")
    for k, mo, n, a0, a1, b1, b2 in fits_out:
        p(f"| {k} | {mo} | {n:,} | {a0:.3f} | {a1:.3f}, {b1[0]:+.4f}, {b1[1]:+.4f} | {b2[0]:+.4f}, {b2[1]:+.4f} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["stations", "fetch", "study"])
    a = ap.parse_args()
    {"stations": cmd_stations, "fetch": cmd_fetch, "study": cmd_study}[a.cmd]()


if __name__ == "__main__":
    main()
