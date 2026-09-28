"""Plan v2.4 P3.10 part 5 (new information: US one-minute readings): does the
station's own minute series say which bucket the day is entering before the
hourly report does, early enough to call the winner before the market?

Hassan, 28 Sep: "we need our predictive model to win the single max temp
winner". On the record the market reacts to a new report within minutes and
buying after the report loses (docs/MARKET_REACTION_2026-09-28.md). A US ASOS
station measures every minute; its report is built from those minutes. If the
minutes show the new bucket before the report does, a desk that reads them is
ahead of a market that waits for the report. Research: nothing here trades.

FIXED HERE, BEFORE ANY DATA WAS FETCHED
---------------------------------------
Data. IEM's one-minute ASOS archive (asos1min.py, tmpf, whole F) for the US
  cities' current stations, FROM to LAST; the reports are the desk's station
  series (data/replay/inputs_2026-09-26/obs_utc.csv.gz, routine and specials).
The signal. At every 5-minute mark (:00, :05, ...), the mean of the minute
  readings over the 5 minutes ending at the mark, rounded half up to whole F,
  usable AVAIL_MIN minutes after the mark. A PRE-EVENT is the first mark,
  FIRST_HOUR to LAST_HOUR local, whose value lies in a bucket above the bucket
  of the reports' running maximum up to that mark; one per bucket per day.
M1 Confirmation: the share of pre-events followed within CONFIRM_MIN by a
  report reaching that bucket or higher, and the lead (the report's time minus
  the mark), median and quartiles.
M2 The winner: how often the pre-event's bucket is the day's winner, beside
  how often the reports' running-maximum bucket at the same mark is (the
  control's bucket).
M3 The trade: one YES share of the pre-event's bucket at the first minute price
  at or after the mark + AVAIL_MIN; cost = ask + fee(ask) as p310_s10_after_costs
  (the archived books' median half-spread, fee 0.05 x (1 - x)); P&L per share
  with a 90% bootstrap over dates. The control: the same at the same moment for
  the reports' running-maximum bucket. And the pre-event bucket's price change
  from the buy to +10 and +30 min (does the market catch up afterwards?).
If more than MAX_PRE pre-events qualify, a random sample (SEED).

READ THIS FIRST (kept in the report): IEM's one-minute archive is NCEI's, which
is not published in real time; a live desk would need a real-time five-minute
feed (the ASOS high-frequency reports, e.g. through MADIS or Synoptic), not
checked here. The price is quoted, not executable. The one-minute value is not
what the venue settles on: the venue reads the reports.

    python tools/p310_one_minute.py fetch     # minutes + prices -> cache (resumable)
    python tools/p310_one_minute.py write     # -> data/training/asos_1min/, data/training/market_history/onemin_*
    python tools/p310_one_minute.py report > docs/ONE_MINUTE_READINGS_2026-09-28.md
"""
import argparse
import csv
import datetime as dt
import gzip
import io
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
import market_vs_model as mvm              # noqa: E402
import p310_s10_after_costs as costs       # noqa: E402
import p310_reaction as rx                 # noqa: E402

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
FROM, LAST = "2026-03-01", "2026-09-25"
FIRST_HOUR, LAST_HOUR = 9, 19
AVAIL_MIN = 2
CONFIRM_MIN = 60
PRE_MIN, POST_MIN = 10, 120
MAX_PRE = 3000
SEED = 5
UA = {"User-Agent": "arbdesk4-research/1.0"}
MIN_DIR = "data/training/asos_1min/"
OUT = "data/training/market_history/"


def get_text(url, tries=5):
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read().decode()
        except Exception as e:
            err = e
            time.sleep(min(60, 2 ** i))
    raise err


def us_stations(cities):
    return {c: r["icao"] for c, r in cities.items() if r["unit"] == "F" and r["status"] == "active" and r["icao"]}


