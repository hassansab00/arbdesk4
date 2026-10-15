"""Append newly settled venue labels and hourly prices to WXPredict nightly.

The original market_history files remain untouched. Nightly files preserve
every first answer; only an unresolved event/winner may become resolved.
Existing closed events and fetched series are never replaced. A missing or
empty price response is retried next night, and partial work is committed.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_history as mh  # noqa: E402
from wxpredict import common  # noqa: E402

EVENT = ["event_id", "slug", "city_slug", "city_key", "date", "unit", "resolution_source",
         "station_icao", "station_text", "created_at", "closed", "listing"]
BAND = ["event_id", "band_index", "label", "band_lo", "band_hi", "open_low", "open_high", "token_yes", "winner"]
PRICE = ["event_id", "band_index", "t", "p"]
SERIES = ["event_id", "band_index", "points", "received_at"]
LOOKBACK_DAYS = 14
BUDGET_S = 180
WORKERS = 4
JOB = "P2.14_market_record"
_HTTP = threading.local()


def get(url, deadline):
    """No request or retry starts after the shared nightly deadline."""
    for attempt in range(2):
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("market record deadline")
        try:
            if not hasattr(_HTTP, "session"):
                _HTTP.session = requests.Session()
            r = _HTTP.session.get(url, headers=mh.UA, timeout=min(20, left))
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt:
                raise
    raise RuntimeError("no answer")


def index(first, last, deadline, fetch=get):
    seen = {}
    for tag in mh.TAGS:
        tag_row = fetch(mh.GAMMA.rsplit("/events", 1)[0] + "/tags/slug/" + tag, deadline)
        if not isinstance(tag_row, dict) or not tag_row.get("id"):
            raise ValueError("Gamma tag has no id: " + tag)
        for page in range(50):
            q = {"tag_id": tag_row["id"], "end_date_min": first.isoformat(), "end_date_max": last.isoformat(),
                 "closed": "true", "limit": 100, "offset": page * 100}
            rows = fetch(mh.GAMMA + "?" + urllib.parse.urlencode(q), deadline)
            if not isinstance(rows, list):
                raise ValueError("Gamma did not return an event list")
            for e in rows:
                if mh.TITLE.match(e.get("title") or ""):
                    seen[str(e["id"])] = e
            print(f"market record: {tag} page {page + 1}, {len(rows)} events", file=sys.stderr, flush=True)
            if len(rows) < 100:
                break
        else:
            raise ValueError("Gamma pagination limit reached")
    return list(seen.values())


def event_rows(e):
    """The historical writer's schema, with a complete, settled ladder only."""
    bands, why = mh.ladder(e)
    if bands is None:
        raise ValueError(why)
    if e.get("closed") is not True or [b[8] for b in bands].count(1) != 1 or any(b[8] not in (0, 1) for b in bands):
        return None
    if len(bands) < 2 or not bands[0][4] or not bands[-1][5] or any(a[3] != b[2] for a, b in zip(bands, bands[1:])):
        raise ValueError("noncontiguous settled ladder")
    match = mh.SLUG_CITY.match(e.get("slug") or "")
    city_slug = match.group(2) if match else ""
    day = mh.event_date(e.get("slug") or "", e.get("endDate") or "")
    url = e.get("resolutionSource") or mh.description_url(e.get("description") or "") or ""
    city = mh.city_key_of(city_slug)
    station = mh.station_icao(url)
    if day is None or not city or not station:
        raise ValueError("event lacks dated city or settlement station")
    event = dict(zip(EVENT, [str(e["id"]), e["slug"], city_slug, city, day.isoformat(), bands[0][6], url,
                            station, mh.station_text(e.get("description") or ""),
                            e.get("startDate") or e.get("creationDate") or e.get("createdAt") or "",
                            "1", "arch" if match.group(1) else "main"]))
    out = []
    for i, label, lo, hi, ol, oh, _, token, win in bands:
        out.append(dict(zip(BAND, [str(e["id"]), str(i), label, "" if lo is None else str(lo),
                                  "" if hi is None else str(hi), str(int(ol)), str(int(oh)), str(token), str(win)])))
    return event, out


def checked_update(old_event, old_bands, event, bands):
    """Keep the first identity and metadata; allow only unresolved -> final."""
    if old_event is None:
        return event, bands
    for col in ("slug", "city_key", "date", "unit", "station_icao", "listing"):
        if old_event[col] != event[col]:
            raise ValueError("event identity changed: " + col)
    if len(old_bands) != len(bands):
        raise ValueError("ladder size changed")
    kept = []
    for a, b in zip(sorted(old_bands, key=lambda r: int(r["band_index"])), bands):
        for col in BAND[:-1]:
            av, bv = a[col], b[col]
            if col in ("band_lo", "band_hi"):
                av, bv = (None if av == "" else float(av)), (None if bv == "" else float(bv))
            if av != bv:
                raise ValueError("band identity changed: " + col)
        if a["winner"] not in ("", b["winner"]):
            raise ValueError("a recorded winner changed")
        kept.append({**a, "winner": b["winner"]})
    return {**old_event, "closed": "1"}, kept


