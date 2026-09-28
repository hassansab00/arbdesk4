"""The venue's own record: every "Highest temperature in <city>" event on
Polymarket, its ladder, its winner and the hourly price of every bucket.

WHY
---
Every verdict on the model against the market so far rests on the few days the
database still holds whole priced ladders for (22 Sep onwards: edges and books
older than that are pruned into data/archive, and those are partial ladders).
Polymarket keeps the hourly price history of resolved markets, so the market's
belief at any cutoff can be rebuilt for every city-day it has listed. This
fetches it once, into the repository, so that every model is judged against
the market on thousands of city-days instead of a handful. Research input
only: nothing here prices or trades.

WHAT IS FETCHED
---------------
1. The index: Gamma `/events` by tag (`daily-temperature`, `highest-temperature`)
   in 10-day windows of `end_date` (the API refuses deep offsets), keeping the
   events titled "Highest temperature in ...". Each event's markets carry the
   bucket label, the YES token, and once resolved `outcomePrices` ("1"/"0").
2. The prices: CLOB `/prices-history` for each YES token, `fidelity=60`
   (hourly), from the event's creation to 36 h after its `endDate`.

FILES (data/training/market_history/)
-------------------------------------
events.csv.gz  one row per event: event_id, slug, city_slug, city_key (null
               when the city is not in public.cities), date (the day the event
               is about, local), unit, resolution_source (the settlement page;
               from the rules text when the event has none), station_icao (the
               ICAO that page names: its station can differ from today's),
               station_text (the station the rules name), created_at, closed,
               listing
               ('arch' for the 149 slugs Polymarket prefixed "arch-", 48 of
               which have a "main" twin for the same day: prefer "main")
bands.csv.gz   one row per bucket: event_id, band_index (lowest first), label,
               band_lo, band_hi (half-open [lo, hi) in whole units of the unit,
               as v_canonical_bands), open_low, open_high, token_yes, winner
               (1, 0, or empty while unresolved)
prices.csv.gz  event_id, band_index, t (unix seconds), p (YES price)

    python tools/market_history.py index            # Gamma -> cache
    python tools/market_history.py prices [--limit N]   # CLOB -> cache (resumable)
    python tools/market_history.py write            # cache -> data/training/market_history
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

GAMMA = "https://gamma-api.polymarket.com/events"
CLOB = "https://clob.polymarket.com/prices-history"
TAGS = ("daily-temperature", "highest-temperature")
TITLE = re.compile(r"^Highest temperature in (.+?) on ", re.I)
SLUG_CITY = re.compile(r"^(arch-)?highest-temperature-in-(.+)-on-[a-z]+-\d+(?:-\d{4})?$")
FIRST = dt.date(2024, 12, 1)
WINDOW_DAYS = 10
FIDELITY_MIN = 60
AFTER_END_H = 36
WORKERS = 8
UA = {"User-Agent": "arbdesk4-research/1.0"}
OUT = os.path.join("data", "training", "market_history")

# Slug city -> public.cities.city_key where they differ by more than '-' -> '_'
# (read from markets.event_slug on 28 Sep: nyc is listed as both).
ALIASES = {"new-york-city": "nyc", "nyc": "nyc"}
KNOWN = {
    "amsterdam", "ankara", "atlanta", "austin", "beijing", "buenos_aires", "busan", "cape_town", "chengdu",
    "chicago", "chongqing", "dallas", "denver", "guangzhou", "helsinki", "houston", "istanbul", "jeddah",
    "karachi", "kuala_lumpur", "london", "los_angeles", "lucknow", "madrid", "manila", "mexico_city", "miami",
    "milan", "moscow", "munich", "nyc", "panama_city", "paris", "qingdao", "san_francisco", "sao_paulo",
    "seattle", "seoul", "shanghai", "shenzhen", "singapore", "tel_aviv", "tokyo", "toronto", "warsaw",
    "wellington", "wuhan", "zhengzhou", "dc", "hong_kong", "jakarta", "jinan", "lagos", "taipei",
}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9

BAND_BELOW = re.compile(r"^(-?\d+)\s*°\s*([CF]) or below$", re.I)
BAND_ABOVE = re.compile(r"^(-?\d+)\s*°\s*([CF]) or higher$", re.I)
BAND_RANGE = re.compile(r"^(-?\d+)\s*-\s*(-?\d+)\s*°\s*([CF])$", re.I)
BAND_ONE = re.compile(r"^(-?\d+)\s*°\s*([CF])$", re.I)


def parse_band(label):
    """(lo, hi, open_low, open_high, unit) on the half-open whole-unit convention
    of v_canonical_bands, or None for a label this does not recognise."""
    s = (label or "").strip()
    m = BAND_BELOW.match(s)
    if m:
        return None, int(m.group(1)) + 1, True, False, m.group(2).upper()
    m = BAND_ABOVE.match(s)
    if m:
        return int(m.group(1)), None, False, True, m.group(2).upper()
    m = BAND_RANGE.match(s)
    if m:
        return int(m.group(1)), int(m.group(2)) + 1, False, False, m.group(3).upper()
    m = BAND_ONE.match(s)
    if m:
        return int(m.group(1)), int(m.group(1)) + 1, False, False, m.group(2).upper()
    return None


def city_key_of(city_slug):
    key = ALIASES.get(city_slug, city_slug.replace("-", "_"))
    return key if key in KNOWN else None


def event_date(slug, end_date):
    """The local day the event is about: month and day from the slug, the year
    from the slug when it carries one, else the year of endDate (noon UTC on
    that day)."""
    m = re.search(r"-on-([a-z]+)-(\d+)(?:-(\d{4}))?$", slug)
    if not m or m.group(1) not in MONTHS:
        return None
    year = int(m.group(3)) if m.group(3) else int(end_date[:4])
    try:
        return dt.date(year, MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None


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
        except Exception as e:           # network: retry with backoff
            err = e
        time.sleep(min(60, 2 ** i))
    raise err


def cache_dir(args):
    d = args.cache
    os.makedirs(d, exist_ok=True)
    return d


STATION_TEXT = re.compile(r"recorded (?:by (?:the )?(?P<by>[^.]+?) )?(?:at (?:the )?(?P<at>[^.]+?) )?in degrees", re.S)
DESCRIPTION_URL = re.compile(r"https?://[^\s)\"']+")


def station_text(description):
    """The station the rules name ("... recorded [by NOAA] at the <station> in
    degrees ...", or "recorded by the Hong Kong Observatory in degrees"), or ''."""
    m = STATION_TEXT.search(description)
    return (m.group("at") or m.group("by") or "").strip() if m else ""


def description_url(description):
    """The first settlement-page URL in the rules (wunderground or weather.gov), or ''."""
    for u in DESCRIPTION_URL.findall(description):
        if "wunderground.com" in u or "weather.gov" in u:
            return u.rstrip(".,")
    return ""


def station_icao(url):
    """The ICAO a settlement URL names: `...timeseries?site=eham` or a
    wunderground path ending in the ICAO. '' when it names none."""
    if not url:
        return ""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if q.get("site"):
        return q["site"][0].upper()
    last = urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return last.upper() if re.fullmatch(r"[A-Za-z]{4}", last) else ""


def cmd_index(args):
    seen = {}
    d, last = FIRST, dt.date.today() + dt.timedelta(days=3)
    while d <= last:
        nxt = d + dt.timedelta(days=WINDOW_DAYS)
        for tag in TAGS:
            off = 0
            while True:
                q = {"tag_slug": tag, "end_date_min": d.isoformat(), "end_date_max": nxt.isoformat(),
                     "limit": 100, "offset": off}
                page = get(f"{GAMMA}?{urllib.parse.urlencode(q)}")
                if not page:
                    break
                for e in page:
                    if TITLE.match(e.get("title") or ""):
                        seen[e["id"]] = e
                off += len(page)
                if len(page) < 100:
                    break
        d = nxt
    rows = []
    for e in seen.values():
        rows.append({k: e.get(k) for k in ("id", "slug", "title", "startDate", "creationDate", "createdAt",
                                           "endDate", "closed", "closedTime", "resolutionSource", "seriesSlug")}
                    | {"station_text": station_text(e.get("description") or ""),
                       "description_url": description_url(e.get("description") or "")}
                    | {"markets": [{k: m.get(k) for k in ("id", "question", "groupItemTitle", "clobTokenIds",
                                                          "outcomePrices", "conditionId", "closed",
                                                          "umaResolutionStatus")}
                                   for m in e.get("markets") or []]})
    rows.sort(key=lambda r: (r["endDate"] or "", r["slug"]))
    path = os.path.join(cache_dir(args), "index.json")
    with open(path, "w") as f:
        json.dump(rows, f)
    print(f"index: {len(rows)} events -> {path}", file=sys.stderr)


def load_index(args):
    with open(os.path.join(cache_dir(args), "index.json")) as f:
        return json.load(f)


def ladder(event):
    """[(band_index, label, lo, hi, open_low, open_high, unit, token_yes, winner)]
    lowest bucket first, or (None, why) when the ladder cannot be read."""
    out = []
    for m in event["markets"]:
        label = m.get("groupItemTitle") or ""
        b = parse_band(label)
        if b is None:
            return None, f"unparsed label {label!r}"
        try:
            token = json.loads(m.get("clobTokenIds") or "[]")[0]
        except (ValueError, IndexError):
            return None, "no token"
        winner = ""
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
            if m.get("closed") and prices and prices[0] in ("1", "0"):
                winner = int(prices[0])
        except ValueError:
            pass
        out.append((label, *b, token, winner))
    out.sort(key=lambda r: (r[1] is not None, r[1] if r[1] is not None else -10 ** 6))
    units = {r[5] for r in out}
    if len(units) != 1:
        return None, f"mixed units {sorted(units)}"
    return [(i, *r) for i, r in enumerate(out)], None


def ts(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def cmd_prices(args):
    events = load_index(args)
    done_path = os.path.join(cache_dir(args), "prices.jsonl")
    done = set()
    if os.path.exists(done_path):
        with open(done_path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done.add((r["event_id"], r["band_index"]))
                except ValueError:
                    pass                           # a torn last line from an interrupted run
    jobs = []
    for e in events:
        if not e.get("closed"):
            continue
        bands, why = ladder(e)
        if bands is None:
            continue
        start = ts(e.get("startDate") or e.get("creationDate") or e["createdAt"])
        end = ts(e["endDate"]) + AFTER_END_H * 3600
        for band in bands:
            if (e["id"], band[0]) not in done:
                jobs.append((e["id"], band[0], band[7], start, end))
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"prices: {len(done)} bands cached, {len(jobs)} to fetch", file=sys.stderr)
    lock = threading.Lock()
    t0 = time.time()

    def one(job):
        eid, idx, token, start, end = job
        q = {"market": token, "startTs": start, "endTs": end, "fidelity": FIDELITY_MIN}
        h = get(f"{CLOB}?{urllib.parse.urlencode(q)}").get("history") or []
        return {"event_id": eid, "band_index": idx, "points": [[int(p["t"]), p["p"]] for p in h]}

    n = 0
    with open(done_path, "a") as out, ThreadPoolExecutor(WORKERS) as pool:
        futs = [pool.submit(one, j) for j in jobs]
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:                # recorded, retried on the next run
                print(f"  failed: {e}", file=sys.stderr)
                continue
            with lock:
                out.write(json.dumps(r) + "\n")
                n += 1
                if n % 2000 == 0:
                    out.flush()
                    print(f"  {n}/{len(jobs)} in {time.time() - t0:.0f} s", file=sys.stderr)
    print(f"prices: fetched {n} in {time.time() - t0:.0f} s", file=sys.stderr)


def cmd_write(args):
    events = load_index(args)
    os.makedirs(OUT, exist_ok=True)
    prices = {}
    with open(os.path.join(cache_dir(args), "prices.jsonl")) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            prices[(r["event_id"], r["band_index"])] = r["points"]
    skipped = {}
    ev_rows, band_rows, n_pts = [], [], 0
    with gzip.open(os.path.join(OUT, "prices.csv.gz"), "wt", newline="") as pf:
        pw = csv.writer(pf)
        pw.writerow(["event_id", "band_index", "t", "p"])
        for e in events:
            bands, why = ladder(e)
            if bands is None:
                skipped[why.split(" ")[0]] = skipped.get(why.split(" ")[0], 0) + 1
                continue
            m = SLUG_CITY.match(e["slug"])
            city_slug = m.group(2) if m else ""
            listing = "arch" if (m and m.group(1)) else "main"
            day = event_date(e["slug"], e["endDate"] or "")
            if day is None:
                skipped["undated"] = skipped.get("undated", 0) + 1
                continue
            url = e.get("resolutionSource") or e.get("description_url") or ""
            ev_rows.append([e["id"], e["slug"], city_slug, city_key_of(city_slug) or "", day.isoformat(),
                            bands[0][6], url, station_icao(url), e.get("station_text") or "",
                            e.get("startDate") or e.get("creationDate") or e.get("createdAt") or "",
                            int(bool(e.get("closed"))), listing])
            for (i, label, lo, hi, ol, oh, unit, token, winner) in bands:
                band_rows.append([e["id"], i, label, "" if lo is None else lo, "" if hi is None else hi,
                                  int(ol), int(oh), token, winner])
                for t, p in sorted(prices.get((e["id"], i)) or []):
                    pw.writerow([e["id"], i, t, p])
                    n_pts += 1
    with gzip.open(os.path.join(OUT, "events.csv.gz"), "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "slug", "city_slug", "city_key", "date", "unit", "resolution_source",
                    "station_icao", "station_text", "created_at", "closed", "listing"])
        w.writerows(ev_rows)
    with gzip.open(os.path.join(OUT, "bands.csv.gz"), "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event_id", "band_index", "label", "band_lo", "band_hi", "open_low", "open_high",
                    "token_yes", "winner"])
        w.writerows(band_rows)
    print(json.dumps({"events": len(ev_rows), "bands": len(band_rows), "price_points": n_pts,
                      "skipped": skipped}), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "prices", "write"])
    ap.add_argument("--cache", default=os.environ.get("MARKET_HISTORY_CACHE", ".market_history_cache"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    {"index": cmd_index, "prices": cmd_prices, "write": cmd_write}[args.cmd](args)


if __name__ == "__main__":
    main()