def fetch_minutes(icao, cache):
    """{utc epoch minute: tmpf} for FROM..LAST (+1 day), month by month, cached."""
    path = os.path.join(cache, f"{icao}.json")
    if os.path.exists(path):
        return {int(k): v for k, v in json.load(open(path)).items()}
    sid = icao[1:] if icao.startswith("K") else icao
    out = {}
    start = dt.date.fromisoformat(FROM) - dt.timedelta(days=1)
    end = dt.date.fromisoformat(LAST) + dt.timedelta(days=2)
    d = start
    while d < end:
        nxt = min(end, (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1))
        q = {"station": sid, "tz": "UTC", "year1": d.year, "month1": d.month, "day1": d.day, "hour1": 0,
             "minute1": 0, "year2": nxt.year, "month2": nxt.month, "day2": nxt.day, "hour2": 0, "minute2": 0,
             "vars": "tmpf", "sample": "1min", "what": "download", "delim": "comma"}
        text = get_text(f"{IEM}?{urllib.parse.urlencode(q)}")
        for r in csv.DictReader(io.StringIO(text)):
            v = r.get("tmpf")
            if v in (None, "", "M"):
                continue
            t = dt.datetime.strptime(r["valid(UTC)"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
            out[int(t.timestamp())] = float(v)
        d = nxt
        time.sleep(1)                     # IEM is a public service: one request at a time
    json.dump(out, open(path, "w"))
    return out


def five_minute_marks(minutes, day_start, day_end):
    """[(mark epoch, whole F)]: the mean of the minute readings over the 5 minutes
    ending at each mark, rounded half up; a mark with fewer than 3 readings is skipped."""
    out = []
    t = day_start - day_start % 300
    while t <= day_end:
        vals = [minutes[m] for m in range(t - 240, t + 60, 60) if m in minutes]
        if len(vals) >= 3:
            out.append((t, math.floor(sum(vals) / len(vals) + 0.5)))
        t += 300
    return out


def find_pre_events(events, tz, obs, minutes_by_city, icao):
    """[(event_id, city, date, mark, bucket_pre, bucket_metar, confirm_t or None)]"""
    out = []
    for eid, e in events.items():
        city = e["city"]
        if city not in icao or not (FROM <= e["date"] <= LAST) or e["unit"] != "F":
            continue
        reads = obs.get((city, e["date"]))
        mins = minutes_by_city.get(city)
        if not reads or not mins:
            continue
        zone = ZoneInfo(tz[city])
        day = dt.date.fromisoformat(e["date"])
        start = int(dt.datetime.combine(day, dt.time(0), zone).timestamp())
        end = int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), zone).timestamp())
        metar = [(t, rx.venue_reading(c, "F")) for t, c in reads]            # sorted by time
        seen = set()
        for mark, f in five_minute_marks(mins, start, end - 1):
            local = dt.datetime.fromtimestamp(mark, zone)
            if not (FIRST_HOUR <= local.hour < LAST_HOUR):
                continue
            so_far = [v for t, v in metar if t <= mark]
            if not so_far:
                continue
            k_metar = rx.bucket_of(e["bands"], max(so_far))
            k_pre = rx.bucket_of(e["bands"], f)
            if k_metar is None or k_pre is None or k_pre <= k_metar or k_pre in seen:
                continue
            seen.add(k_pre)
            confirm = next((t for t, v in metar if mark < t <= mark + CONFIRM_MIN * 60
                            and (rx.bucket_of(e["bands"], v) or -1) >= k_pre), None)
            out.append((eid, city, e["date"], mark, k_pre, k_metar, confirm))
    return out


