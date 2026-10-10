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

BESIDE THE TICK, NOT AFTER IT (28 Sep). tick.yml ran this last, on whatever
the other steps left before TICK_DEADLINE. As the engine moved into tick.py
that became nothing: from 27 Sep 16:36Z to 28 Sep 19:36Z 9 of 27 ticks left
it no time and 6 more under 5 s, 11 of 28 Sep's 19 runs read no trade, and the cycle stood at its 04:36Z
mark all day (nothing was lost: the mark held, and no batch was truncated).
It waits on the API, not the CPU, so tick.yml now starts it in the background
before the checkpoints with --budget 30; budget() still ends it before the
same deadline, so the job stays inside its one billed minute.

--budget 60 FROM 9 OCT (Wave 2.D). The tick has 110 s from #353 (the
repository is public and its runners are not billed). In the 7 days to 9 Oct
3 of 168 runs stopped at the 30 s with batches left (ingest_log); budget()
still clamps the 60 to the deadline less RESERVE_S.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import requests

from common import _cfg, _headers, _post, _retry_after, log_run, rest, rest_all, rpc

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
RATE_LIMIT_WAIT_S = 5.0      # a 429 without Retry-After (fetch_batch)
RATE_LIMIT_MAX_WAIT_S = 15.0
UA = {"User-Agent": "arbdesk4-trades/1.0", "Accept": "application/json"}
# A print's dedupe key (condition_id, traded_at, price, size, proxy_wallet)
# is held as its 16-byte hash (trade_dedupe_key, WXPredict build 2.A, 8 Oct:
# 19 MB of index as five columns). PostgREST cannot name that expression in
# on_conflict, so prints go in through insert_trade_prints(), which does. The
# path is spelled out whole so tools/gen_provenance.py, which finds a script's
# RPCs by their literal rest/v1/rpc/ path, still names this job as a writer of
# trades_observed.
INSERT_PATH = "/rest/v1/rpc/insert_trade_prints"


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


def fetch_batch(ids, since, get=requests.get, wait_until=None, stats=None,
                sleep=time.sleep, clock=time.monotonic):
    """Every trade on these markets newer than `since`, newest first, paged.

    A 429 IS WAITED OUT ONCE A PAGE (Wave 2.D, R8). In the 7 days to 9 Oct
    19:40Z, 5 of 168 runs lost a batch to `429 Too Many Requests` (ingest_log);
    the batch was read again the next hour from the cursor, so nothing was
    lost, only an hour late. The page is now asked again after Retry-After
    (RATE_LIMIT_WAIT_S when the API sends none, never more than
    RATE_LIMIT_MAX_WAIT_S), once, and only when the wait still ends before
    `wait_until` (the run's budget); otherwise the 429 is raised as before.
    `stats` counts the waits for the run's log row."""
    out = []
    for page in range(MAX_PAGES):
        params = {"market": ",".join(ids), "limit": PAGE, "offset": page * PAGE}
        r = get(API, params=params, headers=UA, timeout=20)
        if getattr(r, "status_code", 200) == 429:
            after = _retry_after(r)        # None when not sent; 0 is a valid "now" (Codex on #356)
            wait = min(RATE_LIMIT_WAIT_S if after is None else max(0.0, after), RATE_LIMIT_MAX_WAIT_S)
            if wait_until is None or clock() + wait < wait_until:
                sleep(wait)
                if stats is not None:
                    stats["waits"] = stats.get("waits", 0) + 1
                    stats["seconds"] = round(stats.get("seconds", 0.0) + wait, 1)
                r = get(API, params=params, headers=UA, timeout=20)
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
    new = 0
    for i in range(0, len(rows), 500):
        r = _post(_cfg()["url"] + INSERT_PATH, headers=_headers(),
                  data=json.dumps({"p_rows": rows[i:i + 500]}), timeout=60)
        if r.status_code >= 400:
            raise requests.HTTPError(f"insert_trade_prints -> HTTP {r.status_code}: {r.text[:300]}", response=r)
        new += len(r.json() or [])
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
    rate_limited = {"waits": 0, "seconds": 0.0}
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
            raw = fetch_batch(batch, since, wait_until=started + budget_s, stats=rate_limited)
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
              "rate_limited": rate_limited,
              "summary": f"{new} new trades from {len(ids)} open markets "
                         f"({batches_done} of the {len(batches)} batches left in the cycle, {fetched} fetched since the mark)"}
    log_run("P0.4_trade_history", status, new, detail)
    print(detail["summary"], "| errors:", len(errors))
    detail["status"] = status
    return detail


