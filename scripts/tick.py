"""The hourly tick (plan v2 P6.1), first stage: the checkpoint writer (P4.2).

WHAT WE SAID, WHEN WE SAID IT. prediction_checkpoints (P4.1) holds one
immutable ladder per city, target day and checkpoint - the price the engine
published at a fixed moment on the CITY'S clock, with the market's book beside
it. The scoreboard (P4.6) grades those rows, never a price computed after the
fact: databank froze the newest price with no time limit, and 302 of 496
frozen ladders were priced after the local close (plan v2 P4.3).

Each run:

  1. works out which checkpoints are DUE: a decision time in the last
     GRACE_MIN minutes, for a market the engine may still price, not already
     written by this engine version;
  2. prices those city-days only, with the same process_city_day the intraday
     pipeline uses (same caches, same floors, same thread pool);
  3. reads every due band's CLOB book in ONE request;
  4. writes one row per checkpoint, ignoring a duplicate - the table refuses
     an edit, so a second tick in the same window can only add nothing.

Checkpoints (local time): d1_eve 18:00 the day before; morning 09:00;
noon 12:00; prepeak_2h / prepeak_1h / postpeak_1h around the city's measured
peak hour for that month (derived_weather_peak). A city with no measured peak
skips those three and says so - 15:00 would be a guess.

A TIME BUDGET, NOT A HOPE. Work that cannot finish inside BUDGET_S is left
for the next tick and logged as deferred, never silently dropped: GitHub bills
each job in whole minutes, so a 61-second tick costs two (plan v2 P6.1).
"""
import datetime as dt
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from zoneinfo import ZoneInfo

from common import _post, get_cities, log_run, rest, rest_all, upsert

CHECKPOINTS = ("d1_eve", "morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")
PEAK_OFFSETS_H = {"prepeak_2h": -2, "prepeak_1h": -1, "postpeak_1h": 1}
FIXED_LOCAL = {"morning": (0, 9, 0), "noon": (0, 12, 0), "d1_eve": (-1, 18, 0)}

# A tick runs hourly at :36 (n8n's clock), so a decision time is normally 36-96 minutes old
# when the first tick after it runs. 75 minutes catches every checkpoint once
# with the tick on time, and twice across a 15-minute overlap - the second
# write is ignored by the table's unique key.
GRACE_MIN = 75
BUDGET_S = 45.0
CLOB_BOOKS = "https://clob.polymarket.com/books"
ON_CONFLICT = "city_key,target_date,checkpoint,engine_version"
SUM_TOLERANCE = 1e-4


def engine_version():
    sha = os.environ.get("GITHUB_SHA") or ""
    return f"git:{sha[:12]}" if sha else "local"


# --------------------------------------------------------------------------
# 1. what is due
# --------------------------------------------------------------------------
def decision_times(target_date, timezone, peak_hour_local):
    """{checkpoint: (local naive datetime, utc datetime)} for one city-day.

    peak_hour_local is the measured modal peak (fractional hours) or None."""
    zone = ZoneInfo(timezone)
    day = dt.date.fromisoformat(str(target_date))
    out = {}
    for name, (day_off, hh, mm) in FIXED_LOCAL.items():
        local = dt.datetime.combine(day + dt.timedelta(days=day_off), dt.time(hh, mm))
        out[name] = local
    if peak_hour_local is not None:
        peak = dt.datetime.combine(day, dt.time(0, 0)) + dt.timedelta(
            minutes=round(float(peak_hour_local) * 60))
        for name, off in PEAK_OFFSETS_H.items():
            out[name] = peak + dt.timedelta(hours=off)
    return {k: (v, v.replace(tzinfo=zone).astimezone(dt.timezone.utc)) for k, v in out.items()}


