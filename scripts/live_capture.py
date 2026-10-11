#!/usr/bin/env python3
"""The live record (WXPredict build WP3, phase 2.2/2.3; Hassan, 11 Oct: "go
ahead with live capture").

The training table decides what each row may know by rule (features.py:
REPORT_LAG_S, PUBLISH_H, the market's hourly point). Nothing recorded what the
live system actually had at a decision, so the rules could not be checked
live (the parity test, phase 2.3) and no model could be judged on the inputs
it would really have had. This runs in the hourly tick, beside the
checkpoints, and keeps every input as it arrives, with the time it arrived, in
public.live_capture (supabase/migrations/20261011210000_the_live_record.sql):

  books   every open bucket's YES book (the buckets the 2-hourly snapshot
          reads: an open market resolving from 8 h ago on). POST /books takes
          at most BOOKS_PER_REQUEST tokens: 869 in one request were refused
          ("Payload exceeds the limit"), 500 answered in 0.81 s with 1.2 MB
          (11 Oct). Per bucket: best bid, best ask, the size at each, the USD
          within 5 cents of each touch (the snapshot's own tier rule), the
          last trade, the book's own time, the minimum order and the tick.
  hourly  Open-Meteo's best_match `_previous_day1` for the seven variables the
          training reads (honest_record.HEATING), every active city, every
          UTC hour from yesterday's midnight to HORIZON_H ahead: the hours a
          decision now may know under the training's rule (an hour is known
          24 - PUBLISH_H = 17 h before it). For an hour no run from a day
          before covers, the API answers with the current forecast (measured
          11 Oct 18:15Z), so what was served when is itself the record.
  report  every report the tick's own IEM read returned (tick.py asks once
          for every field the training reads and leaves the answer in
          LIVE_CAPTURE_IEM; no second request), each field as IEM gave it.

A report or hourly value is stored once per change (live_capture_put), with
the first time it was seen. Off or manual on the Workflows page
(P2.12_live_capture) stores nothing. Each part's failure is recorded and stops
nothing else; one ingest_log row a run. Never raises into the tick.

    python scripts/live_capture.py [--dry-run]
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import _get, _post, get_cities, log_run, rest, rest_all, rpc  # noqa: E402
import honest_record  # noqa: E402
import ingest_observations  # noqa: E402

JOB = "P2.12_live_capture"
SWITCH = "P2.12_live_capture"
CLOB_BOOKS = "https://clob.polymarket.com/books"
BOOKS_PER_REQUEST = 400
DEPTH_C = 0.05
# The training's rule: an hourly value for H is known from H - (24 - PUBLISH_H) h
# (tools/wxpredict/features.py hourly_known; PUBLISH_H 7).
HORIZON_H = 17
CITIES_PER_REQUEST = honest_record.CHUNK
REQUEST_TIMEOUT_S = 15
# The tick reads the stations first; its answer is normally on disk a few
# seconds in. Waited for no longer than this.
IEM_WAIT_S = 45
BUDGET_S = 95.0
RESERVE_S = 5.0
PUT_ROWS = 2000
FULL_FIELDS = ingest_observations.FULL_DATA.split(",")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _r6(x):
    return None if x is None else round(x, 6)


# ---------------------------------------------------------------------------
# books
# ---------------------------------------------------------------------------
def book_top(book):
    """[bid, ask, bid size, ask size, bid USD within DEPTH_C, ask USD within
    DEPTH_C, last trade, book time (s), min order size, tick size] of one CLOB
    book. The touch is found by price, not by position: the venue lists bids
    ascending and asks descending (11 Oct), and nothing promises it."""
    def levels(side):
        out = []
        for lv in book.get(side) or []:
            p, s = _num(lv.get("price")), _num(lv.get("size"))
            if p is not None and s is not None and s > 0:
                out.append((p, s))
        return out
    bids, asks = levels("bids"), levels("asks")
    bid = max((p for p, _ in bids), default=None)
    ask = min((p for p, _ in asks), default=None)
    e = 1e-9
    bid_sz = sum(s for p, s in bids if p == bid) if bid is not None else None
    ask_sz = sum(s for p, s in asks if p == ask) if ask is not None else None
    bid_usd = sum(p * s for p, s in bids if bid - p <= DEPTH_C + e) if bid is not None else None
    ask_usd = sum(p * s for p, s in asks if p - ask <= DEPTH_C + e) if ask is not None else None
    ts = _num(book.get("timestamp"))
    return [_r6(bid), _r6(ask), _r6(bid_sz), _r6(ask_sz), _r6(bid_usd), _r6(ask_usd),
            _r6(_num(book.get("last_trade_price"))), None if ts is None else int(ts // 1000),
            _num(book.get("min_order_size")), _num(book.get("tick_size"))]


def open_bands(now):
    """{token_yes: band_id} for every open bucket: the snapshot's own scope
    (n8n P0.3: markets not closed, resolving from 8 h ago on)."""
    rows = rest_all("bands", [
        ("select", "band_id,token_yes,markets!inner(resolution_date,closed)"),
        ("markets.closed", "eq.false"),
        ("markets.resolution_date", f"gte.{(now - dt.timedelta(hours=8)).date().isoformat()}"),
        ("token_yes", "not.is.null"),
    ], order="band_id.asc", page_size=1000)
    return {str(r["token_yes"]): str(r["band_id"]) for r in rows if r.get("token_yes")}


def fetch_books(band_of, left):
    """({band_id: top}, detail). One request per BOOKS_PER_REQUEST tokens; a
    request that fails is recorded and the others are kept."""
    tokens = sorted(band_of)
    tops, errors, asked = {}, [], 0
    for i in range(0, len(tokens), BOOKS_PER_REQUEST):
        if left() < RESERVE_S:
            errors.append(f"tokens {i}-{len(tokens)}: not asked, out of time")
            break
        chunk = tokens[i:i + BOOKS_PER_REQUEST]
        asked += len(chunk)
        try:
            r = _post(CLOB_BOOKS, headers={"content-type": "application/json"},
                      data=json.dumps([{"token_id": t} for t in chunk]),
                      timeout=min(REQUEST_TIMEOUT_S, max(1.0, left() - RESERVE_S)))
            r.raise_for_status()
            for book in r.json() or []:
                band = band_of.get(str(book.get("asset_id")))
                if band:
                    tops[band] = book_top(book)
        except Exception as e:
            errors.append(f"tokens {i}-{i + len(chunk)}: {type(e).__name__}: {str(e)[:120]}")
    return tops, {"buckets": len(tokens), "asked": asked, "books": len(tops), "errors": errors}


# ---------------------------------------------------------------------------
# the hourly forecast
# ---------------------------------------------------------------------------
def hourly_window(now):
    """(first, last) naive UTC hours kept: yesterday's midnight to HORIZON_H ahead."""
    first = dt.datetime(now.year, now.month, now.day) - dt.timedelta(days=1)
    last = now.replace(tzinfo=None, minute=0, second=0, microsecond=0) + dt.timedelta(hours=HORIZON_H)
    return first, last