# ---------------------------------------------------------------------------
# THE TRADES BEFORE WE KNEW THE MARKET (R46, WXPredict build phase 2.4, 10 Oct).
#
# The hourly cycle above reads every OPEN market from one mark, the newest
# trade stored less OVERLAP. A market enters `bands` only when n8n's P0.2
# discovery first asks for it, at 03:20, 09:20, 15:20 and 21:20Z, for the next
# day's events; the venue lists them about a day earlier and they trade from
# then. Their trades before discovery are older than the mark, so no run ever
# asked for them. Measured 10 Oct (tools/wxpredict/trade_history.py archive):
# for the 10 sampled events of 1-4 Oct the API serves 15,726 trades and we
# hold 13,113 rows; of the 2,506 never stored, 2,484 were traded before the
# market's markets.first_seen_at (e.g. Tokyo's 1 Oct event, listed 29 Sep
# 04:57Z, first seen 30 Sep 03:20Z). Of the rest, 22 are trades at 0.999 placed
# 35-52 h after first sight, after the market left `open_bands`, and 107 were
# merged by the dedupe key (separate transactions sharing condition, second,
# price, size and wallet): both named in R46, neither fixed here.
#
# So each market first seen in the last BACKFILL_HOURS is asked once more for
# every trade up to OVERLAP after it was first seen (the API's `end`, unix
# seconds), i.e. exactly the window the hourly cycle never read, and what is
# new is inserted through the same insert_trade_prints (duplicates turned
# away). It runs in pipeline_intraday (02:36, 08:36, 14:36, 20:36Z), not in
# the tick, so the tick's 60 s are untouched: 13 h covers every discovery run
# twice (a discovery at 03:20Z is 5.3 h before the 08:36Z run and 11.3 h
# before the 14:36Z run), so one failed run is made good by the next. The
# backfill runs read BACKFILL_FIRST_HOURS until one has finished whole ('ok'),
# to reach every market discovered before this existed.
#
# A RUN THAT STOPS SHORT IS RESUMED (Codex on #365). The markets a run did not
# reach, or failed on, are logged as `pending` and read FIRST by the next run,
# whether or not they are still inside its window, so a slow spell cannot
# leave a cohort's tail to age out unread. A pending market is carried for at
# most BACKFILL_LOOKBACK after it was first seen. A window that reached the
# offset cap is not retried: asking again gives the same answer.
#
# THE WORKFLOWS PAGE'S SWITCH IS OBEYED (Codex on #365): with P0.4 off or
# manual there, the backfill writes nothing, as the hourly run does.
#
# THE OFFSET CAP. The API refuses an offset over 10,000. A window whose answer
# reaches it is asked again in halves with `start` and `end`, down to
# BACKFILL_MAX_DEPTH halvings; one that still reaches it is logged, never
# called whole.
BACKFILL_JOB = "P0.4_trade_backfill"
BACKFILL_HOURS = 13
BACKFILL_FIRST_HOURS = 60
BACKFILL_BUDGET_S = 240.0
BACKFILL_LOOKBACK = dt.timedelta(days=7)    # the earliest a window starts: listed ~1-2 days before first sight
BACKFILL_MAX_DEPTH = 12