def due_checkpoints(now, markets, tz_of, peak_of, written, grace_min=GRACE_MIN):
    """([(city_key, target_date, checkpoint, local_time)], [skip notes]).

    markets: v_priceable_markets rows; tz_of: city -> timezone;
    peak_of: (city, month) -> peak_hour_local; written: set of
    (city, target_date, checkpoint) this engine version already holds."""
    due, notes = [], []
    lo = now - dt.timedelta(minutes=grace_min)
    seen = set()
    for m in markets:
        city, target = m["city_key"], str(m["resolution_date"])
        if (city, target) in seen:
            continue
        seen.add((city, target))
        tz = tz_of.get(city)
        if not tz:
            notes.append(f"{city} {target}: no timezone")
            continue
        month = dt.date.fromisoformat(target).month
        peak = peak_of.get((city, month))
        times = decision_times(target, tz, peak)
        for name in CHECKPOINTS:
            if name not in times:
                if name in PEAK_OFFSETS_H and _in_window(
                        decision_times(target, tz, 15.0)[name][1], lo, now):
                    notes.append(f"{city} {target} {name}: no measured peak hour for month {month}")
                continue
            local, utc = times[name]
            if not _in_window(utc, lo, now):
                continue
            if (city, target, name) in written:
                continue
            due.append((city, target, name, local))
    return due, notes


def _in_window(t, lo, hi):
    return lo < t <= hi


# --------------------------------------------------------------------------
# 3. the market beside the call
# --------------------------------------------------------------------------
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def book_top(book):
    """{'bid','ask','last'} from one CLOB book. The levels' order is not relied
    on: the best bid is the highest bid, the best ask the lowest ask."""
    bids = [p for p in (_num(l.get("price")) for l in book.get("bids") or []) if p is not None]
    asks = [p for p in (_num(l.get("price")) for l in book.get("asks") or []) if p is not None]
    return {"bid": max(bids) if bids else None, "ask": min(asks) if asks else None,
            "last": _num(book.get("last_trade_price"))}


def market_price(top):
    """The market's number for a band: the mid of a two-sided book, else the
    last trade, else None."""
    if top.get("bid") is not None and top.get("ask") is not None:
        return round((top["bid"] + top["ask"]) / 2, 6)
    return top.get("last")


def fetch_books(token_of_band):
    """{band_id: top} for every band with a YES token, in one request. The
    response is matched by asset_id, not by position."""
    tokens = {str(t): b for b, t in token_of_band.items() if t}
    if not tokens:
        return {}
    r = _post(CLOB_BOOKS, headers={"content-type": "application/json"},
              data=json.dumps([{"token_id": t} for t in tokens]), timeout=15)
    r.raise_for_status()
    out = {}
    for book in r.json() or []:
        band = tokens.get(str(book.get("asset_id")))
        if band:
            out[band] = book_top(book)
    return out


# --------------------------------------------------------------------------
# 4. the row
# --------------------------------------------------------------------------
def _path_and_model(reasons):
    label = next((r[len("priced_from:"):] for r in reasons or [] if r.startswith("priced_from:")), "")
    path = "forecast"
    rest_label = label
    if rest_label.startswith("trajectory:"):
        path = "trajectory"
        rest_label = rest_label[len("trajectory:"):]
        hour, _, tail = rest_label.partition(":")
        if len(hour) == 3 and hour.endswith("h") and hour[:2].isdigit():
            rest_label = tail
    if rest_label.startswith("model:"):
        path = "model" if path == "forecast" else "trajectory+model"
    model = rest_label.split(":", 1)[0] if rest_label else None
    if model == "model":
        model = "arbdesk_weather_model"
    return path, model