def hourly_rows(city_key, answer, first, last):
    """[{key, payload}] from one city's answer (asked in GMT): every hour in
    [first, last] with a temperature, the seven HEATING values in order."""
    h = (answer or {}).get("hourly") or {}
    cols = [h.get(f"{v}_previous_day1") or [] for v in honest_record.HEATING]
    out = []
    for i, t in enumerate(h.get("time") or []):
        stamp = dt.datetime.fromisoformat(t)
        vals = [c[i] if i < len(c) else None for c in cols]
        if stamp < first or stamp > last or vals[0] is None:
            continue
        out.append({"key": f"{city_key}|{stamp.strftime('%Y-%m-%dT%H:%M')}", "payload": vals})
    return out


def fetch_hourly(cities, now, left):
    """([{key, payload}], detail). One request per CITIES_PER_REQUEST cities,
    one at a time (honest_record: Open-Meteo refuses several open requests
    from one address); a chunk that fails is recorded, the next tick asks
    again."""
    first, last = hourly_window(now)
    rows, errors, answered = [], [], 0
    for i in range(0, len(cities), CITIES_PER_REQUEST):
        chunk = cities[i:i + CITIES_PER_REQUEST]
        if left() < RESERVE_S:
            errors.append(f"{chunk[0]['city_key']}..: not asked, out of time")
            break
        params = {
            "latitude": ",".join(str(c["latitude"]) for c in chunk),
            "longitude": ",".join(str(c["longitude"]) for c in chunk),
            "timezone": "GMT",
            "start_date": first.date().isoformat(),
            "end_date": last.date().isoformat(),
            "hourly": ",".join(f"{v}_previous_day1" for v in honest_record.HEATING),
        }
        try:
            r = _get(honest_record.PREVIOUS_API, params=params,
                     timeout=min(REQUEST_TIMEOUT_S, max(1.0, left() - RESERVE_S)))
            r.raise_for_status()
            js = r.json()
            answers = [js] if isinstance(js, dict) else js
            if not isinstance(answers, list) or len(answers) != len(chunk):
                raise ValueError(f"{len(answers) if isinstance(answers, list) else 'no'} answers "
                                 f"for {len(chunk)} places")
            for city, a in zip(chunk, answers):
                rows += hourly_rows(city["city_key"], a, first, last)
            answered += len(chunk)
        except Exception as e:
            errors.append(f"{chunk[0]['city_key']}..{chunk[-1]['city_key']}: "
                          f"{type(e).__name__}: {str(e)[:120]}")
    return rows, {"cities": len(cities), "answered": answered, "values": len(rows),
                  "window": [first.isoformat(), last.isoformat()], "errors": errors}


# ---------------------------------------------------------------------------
# the station reports
# ---------------------------------------------------------------------------
def report_rows(text):
    """[{key, payload}] from IEM's CSV: key '<station>|<valid>', the payload
    every FULL_FIELDS value exactly as IEM wrote it."""
    out = []
    for rec in csv.DictReader(io.StringIO(text or "")):
        station = (rec.get("station") or "").strip()
        valid = (rec.get("valid") or "").strip()
        if not station or not valid:
            continue
        out.append({"key": f"{station}|{valid}", "payload": [rec.get(f) for f in FULL_FIELDS]})
    return out