def cmd_fetch(args):
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    icao = us_stations(cities)
    os.makedirs(args.cache, exist_ok=True)
    minutes_by_city = {}
    for city, st in sorted(icao.items()):
        minutes_by_city[city] = fetch_minutes(st, args.cache)
        print(f"  {city} {st}: {len(minutes_by_city[city]):,} minutes", file=sys.stderr)
    events, _ = mvm.load_events(tz, cities)
    obs = rx.load_obs(set(icao), tz)
    pre = find_pre_events(events, tz, obs, minutes_by_city, icao)
    found = len(pre)
    if len(pre) > MAX_PRE:
        pre = sorted(random.Random(SEED).sample(pre, MAX_PRE), key=lambda x: (x[2], x[1], x[3]))
    json.dump({"pre": pre, "found": found}, open(os.path.join(args.cache, "pre.json"), "w"))
    tok = rx.tokens()
    path = os.path.join(args.cache, "prices.jsonl")
    done = set()
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
                done.add((r["i"], r["kind"]))
            except ValueError:
                pass
    jobs = [(i, kind, p) for i, p in enumerate(pre) for kind in ("pre", "metar") if (i, kind) not in done]
    print(f"pre-events {found:,} found, {len(pre):,} kept; price series to fetch {len(jobs):,}", file=sys.stderr)

    def one(job):
        i, kind, (eid, city, day, mark, k_pre, k_metar, _c) = job
        k = k_pre if kind == "pre" else k_metar
        q = {"market": tok[(eid, k)], "startTs": mark - PRE_MIN * 60, "endTs": mark + POST_MIN * 60, "fidelity": 1}
        h = rx.get(f"{rx.CLOB}?{urllib.parse.urlencode(q)}").get("history") or []
        return {"i": i, "kind": kind, "points": [[int(p["t"]), p["p"]] for p in h]}

    with open(path, "a") as out, ThreadPoolExecutor(8) as pool:
        for fut in as_completed([pool.submit(one, j) for j in jobs]):
            try:
                out.write(json.dumps(fut.result()) + "\n")
            except Exception as e:
                print(f"  failed: {e}", file=sys.stderr)