def build_row(city, target, checkpoint, local_time, rows, reasons, books, reading, version):
    """One prediction_checkpoints row, or (None, why) when it cannot be one.

    Checked here against the table's own constraints, because one row the
    database refuses would take its whole batch down with it."""
    probs = {str(r["band_id"]): float(r["calibrated_prob"]) for r in rows
             if r.get("calibrated_prob") is not None}
    if not probs or len(probs) != len(rows):
        return None, "a band has no probability"
    total = sum(probs.values())
    if abs(total - 1) > SUM_TOLERANCE:
        return None, f"ladder sums to {total:.6f}"
    ranked = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
    head = rows[0]
    path, model = _path_and_model(reasons)
    market = {b: books[b] for b in probs if b in books}
    priced = {b: market_price(t) for b, t in market.items()}
    priced = {b: p for b, p in priced.items() if p is not None}
    inputs_ok = bool(head.get("pricing_eligible", True))
    row = {
        "city_key": city, "target_date": target, "checkpoint": checkpoint,
        "local_decision_time": local_time.isoformat(timespec="minutes"),
        "engine_version": version, "model_path": path,
        "probs": probs,
        "top_band_id": ranked[0][0], "top_prob": ranked[0][1],
        "second_prob": ranked[1][1] if len(ranked) > 1 else None,
        "centre_c": head.get("centre_c"), "sigma_c": head.get("sigma_c"),
        "running_max_c": head.get("observed_floor_c"),
        "reading_at": (reading or {}).get("latest_reading_at") if head.get("observed_floor_c") is not None else None,
        "reading_source": (reading or {}).get("live_source_kind") if head.get("observed_floor_c") is not None else None,
        "forecast_issued_at": head.get("input_forecast_run"),
        "forecast_model": model,
        "market": market or None,
        "market_top_band_id": (max(priced.items(), key=lambda kv: (kv[1], kv[0]))[0]
                               if priced else None),
        "inputs_ok": inputs_ok,
        "block_reason": None if inputs_ok else (head.get("pricing_block_reason") or "pricing_ineligible"),
    }
    return row, None


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------
def _written(version, keys):
    """The (city, target, checkpoint) this version already holds among keys."""
    if not keys:
        return set()
    cities = sorted({k[0] for k in keys})
    dates = sorted({k[1] for k in keys})
    rows = rest_all("prediction_checkpoints", [
        ("select", "city_key,target_date,checkpoint"),
        ("engine_version", f"eq.{version}"),
        ("city_key", f"in.({','.join(cities)})"),
        ("target_date", f"in.({','.join(dates)})"),
    ], order="checkpoint_id.asc", page_size=1000)
    return {(r["city_key"], str(r["target_date"]), r["checkpoint"]) for r in rows}