def recent_markets(since, pending=()):
    """{market_id: (first_seen_at, [bands])} for every market first seen at or
    after `since`, and for every market in `pending` wherever it was first seen."""
    rows = rest_all("bands", {
        "select": "band_id,token_yes,token_no,condition_id,"
                  "markets!inner(market_id,city_key,resolution_date,closed,first_seen_at)",
        "markets.first_seen_at": f"gte.{since.isoformat()}",
        "condition_id": "not.is.null"}, order="band_id.asc")
    if pending:
        rows += rest_all("bands", {
            "select": "band_id,token_yes,token_no,condition_id,"
                      "markets!inner(market_id,city_key,resolution_date,closed,first_seen_at)",
            "markets.market_id": "in.(" + ",".join(str(m) for m in pending) + ")",
            "condition_id": "not.is.null"}, order="band_id.asc")
    out, seen_bands = {}, set()
    for b in rows:
        if b["band_id"] in seen_bands:
            continue
        seen_bands.add(b["band_id"])
        m = b["markets"]
        seen = dt.datetime.fromisoformat(str(m["first_seen_at"]).replace("Z", "+00:00"))
        out.setdefault(m["market_id"], (seen, []))[1].append(b)
    return out


def fetch_window(ids, start, end, get=requests.get, depth=0, sleep=time.sleep):
    """Every trade on these markets with start <= timestamp <= end (unix s): (trades, whole).

    `whole` is False only when a window still reaches the offset cap after
    BACKFILL_MAX_DEPTH halvings. A 429 is waited out once a page, as
    fetch_batch does (Retry-After, else RATE_LIMIT_WAIT_S, never more than
    RATE_LIMIT_MAX_WAIT_S); a second one fails the market for this run."""
    out = []
    for page in range(MAX_PAGES + 1):              # offsets 0 to 10,000: all the API gives
        params = {"market": ",".join(ids), "start": int(start), "end": int(end),
                  "limit": PAGE, "offset": page * PAGE}
        r = get(API, params=params, headers=UA, timeout=20)
        if getattr(r, "status_code", 200) == 429:
            after = _retry_after(r)
            sleep(min(RATE_LIMIT_WAIT_S if after is None else max(0.0, after), RATE_LIMIT_MAX_WAIT_S))
            r = get(API, params=params, headers=UA, timeout=20)
        r.raise_for_status()
        got = r.json()
        if not isinstance(got, list):
            raise ValueError(f"trades API returned {type(got).__name__}, not a list")
        out.extend(got)
        if len(got) < PAGE:
            return out, True
    # every page full up to the cap: ask again in halves
    if depth >= BACKFILL_MAX_DEPTH or end - start < 2:
        return out, False
    mid = (int(start) + int(end)) // 2
    a, wa = fetch_window(ids, start, mid, get, depth + 1, sleep)
    b, wb = fetch_window(ids, mid + 1, end, get, depth + 1, sleep)
    return a + b, wa and wb


def previous_backfill():
    """(ever_ok, pending): whether a backfill has ever finished whole ('ok'),
    and the markets the last run that read anything left pending. Until one has
    finished whole, each run reads BACKFILL_FIRST_HOURS."""
    ok = rest("ingest_log", [("select", "logged_at"), ("job", f"eq.{BACKFILL_JOB}"),
                             ("status", "eq.ok"), ("order", "logged_at.desc"), ("limit", "1")])
    last = rest("ingest_log", [("select", "detail"), ("job", f"eq.{BACKFILL_JOB}"),
                               ("status", "neq.skipped"), ("order", "logged_at.desc"), ("limit", "1")])
    pending = ((last[0].get("detail") or {}).get("pending") or []) if last else []
    return bool(ok), [str(m) for m in pending]