def cmd_write(args):
    cities, _ = mvm.load_cities()
    os.makedirs(MIN_DIR, exist_ok=True)
    for city, st in sorted(us_stations(cities).items()):
        mins = {int(k): v for k, v in json.load(open(os.path.join(args.cache, f"{st}.json"))).items()}
        with gzip.open(os.path.join(MIN_DIR, f"{st}.csv.gz"), "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["utc_epoch", "tmpf"])
            for t in sorted(mins):
                w.writerow([t, mins[t]])
    meta = json.load(open(os.path.join(args.cache, "pre.json")))
    with gzip.open(OUT + "onemin_pre_events.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["i", "event_id", "city_key", "date", "mark", "bucket_pre", "bucket_metar", "confirm_t"])
        for i, (eid, city, day, mark, kp, km, c) in enumerate(meta["pre"]):
            w.writerow([i, eid, city, day, mark, kp, km, c or ""])
    pts = {}
    for line in open(os.path.join(args.cache, "prices.jsonl")):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        pts[(r["i"], r["kind"])] = r["points"]
    with gzip.open(OUT + "onemin_prices.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["i", "kind", "t", "p"])
        for (i, kind), ps in sorted(pts.items()):
            for t, p in sorted(ps):
                w.writerow([i, kind, t, p])
    json.dump({"pre_events_found": meta["found"], "kept": len(meta["pre"]), "seed": SEED, "max": MAX_PRE},
              open(OUT + "onemin_meta.json", "w"))
    print(json.dumps({"pre": len(meta["pre"]), "found": meta["found"], "series": len(pts)}), file=sys.stderr)


def cmd_report(args):
    tz = json.load(open(mvm.RI + "tz.json"))
    cities, _ = mvm.load_cities()
    events, _ = mvm.load_events(tz, cities)
    pre = [r for r in mvm.read_csv(OUT + "onemin_pre_events.csv.gz")]
    pts = defaultdict(list)
    for r in mvm.read_csv(OUT + "onemin_prices.csv.gz"):
        pts[(int(r["i"]), r["kind"])].append((int(r["t"]), float(r["p"])))
    for v in pts.values():
        v.sort()
    meta = json.load(open(OUT + "onemin_meta.json"))
    spreads, _ = costs.spread_model()
    p = print
    p("# US one-minute readings: do they show the winning bucket before the report does? (28 Sep 2026)")
    p()
    p("Generated by `tools/p310_one_minute.py` from committed inputs only (`data/training/asos_1min/`, "
      "`data/training/market_history/onemin_*`, the record, the station series, the archived books). The signal, "
      "measures and trade are fixed in the tool's docstring, written before any data was fetched. Research only.")
    p()
    p("## Read this first")
    p()
    p("- **Not real time.** IEM's one-minute archive is NCEI's, which is not published in real time. Trading on "
      "this would need a real-time five-minute feed (the ASOS high-frequency reports), not checked here.")
    p("- **Quoted, not executable.** The ask adds the archived books' median half-spread; the fee is the venue's.")
    p("- **The venue settles on the reports**, not on the minutes.")
    p()
    days = {(r["city_key"], r["date"]) for r in pre}
    p(f"Pre-events (a five-minute mean entering a bucket above the reports' running maximum, {FIRST_HOUR}:00-"
      f"{LAST_HOUR}:00 local, {FROM} to {LAST}, the {len({r['city_key'] for r in pre})} US cities): "
      f"{meta['pre_events_found']:,} found, {len(pre):,} kept (seed {SEED}), on {len(days):,} city-days.")
    p()
    p("## M1. Does a report follow, and how much earlier was the signal?")
    p()
    conf = [r for r in pre if r["confirm_t"]]
    leads = sorted((int(r["confirm_t"]) - int(r["mark"])) / 60 for r in conf)
    p(f"Followed within {CONFIRM_MIN} min by a report reaching the bucket: {len(conf):,} of {len(pre):,} "
      f"({100 * len(conf) / max(1, len(pre)):.1f}%).")
    if leads:
        p(f"Lead (the report's time minus the mark): median {leads[len(leads) // 2]:.0f} min, quartiles "
          f"{leads[len(leads) // 4]:.0f} / {leads[3 * len(leads) // 4]:.0f}.")
    p()
    p("## M2. The winner")
    p()
    def won(r, k):
        return events[r["event_id"]]["winner"] == int(k)
    hp = sum(won(r, r["bucket_pre"]) for r in pre)
    hm = sum(won(r, r["bucket_metar"]) for r in pre)
    p(f"The pre-event's bucket won the day on {hp:,} of {len(pre):,} ({100 * hp / max(1, len(pre)):.1f}%); the "
      f"reports' running-maximum bucket at the same mark won on {hm:,} ({100 * hm / max(1, len(pre)):.1f}%).")
    p()
    p("## M3. The trade")
    p()
    p("| rule | trades | days | hit rate | mean cost | P&L per share [90%] |")
    p("|---|---|---|---|---|---|")
    for kind, label, kcol in (("pre", "buy the pre-event's bucket", "bucket_pre"),
                              ("metar", "control: buy the reports' running-maximum bucket", "bucket_metar")):
        tr = []
        for r in pre:
            ps = pts.get((int(r["i"]), kind))
            if not ps:
                continue
            px = rx.first_at_or_after(ps, int(r["mark"]) + AVAIL_MIN * 60)
            if px is None:
                continue
            hs = spreads.get(costs.bin_of(px), (0.0, 0.0, 0))[0]
            ask = min(0.999, px + hs)
            cost = ask + costs.fee(ask)
            w = won(r, r[kcol])
            tr.append((r["date"], (1.0 if w else 0.0) - cost, w, cost))
        if tr:
            m = costs.boot([(d, v) for d, v, _w, _c in tr])
            p(f"| {label} | {len(tr):,} | {len({d for d, *_ in tr})} | {100 * sum(w for *_, w, _c in tr) / len(tr):.1f}% | "
              f"{statistics.mean(c for *_, c in tr):.3f} | {m[0]:+.4f} [{m[1]:+.4f}, {m[2]:+.4f}] |")
    p()
    moves = defaultdict(list)
    for r in pre:
        ps = pts.get((int(r["i"]), "pre"))
        if not ps:
            continue
        t_buy = int(r["mark"]) + AVAIL_MIN * 60
        a = rx.first_at_or_after(ps, t_buy)
        for k in (10, 30):
            b = rx.first_at_or_after(ps, t_buy + k * 60)
            if a is not None and b is not None:
                moves[k].append(b - a)
    for k, v in sorted(moves.items()):
        p(f"The pre-event bucket's price from the buy to +{k} min: median {statistics.median(v):+.4f}, mean "
          f"{statistics.mean(v):+.4f} ({len(v):,}).")
    p()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "write", "report"])
    ap.add_argument("--cache", default=os.environ.get("ONEMIN_CACHE", ".onemin_cache"))
    args = ap.parse_args()
    {"fetch": cmd_fetch, "write": cmd_write, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
