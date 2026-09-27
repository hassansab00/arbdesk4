"""Trade prints for every open market (replaces n8n P0.4, plan v2 P6.2).

WHY IT WAS REPLACED. n8n P0.4 loaded bands with no ordering, kept the first
120 distinct markets and fetched their tapes. With no ORDER BY, Postgres
handed back the same subset every run - markets that had already resolved -
so every run re-read the same 5,000 trades, the insert ignored every one of
them as a duplicate, and the log said "5000 trades written". Measured 25 Sep:
the newest print in trades_observed was 24 Sep 09:42Z, and the last three runs
reported the identical $119,846.20. It also cost 4 n8n executions a day.

WHAT THIS DOES INSTEAD.
  * Markets: every band of every OPEN market resolving yesterday or later
    (1,045 condition ids on 25 Sep). Nothing is capped away.
  * Requests: Polymarket's data API takes a comma-separated `market` list
    (verified 25 Sep: three markets together returned exactly the 13 + 246 +
    58 trades they return alone; and on 25 Sep one request for 100 markets
    returned the same 992 trades as two requests for 50 over a shared 2.6 h
    window). BATCH ids per request, newest first: 100 took all 1,045 markets
    in 11 requests and 7.5 s from a cloud container, 50 took 21 and 14.5 s.
  * Paging: `limit`/`offset`, stopping at the first page older than the
    high-water mark - the newest trade already stored, less an overlap - so an
    hourly run reads roughly one page per batch.
  * Rows: the P0.4 mapping exactly (band by `asset` token; condition, city,
    token, side, price, size, traded_at from `timestamp`, proxy_wallet never
    null), written with ignore-duplicates on the same key.
  * Counting: PostgREST returns only the rows it actually inserted
    (return=representation with ignore-duplicates), so `rows` is NEW trades,
    never rows sent.

A TIME BUDGET. It runs inside the hourly tick job, which is billed one minute
while it finishes inside 60 s. A run that runs out of BUDGET_S says so in its
log (complete: false, the cursor it reached, the mark it used), and the next
run carries on after that cursor with the OLDER mark - otherwise the markets
it never reached would be read from a mark set by trades on the ones it did,
and their gap would be skipped for good (see plan()). Measured 25 Sep 09:37Z,
the first run: a day's backlog took 20 s for one batch of 100 markets, so the
backlog is read one batch per hour until the cycle reaches the end.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import requests

from common import _cfg, _headers, _post, log_run, rest, rest_all, rpc

API = "https://data-api.polymarket.com/trades"
BATCH = 100
PAGE = 1000
MAX_PAGES = 10               # the API refuses an offset over 10,000 (HTTP 400, measured 25 Sep)
BUDGET_S = 15.0
# What a run needs besides its batches (loading the bands, refresh_derived,
# the log): a light run took 5.6 s in all on 26 Sep 01:37Z (one batch, 40 new
# trades), so that much is kept back from the tick's deadline.
RESERVE_S = 6.0
OVERLAP = dt.timedelta(hours=1)
UA = {"User-Agent": "arbdesk4-trades/1.0", "Accept": "application/json"}
CONFLICT = "condition_id,traded_at,price,size,proxy_wallet"


def switched_on():
    """The Workflows page's switch for P0.4: (on, mode).

    Only the MODE is honoured. should_run() also enforces every_minutes, which
    is n8n's cadence (300) and would skip four hourly runs in five; the tick
    sets the cadence now. A missing row runs, like should_run().
    """
    r = rest("settings", [("select", "value"), ("key", "eq.workflow_schedules")])
    mode = ((r[0].get("value") or {}).get("P0.4_trade_history") or {}).get("mode", "auto") if r else "auto"
    return mode == "auto", mode


def open_bands(today):
    rows = rest_all("bands", {
        "select": "band_id,token_yes,token_no,condition_id,markets!inner(city_key,resolution_date,closed)",
        "markets.closed": "eq.false",
        "markets.resolution_date": f"gte.{today - dt.timedelta(days=1)}",
        "condition_id": "not.is.null"}, order="band_id.asc")
    return rows


def high_water():
    """The newest trade already stored, or None on an empty table."""
    r = rest("trades_observed", [("select", "traded_at"), ("order", "traded_at.desc"), ("limit", "1")])
    return dt.datetime.fromisoformat(r[0]["traded_at"].replace("Z", "+00:00")) if r else None


def to_rows(trades, by_token, ingested_at):
    """P0.4's mapping, unchanged. Returns (rows, unmatched)."""
    rows, unmatched = [], 0
    for t in trades:
        token = str(t.get("asset") or "")
        band = by_token.get(token)
        if band is None:
            unmatched += 1
            continue
        try:
            price, size = float(t["price"]), float(t["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if size <= 0 or not 0 < price < 1:
            continue
        ts = t.get("timestamp")
        if ts is None:
            continue
        ts = float(ts)
        traded_at = dt.datetime.fromtimestamp(ts if ts < 1e12 else ts / 1000, dt.timezone.utc)
        rows.append({
            "band_id": band["band_id"],
            "condition_id": t.get("conditionId") or band["condition_id"],
            "city_key": band["markets"]["city_key"],
            "token_id": token,
            "side": t.get("side"),
            "price": price,
            "size": size,
            "traded_at": traded_at.isoformat(),
            "proxy_wallet": t.get("proxyWallet") or "",
            "ingested_at": ingested_at,
        })
    return rows, unmatched


def previous_run():
    """The last run's detail, or {} (an n8n-era row has no 'complete' key: treated as complete)."""
    r = rest("ingest_log", [("select", "detail"), ("job", "eq.P0.4_trade_history"),
                            ("status", "neq.skipped"), ("order", "logged_at.desc"), ("limit", "1")])
    return (r[0].get("detail") or {}) if r else {}


def plan(hw, prev):
    """(since, cursor) for this run.

    A CYCLE reads every open market once, in condition-id order, from the
    start of the list to its end. It can take several runs - while a backlog
    is being read, one batch can use the whole budget - so a run that stops
    early saves a CURSOR, the last condition id it finished, and the next run
    carries on after it with the SAME mark. Only a run that reaches the end of
    the list starts the next cycle from the new high-water mark. (Until 25 Sep
    the saved position was a batch number: it could not finish a cycle that
    spanned runs, and the numbering moved whenever a market opened.)

    cursor '' is the start of the list. A run logged before cursors existed
    carries no cursor: its cycle restarts from the start with its mark.
    """
    since = hw - OVERLAP if hw else None
    cursor = ""
    if prev.get("complete") is False and prev.get("since"):
        prev_since = dt.datetime.fromisoformat(prev["since"])
        since = prev_since if since is None else min(since, prev_since)
        cursor = prev.get("cursor") or ""
    return since, cursor


def fetch_batch(ids, since, get=requests.get):
    """Every trade on these markets newer than `since`, newest first, paged."""
    out = []
    for page in range(MAX_PAGES):
        r = get(API, params={"market": ",".join(ids), "limit": PAGE, "offset": page * PAGE},
                headers=UA, timeout=20)
        r.raise_for_status()
        got = r.json()
        if not isinstance(got, list):
            raise ValueError(f"trades API returned {type(got).__name__}, not a list")
        out.extend(got)
        if len(got) < PAGE:
            break
        if since is not None and min(float(t["timestamp"]) for t in got) < since.timestamp():
            break
    return out


def insert_new(rows):
    """Insert, ignoring duplicates; return how many were actually new.

    No in-run retry (common._post_batch sleeps 5-15 s, the whole budget): a
    failed insert fails its batch, and the next run resumes at that batch from
    the older mark, so nothing is skipped.
    """
    if not rows:
        return 0
    h = _headers()
    h["Prefer"] = "resolution=ignore-duplicates,return=representation"
    new = 0
    for i in range(0, len(rows), 500):
        r = _post(f"{_cfg()['url']}/rest/v1/trades_observed", headers=h,
                  params={"on_conflict": CONFLICT, "select": "trade_id"},
                  data=json.dumps(rows[i:i + 500]), timeout=60)
        if r.status_code >= 400:
            raise requests.HTTPError(f"trades_observed -> HTTP {r.status_code}: {r.text[:300]}", response=r)
        new += len(r.json())
    return new


def budget(budget_s, deadline=None, now_epoch=None):
    """The seconds this run may spend on batches: the flag, and never past the
    tick job's deadline (TICK_DEADLINE, epoch seconds, set by tick.yml) less
    RESERVE_S."""
    if deadline is None:
        return budget_s
    left = float(deadline) - (time.time() if now_epoch is None else now_epoch) - RESERVE_S
    return min(budget_s, left)


def main(budget_s=BUDGET_S, now=None):
    started = time.monotonic()
    budget_s = budget(budget_s, os.environ.get("TICK_DEADLINE"))
    if budget_s <= 0:
        # No time left: no batch would start, but the reads before the loop
        # still ran. On 27 Sep 12:36Z this step began 2 s before the deadline,
        # took 4 s, and put the tick job at 62 s, past its one billed minute.
        # So read only where the last run stopped, carry it forward so the next
        # hour resumes there, and say so (attention, never a silent skip).
        prev = previous_run()
        detail = {"summary": "no time left before the tick's deadline: nothing read; resumes next hour",
                  "budget_s": round(budget_s, 1), "complete": bool(prev.get("complete", False)),
                  "since": prev.get("since"), "cursor": prev.get("cursor") or ""}
        log_run("P0.4_trade_history", "attention", 0, detail)
        print(detail["summary"])
        return {**detail, "status": "attention"}
    now = now or dt.datetime.now(dt.timezone.utc)
    on, mode = switched_on()
    if not on:
        detail = {"summary": f"P0.4_trade_history is {mode} on the Workflows page - not run", "mode": mode}
        log_run("P0.4_trade_history", "skipped", 0, detail)
        print(detail["summary"])
        return {**detail, "status": "skipped"}
    bands = open_bands(now.date())
    by_token = {}
    for b in bands:
        for k in ("token_yes", "token_no"):
            if b.get(k):
                by_token[str(b[k])] = b
    ids = sorted({b["condition_id"] for b in bands})
    hw = high_water()
    since, cursor = plan(hw, previous_run())
    todo = [c for c in ids if c > cursor]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    ingested_at = now.isoformat()
    fetched = new = unmatched = batches_done = 0
    errors, truncated = [], []
    done_to, resume_at, stopped = cursor, None, False
    slowest = 0.0
    for batch in batches:
        # Start a batch only if one as slow as the slowest so far still ends
        # inside the budget. Checking only the time already spent let a second
        # backlog batch start at 14 s of 15 on 25 Sep 10:36Z: the step took
        # 27.6 s and the tick job 58 s of its one billed minute.
        if time.monotonic() - started + slowest > budget_s:
            stopped = True
            break
        t0 = time.monotonic()
        try:
            raw = fetch_batch(batch, since)
            if since is not None and len(raw) >= MAX_PAGES * PAGE and \
                    min(float(t["timestamp"]) for t in raw) >= since.timestamp():
                # Every page full and still newer than the mark: the API will
                # not page deeper, so the trades between are out of reach.
                # Said in the log, never silently skipped.
                truncated.append(batch[0])
            trades = [t for t in raw if since is None or float(t["timestamp"]) >= since.timestamp()]
            rows, um = to_rows(trades, by_token, ingested_at)
            fetched += len(rows)
            unmatched += um
            new += insert_new(rows)
            batches_done += 1
        except Exception as e:                       # noqa: BLE001 - counted and reported
            errors.append(f"after {done_to or 'the start'}: {str(e)[:160]}")
            # A failed batch is resumed like an unreached one: its gap must not
            # be skipped when the high-water mark moves on. The batches after
            # it still run, and are read again next time (duplicates ignored).
            if resume_at is None:
                resume_at = done_to
        done_to = batch[-1]
        slowest = max(slowest, time.monotonic() - t0)
    complete = resume_at is None and not stopped
    next_cursor = None if complete else (resume_at if resume_at is not None else done_to)
    rollups = None
    if new:
        try:
            rollups = rpc("refresh_derived")
        except Exception as e:                       # noqa: BLE001
            errors.append(f"refresh_derived: {str(e)[:160]}")
    status = "error" if errors and not batches_done else (
        "attention" if errors or truncated or not complete else "ok")
    detail = {"markets": len(ids), "batches": f"{batches_done}/{len(batches)}", "high_water": hw.isoformat() if hw else None,
              "since": since.isoformat() if since else None, "complete": complete,
              "cursor": next_cursor, "truncated": truncated,
              "fetched": fetched, "new": new, "unmatched": unmatched, "errors": errors[:5],
              "seconds": round(time.monotonic() - started, 1), "budget_s": round(budget_s, 1), "rollups": rollups,
              "summary": f"{new} new trades from {len(ids)} open markets "
                         f"({batches_done} of the {len(batches)} batches left in the cycle, {fetched} fetched since the mark)"}
    log_run("P0.4_trade_history", status, new, detail)
    print(detail["summary"], "| errors:", len(errors))
    detail["status"] = status
    return detail


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=BUDGET_S)
    a = ap.parse_args()
    d = main(budget_s=a.budget)
    # Only a run in which nothing worked fails the step; partial trouble is in
    # the log row, and the tick it rides with must not turn red for a page.
    sys.exit(1 if d["status"] == "error" else 0)