def backfill(budget_s=BACKFILL_BUDGET_S, hours=None, now=None, get=requests.get):
    """Read each recently discovered market's trades from before it was discovered."""
    started = time.monotonic()
    now = now or dt.datetime.now(dt.timezone.utc)
    on, mode = switched_on()
    if not on:
        detail = {"summary": f"P0.4_trade_history is {mode} on the Workflows page - backfill not run",
                  "mode": mode}
        log_run(BACKFILL_JOB, "skipped", 0, detail)
        print(detail["summary"])
        return {**detail, "status": "skipped"}
    ever_ok, carried = previous_backfill()
    first_run = not ever_ok
    hours = hours if hours is not None else (BACKFILL_FIRST_HOURS if first_run else BACKFILL_HOURS)
    markets = recent_markets(now - dt.timedelta(hours=hours), carried)
    dropped = [m for m, (seen, _) in markets.items() if str(m) in carried and seen < now - BACKFILL_LOOKBACK]
    for m in dropped:
        del markets[m]
    ingested_at = now.isoformat()
    done = fetched = new = unmatched = 0
    errors, capped, left, failed = [], [], [], []
    slowest = 0.0
    # the markets the last run did not finish come first, then the oldest first sight
    order = sorted(markets.items(), key=lambda kv: (str(kv[0]) not in carried, kv[1][0], str(kv[0])))
    for market_id, (seen, bands) in order:
        if time.monotonic() - started + slowest > budget_s:
            left.append(market_id)
            continue
        t0 = time.monotonic()
        by_token = {str(b[k]): b for b in bands for k in ("token_yes", "token_no") if b.get(k)}
        ids = sorted({b["condition_id"] for b in bands})
        end = seen + OVERLAP
        try:
            raw, whole = fetch_window(ids, (seen - BACKFILL_LOOKBACK).timestamp(), end.timestamp(), get=get)
            if not whole:
                capped.append(market_id)
            rows, um = to_rows(raw, by_token, ingested_at)
            fetched += len(rows)
            unmatched += um
            new += insert_new(rows)
            done += 1
        except Exception as e:                       # noqa: BLE001 - counted and reported
            errors.append(f"{market_id}: {str(e)[:160]}")
            failed.append(market_id)
        slowest = max(slowest, time.monotonic() - t0)
    rollups = None
    if new:
        try:
            rollups = rpc("refresh_derived")
        except Exception as e:                       # noqa: BLE001
            errors.append(f"refresh_derived: {str(e)[:160]}")
    status = "error" if errors and not done else ("attention" if errors or capped or left else "ok")
    detail = {"hours": hours, "first_run": first_run, "markets": len(markets), "read": done,
              "carried": len(carried), "dropped": [str(m) for m in dropped],
              "left": len(left), "pending": [str(m) for m in failed + left],
              "capped": capped[:10], "fetched": fetched, "new": new,
              "unmatched": unmatched, "errors": errors[:5], "seconds": round(time.monotonic() - started, 1),
              "budget_s": budget_s, "rollups": rollups,
              "summary": f"{new} trades from before discovery, {done} of {len(markets)} markets first seen "
                         f"in the last {hours} h ({fetched} read)"}
    log_run(BACKFILL_JOB, status, new, detail)
    print(detail["summary"], "| errors:", len(errors))
    detail["status"] = status
    return detail


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=None)
    ap.add_argument("--backfill", action="store_true",
                    help="read the trades of recently discovered markets from before their discovery (R46)")
    ap.add_argument("--hours", type=float, default=None, help="--backfill: markets first seen this many hours back")
    a = ap.parse_args()
    if a.backfill:
        d = backfill(budget_s=a.budget if a.budget is not None else BACKFILL_BUDGET_S, hours=a.hours)
        sys.exit(1 if d["status"] == "error" else 0)
    d = main(budget_s=a.budget if a.budget is not None else BUDGET_S)
    # Only a run in which nothing worked fails the step; partial trouble is in
    # the log row, and the tick it rides with must not turn red for a page.
    sys.exit(1 if d["status"] == "error" else 0)
