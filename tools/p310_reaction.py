"""Plan v2.4 P3.10 part 4: does the market lag a new reading, in minutes?

On the venue's record the market beats the desk's models at every checkpoint
once prices are taken after the model's information (#242), but the price of
the bucket a new reading points to rises 0.077 on average within the hour
after the decision. Hourly prices cannot say whether that lag is tradeable.
Polymarket keeps one-minute prices for resolved markets (checked 28 Sep: 240
points in 4 h, back to March 2026), so this measures the market's reaction to
each new daily maximum minute by minute. Research: nothing here prices or
trades.

FIXED HERE, BEFORE ANY DATA WAS FETCHED
---------------------------------------
Events. A station reading (data/replay/inputs_2026-09-26/obs_utc.csv.gz, the
  station series the desk reads) that lifts the day's running maximum, read the
  venue's way (whole degrees in the market's unit, half up), into a HIGHER
  bucket of that day's ladder than the running maximum before it, between
  FIRST_HOUR and LAST_HOUR local. t0 = the reading's observation time (UTC).
  The bucket entered is B. Only events on the record's scorable city-days
  (active city, current station, exactly one winner), from FROM to LAST.
  At most one event per city-day per bucket; if more than MAX_EVENTS qualify,
  a random sample (seed SEED), stratified by nothing.
Prices. B's one-minute CLOB prices from t0 - PRE_MIN to t0 + POST_MIN.
  The price at t0 + k is the first minute price at or after t0 + k (never one
  before: the lesson of #242).
M1 The reaction: the median and mean of p(t0 + k) - p_before over events, for
  k in STEPS minutes, where p_before is the last price at or before t0 - 1 min.
M2 The half-time: per event, the first k at which the move reaches half of the
  move to t0 + POST_MIN (events that move at least MIN_MOVE); median and
  quartiles.
M3 The trade: one YES share of B at the first price at or after t0 + L, for
  each latency L in LATENCIES; cost = ask + fee(ask), ask = p + half_spread(p)
  (the archived books' median ask - mid by price bin, as p310_s10_after_costs)
  and fee(x) = 0.05 x (1 - x). Outcome: 1 if B won. P&L per share with a 90%
  bootstrap over dates.
M4 The control: at one random minute per city-day in the same hours, at least
  CONTROL_GAP_MIN after any event, one YES share of the bucket holding the
  running maximum then, at the same costs. If the control also makes money,
  the edge is not the reaction.

Changed after the first run, presentation only: the last step of M1 is 119
min (the fetched window ends at t0 + 120, so "the first price at or after
t0 + 120" existed for 3 events), and the events found before sampling are
now recorded (the first run recorded the sample's size).

READ THIS FIRST (said before the result, and kept in the report): the price is
quoted, not executable; the half-spread comes from hourly books; t0 is the
observation time, and the reading is published some minutes later, so a move
before t0 + a few minutes is the market reading faster sources (the station's
own feed, other networks), not a leak; a move after it is the lag.

    python tools/p310_reaction.py fetch        # events + minute prices -> cache (resumable)
    python tools/p310_reaction.py write        # cache -> data/training/market_history/reaction_*.csv.gz
    python tools/p310_reaction.py report > docs/MARKET_REACTION_2026-09-28.md
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_vs_model as mvm              # noqa: E402  (the study's scorable events)
import p310_s10_after_costs as costs       # noqa: E402  (the books' half-spread and the fee)

OBS = "data/replay/inputs_2026-09-26/obs_utc.csv.gz"
OUT = "data/training/market_history/"
CLOB = "https://clob.polymarket.com/prices-history"
FROM, LAST = "2026-03-01", "2026-09-25"
FIRST_HOUR, LAST_HOUR = 9, 19
PRE_MIN, POST_MIN = 30, 120
STEPS = (0, 2, 5, 10, 15, 30, 60, 119)   # 119: the fetched window ends at 120 (changed after the first run)
LATENCIES = (2, 5, 10, 15)
MIN_MOVE = 0.02
CONTROL_GAP_MIN = 60
MAX_EVENTS = 3000
SEED = 7
WORKERS = 8
UA = {"User-Agent": "arbdesk4-research/1.0"}


def whole(v):
    return math.floor(v + 0.5)


def venue_reading(temp_c, unit):
    return whole(temp_c if unit == "C" else temp_c * 9 / 5 + 32)


def bucket_of(bands, x):
    for b in bands:
        if (b["band_lo"] is None or x >= b["band_lo"]) and (b["band_hi"] is None or x < b["band_hi"]):
            return b["band_id"]
    return None


def find_events(events, tz, obs_by_city_day):
    """[(event_id, city, date, t0 epoch, bucket index)] and the controls
    [(event_id, city, date, t epoch, bucket index)]."""
    out, controls = [], []
    rng = random.Random(SEED)
    for eid, e in events.items():
        if not (FROM <= e["date"] <= LAST):
            continue
        readings = obs_by_city_day.get((e["city"], e["date"]))
        if not readings:
            continue
        zone = ZoneInfo(tz[e["city"]])
        run_max, run_bucket, times = None, None, []
        seen = set()
        for t, temp in readings:                             # sorted by time
            local = dt.datetime.fromtimestamp(t, zone)
            v = venue_reading(temp, e["unit"])
            k = bucket_of(e["bands"], v)
            if run_max is None or v > run_max:
                if (run_bucket is not None and k is not None and k > run_bucket
                        and FIRST_HOUR <= local.hour < LAST_HOUR and k not in seen):
                    out.append((eid, e["city"], e["date"], t, k))
                    seen.add(k)
                    times.append(t)
                run_max = v
                if k is not None:
                    run_bucket = k if run_bucket is None else max(run_bucket, k)
        # one control minute: a reading's time in the same hours, far from every event
        cands = [(t, temp) for t, temp in readings
                 if FIRST_HOUR <= dt.datetime.fromtimestamp(t, zone).hour < LAST_HOUR
                 and all(abs(t - te) >= CONTROL_GAP_MIN * 60 for te in times)]
        if cands:
            t, _ = cands[rng.randrange(len(cands))]
            vmax = max(venue_reading(temp, e["unit"]) for tt, temp in readings if tt <= t)
            k = bucket_of(e["bands"], vmax)
            if k is not None:
                controls.append((eid, e["city"], e["date"], t, k))
    return out, controls


def load_obs(cities, tz):
    by = defaultdict(list)
    with gzip.open(OBS, "rt", newline="") as f:
        for r in csv.DictReader(f):
            c = r["city_key"]
            if c not in cities:
                continue
            t = dt.datetime.fromisoformat(r["utc"]).replace(tzinfo=dt.timezone.utc)
            day = t.astimezone(ZoneInfo(tz[c])).date().isoformat()
            if FROM <= day <= LAST:
                by[(c, day)].append((int(t.timestamp()), float(r["temp_c"])))
    for v in by.values():
        v.sort()
    return by


def get(url, tries=6):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 422):
                raise
            err = e
        except Exception as e:
            err = e
        time.sleep(min(60, 2 ** i))
    raise err


def tokens():
    return {(r["event_id"], int(r["band_index"])): r["token_yes"] for r in mvm.read_csv(mvm.MH + "bands.csv.gz")}


def cmd_fetch(args):
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    events, _ = mvm.load_events(tz, cities)
    obs = load_obs(set(tz), tz)
    evs, controls = find_events(events, tz, obs)
    found = len(evs)
    rng = random.Random(SEED)
    if len(evs) > MAX_EVENTS:
        evs = sorted(rng.sample(evs, MAX_EVENTS), key=lambda x: (x[2], x[1], x[3]))
    days = {(e[0]) for e in evs}
    controls = [c for c in controls if c[0] in days]         # the same city-days' control minutes
    os.makedirs(args.cache, exist_ok=True)
    with open(os.path.join(args.cache, "events.json"), "w") as f:
        json.dump({"events": evs, "controls": controls, "found": found}, f)
    tok = tokens()
    path = os.path.join(args.cache, "minutes.jsonl")
    done = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add((r["kind"], r["i"]))
                except ValueError:
                    pass
    jobs = [("event", i, e) for i, e in enumerate(evs)] + [("control", i, c) for i, c in enumerate(controls)]
    jobs = [j for j in jobs if (j[0], j[1]) not in done]
    print(f"events {len(evs)}, controls {len(controls)}, to fetch {len(jobs)}", file=sys.stderr)

    def one(job):
        kind, i, (eid, city, day, t0, k) = job
        q = {"market": tok[(eid, k)], "startTs": t0 - PRE_MIN * 60, "endTs": t0 + POST_MIN * 60, "fidelity": 1}
        h = get(f"{CLOB}?{urllib.parse.urlencode(q)}").get("history") or []
        return {"kind": kind, "i": i, "points": [[int(p["t"]), p["p"]] for p in h]}

    with open(path, "a") as out, ThreadPoolExecutor(WORKERS) as pool:
        for fut in as_completed([pool.submit(one, j) for j in jobs]):
            try:
                out.write(json.dumps(fut.result()) + "\n")
            except Exception as e:
                print(f"  failed: {e}", file=sys.stderr)


def cmd_write(args):
    with open(os.path.join(args.cache, "events.json")) as f:
        meta = json.load(f)
    pts = {}
    with open(os.path.join(args.cache, "minutes.jsonl")) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            pts[(r["kind"], r["i"])] = r["points"]
    with open(OUT + "reaction_meta.json", "w") as f:
        json.dump({"events_found_before_sampling": meta["found"], "sampled": len(meta["events"]),
                   "controls": len(meta["controls"]), "seed": SEED, "max_events": MAX_EVENTS}, f)
    with gzip.open(OUT + "reaction_events.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "i", "event_id", "city_key", "date", "t0", "band_index"])
        for kind, rows in (("event", meta["events"]), ("control", meta["controls"])):
            for i, (eid, city, day, t0, k) in enumerate(rows):
                w.writerow([kind, i, eid, city, day, t0, k])
    with gzip.open(OUT + "reaction_prices.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "i", "t", "p"])
        for (kind, i), ps in sorted(pts.items()):
            for t, p in sorted(ps):
                w.writerow([kind, i, t, p])
    print(json.dumps({"events": len(meta["events"]), "controls": len(meta["controls"]),
                      "with_prices": len(pts)}), file=sys.stderr)


def first_at_or_after(ps, t):
    for tt, p in ps:
        if tt >= t:
            return p
    return None


def last_at_or_before(ps, t):
    best = None
    for tt, p in ps:
        if tt <= t:
            best = p
    return best


def cmd_report(args):
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    events, _ = mvm.load_events(tz, cities)
    rows = defaultdict(dict)
    for r in mvm.read_csv(OUT + "reaction_events.csv.gz"):
        rows[r["kind"]][int(r["i"])] = r
    pts = defaultdict(list)
    for r in mvm.read_csv(OUT + "reaction_prices.csv.gz"):
        pts[(r["kind"], int(r["i"]))].append((int(r["t"]), float(r["p"])))
    for v in pts.values():
        v.sort()
    spreads, _files = costs.spread_model()

    def won(r):
        return events[r["event_id"]]["winner"] == int(r["band_index"])

    p = print
    p("# Does the market lag a new reading? One-minute prices around each new daily maximum (28 Sep 2026)")
    p()
    p("Generated by `tools/p310_reaction.py` from committed inputs only (`data/training/market_history/"
      "reaction_*.csv.gz`, the record, the station series, the archived books). The events, measures, trade and "
      "control are fixed in the tool's docstring, written before any data was fetched. Research only.")
    p()
    p("## Read this first")
    p()
    p("- **Quoted, not executable.** The price is the venue's one-minute price series; the ask adds the archived "
      "books' median half-spread (hourly books), and the fee is the venue's taker fee.")
    p("- **t0 is the observation time.** The reading is published some minutes after it: a move before t0 plus a "
      "few minutes means the market has faster sources than the published report; a move after it is the lag.")
    p("- **Not the platform's speed.** The tick runs hourly; this says whether a faster path would be worth "
      "building, not what the platform can do today.")
    p()
    ev = rows["event"]
    have = [i for i in ev if pts.get(("event", i))]
    meta = json.load(open(OUT + "reaction_meta.json"))
    p(f"Events (a reading that lifts the day's running maximum into a higher bucket, {FIRST_HOUR}:00-"
      f"{LAST_HOUR}:00 local, {FROM} to {LAST}): {meta['events_found_before_sampling']:,} found, "
      f"{len(ev):,} sampled (seed {SEED}); with minute prices {len(have):,}; "
      f"on {len({ev[i]['date'] for i in have})} dates, {len({ev[i]['city_key'] for i in have})} cities. "
      f"Controls: {len(rows['control']):,}.")
    p()
    p("## M1. The reaction of the entered bucket's price")
    p()
    p("Change from the last price at or before t0 - 1 min to the first price at or after t0 + k.")
    p()
    p("| k (min) | events | median change | mean change | share moved up by 0.02 or more |")
    p("|---|---|---|---|---|")
    for k in STEPS:
        ch = []
        for i in have:
            ps, t0 = pts[("event", i)], int(ev[i]["t0"])
            a, b = last_at_or_before(ps, t0 - 60), first_at_or_after(ps, t0 + k * 60)
            if a is not None and b is not None:
                ch.append(b - a)
        if ch:
            p(f"| {k} | {len(ch):,} | {statistics.median(ch):+.4f} | {statistics.mean(ch):+.4f} | "
              f"{100 * sum(c >= MIN_MOVE for c in ch) / len(ch):.1f}% |")
    pre = []
    for i in have:
        ps, t0 = pts[("event", i)], int(ev[i]["t0"])
        a, b = last_at_or_before(ps, t0 - PRE_MIN * 60 + 60), last_at_or_before(ps, t0 - 60)
        if a is not None and b is not None:
            pre.append(b - a)
    if pre:
        p()
        p(f"Before t0: from t0 - {PRE_MIN} min to t0 - 1 min, median {statistics.median(pre):+.4f}, mean "
          f"{statistics.mean(pre):+.4f} ({len(pre):,} events).")
    p()
    p("## M2. How fast")
    p()
    halves = []
    for i in have:
        ps, t0 = pts[("event", i)], int(ev[i]["t0"])
        a, z = last_at_or_before(ps, t0 - 60), first_at_or_after(ps, t0 + POST_MIN * 60 - 60)
        if a is None or z is None or z - a < MIN_MOVE:
            continue
        for tt, pp in ps:
            if tt >= t0 - 60 and pp - a >= (z - a) / 2:
                halves.append((tt - t0) / 60)
                break
    if halves:
        h = sorted(halves)
        p(f"Events whose price rose at least {MIN_MOVE} by t0 + {POST_MIN} min: {len(h):,}. Minutes from t0 until "
          f"half of that rise: median {h[len(h) // 2]:.1f}, quartiles {h[len(h) // 4]:.1f} / {h[3 * len(h) // 4]:.1f}; "
          f"{100 * sum(x <= 0 for x in h) / len(h):.1f}% had moved half-way by t0.")
    p()
    p("## M3. The trade, and M4. the control")
    p()
    p("| rule | trades | days | hit rate | mean cost | P&L per share [90%] |")
    p("|---|---|---|---|---|---|")

    def trade(kind, i, lat):
        r = rows[kind][i]
        ps = pts.get((kind, i))
        if not ps:
            return None
        px = first_at_or_after(ps, int(r["t0"]) + lat * 60)
        if px is None:
            return None
        hs = spreads.get(costs.bin_of(px), (0.0, 0.0, 0))[0]
        ask = min(0.999, px + hs)
        cost = ask + costs.fee(ask)
        return r["date"], (1.0 if won(r) else 0.0) - cost, won(r), cost

    for lat in LATENCIES:
        tr = [x for x in (trade("event", i, lat) for i in have) if x]
        if tr:
            m = costs.boot([(d, v) for d, v, _w, _c in tr])
            p(f"| buy the entered bucket {lat} min after t0 | {len(tr):,} | {len({d for d, *_ in tr})} | "
              f"{100 * sum(w for _d, _v, w, _c in tr) / len(tr):.1f}% | {statistics.mean(c for *_, c in tr):.3f} | "
              f"{m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] |")
    ctl = [x for x in (trade("control", i, 0) for i in rows["control"]) if x]
    if ctl:
        m = costs.boot([(d, v) for d, v, _w, _c in ctl])
        p(f"| control: the running maximum's bucket at a random minute | {len(ctl):,} | {len({d for d, *_ in ctl})} | "
          f"{100 * sum(w for _d, _v, w, _c in ctl) / len(ctl):.1f}% | {statistics.mean(c for *_, c in ctl):.3f} | "
          f"{m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] |")
    p()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "write", "report"])
    ap.add_argument("--cache", default=os.environ.get("REACTION_CACHE", ".reaction_cache"))
    args = ap.parse_args()
    {"fetch": cmd_fetch, "write": cmd_write, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