def read_iem(path, wait_s, left):
    """(received_at, text) the tick left at `path`, or None: the first line is
    when the answer reached the tick, the rest the answer."""
    t0 = time.monotonic()
    while not os.path.exists(path):
        if time.monotonic() - t0 > wait_s or left() < RESERVE_S:
            return None
        time.sleep(0.5)
    with open(path, encoding="utf-8") as fh:
        stamp, _, text = fh.read().partition("\n")
    return dt.datetime.fromisoformat(stamp.strip()), text


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def put(kind, captured_at, rows, dry_run, left=lambda: BUDGET_S):
    """Rows stored, in batches; (asked, new). Each write waits no longer than
    the run has left, so the backstop never ends a write half-way through the
    run's own row."""
    if dry_run or not rows:
        return len(rows), 0
    new = 0
    for i in range(0, len(rows), PUT_ROWS):
        got = rpc("live_capture_put", {"p_kind": kind, "p_captured_at": captured_at.isoformat(),
                                       "p_rows": rows[i:i + PUT_ROWS]}, timeout=max(3.0, min(60.0, left())))
        new += int(got.get("new") or 0)
    return len(rows), new


def mode():
    rows = rest("settings", {"select": "value", "key": "eq.workflow_schedules"})
    value = (rows[0].get("value") if rows else None) or {}
    return ((value.get(SWITCH) or {}).get("mode") or "auto")


def budget(started, deadline=None):
    """Seconds left: BUDGET_S from the start, and never past the tick's deadline."""
    left = BUDGET_S - (time.monotonic() - started)
    if deadline:
        left = min(left, float(deadline) - time.time())
    return left


def run(now=None, dry_run=False, iem_path=None):
    started = time.monotonic()
    deadline = os.environ.get("TICK_DEADLINE")
    left = lambda: budget(started, deadline)  # noqa: E731
    now = now or dt.datetime.now(dt.timezone.utc)
    detail = {"dry_run": dry_run}
    try:
        m = mode()
    except Exception as e:
        m = "auto"
        detail["switch_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    detail["mode"] = m
    if m in ("off", "manual"):
        log_run(JOB, "skipped", 0, detail)
        return "skipped", detail

    parts_ok, new_total = [], 0

    # books
    try:
        t0 = time.monotonic()
        band_of = open_bands(now)
        tops, d = fetch_books(band_of, left)
        captured = dt.datetime.now(dt.timezone.utc)
        d["new"] = put("books", captured, [{"key": "all", "payload": tops}] if tops else [], dry_run, left)[1]
        d["seconds"] = round(time.monotonic() - t0, 1)
        detail["books"] = d
        parts_ok.append(bool(tops) and not d["errors"])
        new_total += d["new"]
    except Exception as e:
        detail["books"] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        parts_ok.append(False)

    # the hourly forecast
    try:
        t0 = time.monotonic()
        cities = [c for c in get_cities() if c.get("latitude") is not None]
        rows, d = fetch_hourly(cities, now, left)
        captured = dt.datetime.now(dt.timezone.utc)
        d["new"] = put("hourly", captured, rows, dry_run, left)[1]
        d["seconds"] = round(time.monotonic() - t0, 1)
        detail["hourly"] = d
        parts_ok.append(d["answered"] == d["cities"] and not d["errors"])
        new_total += d["new"]
    except Exception as e:
        detail["hourly"] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        parts_ok.append(False)

    # the station reports, from the tick's own answer
    try:
        t0 = time.monotonic()
        path = iem_path or os.environ.get("LIVE_CAPTURE_IEM")
        got = read_iem(path, IEM_WAIT_S, left) if path else None
        if got is None:
            detail["reports"] = {"error": "no IEM answer from the tick" + ("" if path else " (LIVE_CAPTURE_IEM unset)")}
            parts_ok.append(False)
        else:
            received, text = got
            rows = report_rows(text)
            asked, new = put("report", received, rows, dry_run, left)
            detail["reports"] = {"received_at": received.isoformat(), "rows": asked, "new": new,
                                 "stations": len({r["key"].split("|")[0] for r in rows}),
                                 "seconds": round(time.monotonic() - t0, 1)}
            parts_ok.append(asked > 0)
            new_total += new
    except Exception as e:
        detail["reports"] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        parts_ok.append(False)

    detail["seconds"] = round(time.monotonic() - started, 1)
    status = "ok" if all(parts_ok) else ("error" if not any(parts_ok) else "partial")
    if not dry_run:
        log_run(JOB, status, new_total, detail)
    return status, detail


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--killed", metavar="EXIT_CODE",
                    help="the run's timeout ended it: write its row and stop (tick.yml's collecting step)")
    args = ap.parse_args(argv)
    if args.killed:
        log_run(JOB, "error", 0, {"killed": True, "exit_code": args.killed,
                                  "note": "ended by tick.yml's timeout before it wrote its own row"})
        return 0
    status, detail = run(dry_run=args.dry_run)
    print(json.dumps({"status": status, **detail}, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