def series(e, b, deadline, fetch=get):
    start = mh.ts(e.get("startDate") or e.get("creationDate") or e["createdAt"])
    end = mh.ts(e["endDate"]) + mh.AFTER_END_H * 3600
    q = {"market": b["token_yes"], "startTs": start, "endTs": end, "fidelity": mh.FIDELITY_MIN}
    answer = fetch(mh.CLOB + "?" + urllib.parse.urlencode(q), deadline)
    history = answer.get("history")
    if not isinstance(history, list) or not history:
        raise ValueError("empty price history")
    points = {}
    for p in history:
        t, price = int(p["t"]), float(p["p"])
        if not start <= t <= end or not 0 <= price <= 1:
            raise ValueError("invalid historical price")
        if t in points and points[t] != price:
            raise ValueError("conflicting historical price point")
        points[t] = price
    return [{"event_id": b["event_id"], "band_index": b["band_index"], "t": str(t), "p": str(p)}
            for t, p in sorted(points.items())]


def run(today=None, budget_s=BUDGET_S, fetch=get):
    today = today or dt.datetime.now(common.UTC).date()
    t0 = time.monotonic()
    deadline = t0 + budget_s
    events = {r["event_id"]: r for r in common.market_rows("events")}
    bands = {}
    for r in common.market_rows("bands"):
        bands.setdefault(r["event_id"], []).append(r)
    # The base's closed series were fetched by market_history.py. Successful
    # incremental series have a receipt; the price file also recovers a crash
    # between its atomic write and the receipt file's atomic write.
    base_closed = {r["event_id"] for r in common.read_csv(os.path.join(common.MH, "events.csv.gz")) if r["closed"] == "1"}
    done = {(eid, r["band_index"]) for eid in base_closed for r in bands.get(eid, [])}
    for p in common.market_nights("prices"):
        done.update((r["event_id"], r["band_index"]) for r in common.read_csv(p))
    root = os.path.join(os.path.dirname(common.MH), "market_history_nightly")
    stamp = today.isoformat()
    def held(kind, key):
        path = os.path.join(root, kind + "-" + stamp + ".csv.gz")
        return {key(r): r for r in common.read_csv(path)} if os.path.exists(path) else {}
    ev = held("events", lambda r: r["event_id"])
    bs = held("bands", lambda r: (r["event_id"], r["band_index"]))
    ps = held("prices", lambda r: (r["event_id"], r["band_index"], r["t"]))
    receipts = held("series", lambda r: (r["event_id"], r["band_index"]))
    refused, jobs, failed = {}, [], []
    active = common.active_cities()
    outside = 0
    raw_events = index(today - dt.timedelta(days=LOOKBACK_DAYS), today + dt.timedelta(days=3), deadline, fetch)
    for raw in raw_events:
        eid = str(raw["id"])
        slug = mh.SLUG_CITY.match(raw.get("slug") or "")
        if not slug or mh.city_key_of(slug.group(2)) not in active:
            outside += 1
            continue
        try:
            found = event_rows(raw)
            if found is None:
                continue
            e, b = checked_update(events.get(eid), bands.get(eid, []), *found)
            if eid not in base_closed:
                ev[eid] = e
                for r in b:
                    bs[(eid, r["band_index"])] = r
                    if (eid, r["band_index"]) not in done:
                        jobs.append((raw, r))
        except (ValueError, KeyError, TypeError) as err:
            refused[eid] = str(err)
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        print(f"market record: {len(raw_events)} candidates, {len(jobs)} series to fetch", file=sys.stderr, flush=True)
        futures = {pool.submit(series, e, b, deadline, fetch): b for e, b in jobs}
        for fut in cf.as_completed(futures):
            b = futures[fut]
            try:
                points = fut.result()
            except Exception:
                failed.append([b["event_id"], b["band_index"]])
                continue
            for r in points:
                ps.setdefault((r["event_id"], r["band_index"], r["t"]), r)
            receipts[(b["event_id"], b["band_index"])] = dict(zip(SERIES, [b["event_id"], b["band_index"],
                str(len(points)), dt.datetime.now(common.UTC).isoformat()]))
    for kind, header, rows in (("events", EVENT, ev), ("bands", BAND, bs), ("prices", PRICE, ps), ("series", SERIES, receipts)):
        if rows:
            common.write_csv(os.path.join(root, kind + "-" + stamp + ".csv.gz"), header,
                             ([r[h] for h in header] for _, r in sorted(rows.items())))
    return {"night": stamp, "events_written": len(ev), "series_asked": len(jobs), "series_failed": failed,
            "refused_events": refused, "outside_active_cities": outside,
            "points_written": len(ps), "seconds": round(time.monotonic() - t0, 2)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", help="night in UTC")
    ap.add_argument("--budget", type=float, default=BUDGET_S)
    args = ap.parse_args(argv)
    result = run(dt.date.fromisoformat(args.end) if args.end else None, args.budget)
    print(json.dumps(result))
    if os.environ.get("SUPABASE_URL"):
        common.ops().log_run(JOB, "partial" if result["series_failed"] or result["refused_events"] else "ok",
                             result["points_written"], result)
    return result


if __name__ == "__main__":
    main()