def run(now=None, budget_s=BUDGET_S, dry_run=False):
    import probability_engine as pe

    t0 = time.monotonic()
    now = now or dt.datetime.now(dt.timezone.utc)
    version = engine_version()

    cities = get_cities(require_coords=False)
    tz_of = {c["city_key"]: c.get("timezone") for c in cities}
    unit_of = {c["city_key"]: (c.get("unit") or "C") for c in cities}
    markets = pe._upcoming_markets()
    peak_of = {(r["city_key"], int(r["month"])): r["peak_hour_local"]
               for r in rest("derived_weather_peak",
                             {"select": "city_key,month,peak_hour_local"})
               if r.get("peak_hour_local") is not None}

    candidates, notes = due_checkpoints(now, markets, tz_of, peak_of, written=set())
    held = _written(version, {(c, t, k) for c, t, k, _ in candidates})
    due = [d for d in candidates if (d[0], d[1], d[2]) not in held]
    detail = {"engine_version": version, "due": len(due), "already_written": len(candidates) - len(due),
              "notes": notes[:20]}
    if not due:
        print(f"tick {now:%Y-%m-%d %H:%MZ}: nothing due ({len(held)} already written)")
        if not dry_run:
            log_run("tick", "ok", 0, dict(detail, seconds=round(time.monotonic() - t0, 1)))
        return detail

    market_of = {(m["city_key"], str(m["resolution_date"])): m for m in markets}
    days = sorted({(c, t) for c, t, _, _ in due})
    bands = pe._bands_for_markets([market_of[d]["market_id"] for d in days])
    bands_by_market = {}
    for b in bands:
        bands_by_market.setdefault(b["market_id"], []).append(b)

    running = {r["city_key"]: r for r in rest("v_city_running_max", {
        "select": "city_key,local_date,running_max_c,running_max_basis,observed_max_today_c,"
                  "live_source_kind,latest_reading_at"})}
    floors = {}
    for city, r in running.items():
        f = pe.measured_floor(r)
        if f is not None and r.get("local_date"):
            floors[city] = (str(r["local_date"]), f)
    promoted = pe._promoted_models()
    model_forecasts = pe._model_forecasts(promoted)
    pe._warm_caches()

    history_cache = {}
    deferred, failed, results = [], [], {}
    workers = max(1, int(os.environ.get("ENGINE_WORKERS", "8")))
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {}
    for city, target in days:
        m = market_of[(city, target)]
        unit = m.get("unit") or unit_of.get(city, "C")
        band_rows = bands_by_market.get(m["market_id"], [])
        if not band_rows:
            failed.append(f"{city} {target}: no canonical bands")
            continue
        futures[pool.submit(pe.process_city_day, city, target, unit, band_rows,
                            history_cache, floors, promoted, model_forecasts)] = (city, target)
    remaining = max(1.0, budget_s - 8.0 - (time.monotonic() - t0))   # 8 s kept for books + write
    done, not_done = wait(futures, timeout=remaining)
    for f in not_done:
        f.cancel()
        deferred.append("%s %s" % futures[f])
    for f in done:
        city, target = futures[f]
        try:
            res = f.result()
        except Exception as e:
            failed.append(f"{city} {target}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if res is None:
            failed.append(f"{city} {target}: no forecast")
            continue
        results[(city, target)] = res
    pool.shutdown(wait=False, cancel_futures=True)

    token_rows = []
    priced_bands = [str(r["band_id"]) for rows, _, _ in results.values() for r in rows]
    for i in range(0, len(priced_bands), 100):
        token_rows += rest("bands", {"select": "band_id,token_yes",
                                     "band_id": f"in.({','.join(priced_bands[i:i + 100])})"})
    try:
        books = fetch_books({str(r["band_id"]): r.get("token_yes") for r in token_rows})
    except Exception as e:
        books = {}
        notes.append(f"CLOB books unavailable: {type(e).__name__}: {str(e)[:120]}")

    out = []
    for city, target, name, local in due:
        res = results.get((city, target))
        if res is None:
            continue
        rows, _reg, reasons = res
        row, why = build_row(city, target, name, local, rows, reasons, books,
                             running.get(city), version)
        if row is None:
            failed.append(f"{city} {target} {name}: {why}")
            continue
        out.append(row)

    written = 0
    if out and not dry_run:
        written = upsert("prediction_checkpoints", out, ON_CONFLICT)
    detail.update({"written": written if not dry_run else 0, "would_write": len(out),
                   "deferred": deferred, "failed": failed[:30],
                   "books": len(books), "seconds": round(time.monotonic() - t0, 1)})
    status = "ok" if not failed and not deferred else "attention"
    print(f"tick {now:%Y-%m-%d %H:%MZ}: {len(due)} due, {len(out)} rows, "
          f"{len(deferred)} deferred, {len(failed)} failed, {detail['seconds']} s")
    for line in failed + [f"deferred: {d}" for d in deferred] + notes:
        print(f"  {line}")
    if not dry_run:
        log_run("tick", status, written, detail)
    if deferred:
        # The deferred city-days are still running on pool threads the
        # interpreter would otherwise wait for; they wrote nothing and the
        # next tick picks them up. Leaving now is what keeps the budget.
        sys.stdout.flush()
        os._exit(0)
    return detail


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    default=os.environ.get("TICK_DRY_RUN", "").lower() == "true",
                    help="price and print, write nothing")
    ap.add_argument("--at", default=os.environ.get("TICK_AT") or None,
                    help="pretend it is this UTC time (ISO), for a rehearsal")
    a = ap.parse_args()
    at = None
    if a.at:
        at = dt.datetime.fromisoformat(a.at)
        at = at.replace(tzinfo=dt.timezone.utc) if at.tzinfo is None else at.astimezone(dt.timezone.utc)
    run(now=at, dry_run=a.dry_run)
