#!/usr/bin/env python3
"""Venue confirmations for ladders whose local day has ended, market by market
(plan v2.2 P4.7, finished 30 Sep: docs/handoff-2026-09-30/02_FIX_SPECS.md F1,
amended by the 30 Sep supplement).

WHY. /predictive's hit/miss record is fact_band_outcome, and a ladder is banked
only when every band carries a venue proof with exactly one winner. Measured
30 Sep 09:21Z (as anon): every US city's newest settled day was 28 Sep, and so
was Mexico City's and Panama City's. The venue itself was not late. Its own
closedTime / umaEndDate for US ladders came a median 1.2-1.4 h after the local
day ended (25-29 Sep, max 4.6 h; Atlanta 29 Sep closed 05:13-05:21Z on 30 Sep),
while resolution_verified_at came 22.8-24.1 h after it. The lag was ours.

WHY THE OLD STEP COULD NOT KEEP UP. paper_settlement.cycle() asks the venue
about one BAND at a time, two calls each, and inside the tick's 5-12 s it
reached 2-34 of ~150 candidate bands a run (29-30 Sep). Most of what it did
reach was a band whose market had not closed yet (`venue_has_no_record`), so
the same unanswerable questions were asked again next hour.

WHAT THIS DOES INSTEAD.
  * The unit is the LADDER. One Gamma request with a repeated condition_ids
    parameter returns every band of it (tested 30 Sep: 11 of 11 in 0.66 s; the
    comma-separated form returns nothing). A ladder the venue has not closed
    costs one call, not eleven.
  * CLOB is still asked per band, because verify() requires Gamma and the CLOB
    to agree on the winner; those calls run a few at a time.
  * ELIGIBILITY IS THE CITY'S OWN CLOCK: a market is a candidate once its
    local day has ended (the city's timezone, not UTC, and never inferred from
    C or F). Tokyo's day is eligible at 15:00Z, Los Angeles's at 07:00Z.
  * FAIR ORDER: the oldest unmet need first (earliest local day end), and a
    ladder found unresolved waits a growing interval before it is asked again
    (BACKOFF_MINUTES), so a market the venue is slow on cannot hold the budget
    while others wait. The attempts are kept in market_confirmation_attempts,
    so the order survives from one run to the next.
  * NOTHING IS INFERRED OR MANUFACTURED. Every proof still goes through
    paper_settlement.verify(); a void or split payout is left alone; a ladder
    stays pending until the venue settles it, and bank_bands still freezes a
    ladder whole or not at all.

WHAT IT RECORDS per market (the supplement's four timestamps): the local day
end, the venue's own latest closedTime (venue-supplied), the last poll that
saw the ladder not fully resolved and the first that saw it fully resolved
(which bracket when the venue made it available to us; the first successful
poll is an upper bound, not the venue's resolution time), and, through
v_outcome_pipeline, when the proof completed, when the ladder was banked, and
the backlog still pending.

  python scripts/confirm_queue.py [--budget-seconds 60] [--days-back 3]
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

JOB = "venue_confirm_queue"
GAMMA_URL = "https://gamma-api.polymarket.com/markets"
CLOB_URL = "https://clob.polymarket.com/markets/"
# Minutes a ladder waits after its k-th consecutive unresolved answer before it
# is asked again. The tick asks hourly and the venue closed US ladders a median
# 1.2-1.4 h after their day (max 4.6 h, 25-29 Sep): the first two waits fit
# inside one tick, so hourly asking continues while a ladder is fresh, and the
# pipeline's run in the same hour does not repeat what the tick just asked. A
# ladder still open after three hours is asked every other tick, then every
# third; a void one is asked at most every ~3 h for DAYS_BACK days.
BACKOFF_MINUTES = (0, 20, 40, 80, 160)
DAYS_BACK = 3
GAMMA_CHUNK = 25          # condition ids per Gamma request (a ladder is 11)
CLOB_WORKERS = 6
DEFAULT_TIMEOUT_S = 20
# A ladder is not started with less than this left: one takes ~1-2 s (one Gamma
# call, then its CLOB calls a few at a time), and the tick's shell backstop is
# 20 s against a 12 s budget.
MIN_LEFT_S = 2.0


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------
def local_day_end(resolution_date, timezone):
    """The UTC instant the city's local day `resolution_date` ends, or None."""
    if not timezone:
        return None
    day = dt.date.fromisoformat(str(resolution_date)[:10])
    nxt = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), ZoneInfo(timezone))
    return nxt.astimezone(dt.timezone.utc)


def backoff_minutes(streak):
    streak = max(0, int(streak or 0))
    return BACKOFF_MINUTES[min(streak, len(BACKOFF_MINUTES) - 1)]


def _ts(value):
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    s = str(value).strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    if s.endswith("+00"):
        s += ":00"
    try:
        out = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


def queue_order(markets, timezones, attempts, now):
    """(due, counts). A market is due when its local day has ended and it was
    never asked or its backoff has expired; due markets come oldest local day
    end first, then market id. counts says why the others are not due."""
    due, counts = [], {"not_ended": 0, "backing_off": 0, "no_timezone": 0}
    for m in markets:
        end = local_day_end(m["resolution_date"], timezones.get(m["city_key"]))
        if end is None:
            counts["no_timezone"] += 1
            continue
        if end > now:
            counts["not_ended"] += 1
            continue
        a = attempts.get(str(m["market_id"])) or {}
        last = _ts(a.get("last_asked_at"))
        wait = backoff_minutes(a.get("unresolved_streak"))
        if last is not None and wait and now - last < dt.timedelta(minutes=wait):
            counts["backing_off"] += 1
            continue
        due.append(dict(m, local_day_end=end))
    due.sort(key=lambda m: (m["local_day_end"], str(m["market_id"])))
    return due, counts


def gamma_params(conditions):
    """Gamma's /markets answers a REPEATED condition_ids parameter; a comma
    list returns []. closed=true because /markets hides closed markets by
    default (paper_settlement.cycle's note on the same parameter)."""
    return [("condition_ids", c) for c in conditions] + [("closed", "true")]


def resolved_at_venue(gamma):
    return bool(gamma) and gamma.get("closed") is True and gamma.get("umaResolutionStatus") == "resolved"


def ladder_outcome(n_asked, n_resolved, n_captured, n_failed):
    """What one attempt at a ladder found."""
    if n_asked == 0:
        return "proven"
    if n_failed and n_captured == 0:
        return "failed"
    if n_resolved == n_asked and n_captured == n_asked:
        return "resolved"
    if n_resolved:
        return "partial"
    return "not_closed"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def _get_json(url, params, timeout=DEFAULT_TIMEOUT_S):
    import requests
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _read_candidates(days_back, today):
    from common import rest_all
    since = (today - dt.timedelta(days=days_back)).isoformat()
    markets = rest_all("markets", [
        ("select", "market_id,city_key,resolution_date"),
        ("resolution_verified_at", "is.null"),
        ("resolution_date", f"gte.{since}"),
        ("resolution_date", f"lte.{today.isoformat()}"),
    ], order="resolution_date.asc,market_id.asc")
    tz = {c["city_key"]: c.get("timezone") for c in rest_all(
        "cities", [("select", "city_key,timezone")], order="city_key.asc")}
    ids = [str(m["market_id"]) for m in markets]
    attempts = {}
    for i in range(0, len(ids), 100):
        for a in rest_all("market_confirmation_attempts", [
                ("select", "market_id,last_asked_at,unresolved_streak,attempts"),
                ("market_id", "in.(" + ",".join(ids[i:i + 100]) + ")")], order="market_id.asc"):
            attempts[str(a["market_id"])] = a
    return markets, tz, attempts


def _read_bands(market_ids):
    from common import rest_all
    out = {}
    for i in range(0, len(market_ids), 100):
        for b in rest_all("bands", {
                "market_id": "in.(" + ",".join(market_ids[i:i + 100]) + ")",
                "select": "band_id,market_id,condition_id,token_yes,token_no"}, order="band_id.asc"):
            out.setdefault(str(b["market_id"]), []).append(b)
    return out


def run(budget_seconds=60.0, days_back=DAYS_BACK, now=None, get=None, trigger="intraday",
        clob_workers=CLOB_WORKERS, dry_run=False, log=True):
    """Ask the venue about due ladders until the budget is spent. Returns the
    detail it logs. Never raises for one bad ladder; a failure is counted.
    log=False when the caller logs its own row (the tick's P4.7 step), so each
    workflow's expected job is written by that workflow alone."""
    import paper_settlement as ps
    from common import rpc, upsert, log_run

    get = get or _get_json
    started = time.monotonic()
    now = now or dt.datetime.now(dt.timezone.utc)
    markets, tz, attempts = _read_candidates(days_back, now.date())
    due, counts = queue_order(markets, tz, attempts, now)
    bands_of = _read_bands([str(m["market_id"]) for m in due]) if due else {}
    conditions = {str(b["condition_id"]) for ladder in bands_of.values() for b in ladder
                  if b.get("condition_id")}
    proven = ps._proven(conditions) if conditions else set()
    prep_s = round(time.monotonic() - started, 1)

    rows, first_failure, completed_ids = [], None, set()
    totals = {"asked": 0, "completed": 0, "evidence": 0, "settled": 0, "failed": 0,
              "venue_calls": 0, "no_identity": 0, "void_or_split": 0}
    unreached = 0
    for index, m in enumerate(due):
        if time.monotonic() - started > budget_seconds - MIN_LEFT_S:
            unreached = len(due) - index
            break
        ladder = bands_of.get(str(m["market_id"]), [])
        todo = [b for b in ladder if str(b.get("condition_id")) not in proven]
        missing_identity = [b for b in todo if not (b.get("condition_id") and b.get("token_yes")
                                                     and b.get("token_no"))]
        todo = [b for b in todo if b not in missing_identity]
        totals["no_identity"] += len(missing_identity)
        resolved, captured, failed, closed_times = [], 0, 0, []
        error = None
        asked_at = dt.datetime.now(dt.timezone.utc)
        if todo:
            totals["asked"] += 1
            try:
                gamma_of = {}
                conds = [str(b["condition_id"]) for b in todo]
                for i in range(0, len(conds), GAMMA_CHUNK):
                    totals["venue_calls"] += 1
                    for g in get(GAMMA_URL, gamma_params(conds[i:i + GAMMA_CHUNK])) or []:
                        gamma_of[str(g.get("conditionId"))] = g
                for g in gamma_of.values():
                    t = _ts(g.get("closedTime"))
                    if t is not None:
                        closed_times.append(t)
                resolved = [b for b in todo if resolved_at_venue(gamma_of.get(str(b["condition_id"])))]
                if resolved:
                    with ThreadPoolExecutor(max(1, clob_workers)) as pool:
                        clobs = list(pool.map(
                            lambda b: _safe(get, CLOB_URL + str(b["condition_id"]), {}), resolved))
                    totals["venue_calls"] += len(resolved)
                    for b, (clob, err) in zip(resolved, clobs):
                        try:
                            if err is not None:
                                raise err
                            gamma = gamma_of[str(b["condition_id"])]
                            winner = ps.verify(b, gamma, clob)
                            if not winner:
                                totals["void_or_split"] += 1
                                continue
                            if dry_run:
                                captured += 1
                                continue
                            identity = hashlib.sha256(json.dumps(
                                {"gamma": gamma, "clob": clob}, sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()
                            upsert("paper_resolution_evidence", [{
                                "proof_id": identity, "condition_id": b["condition_id"],
                                "token_yes": b["token_yes"], "token_no": b["token_no"],
                                "winning_token": winner, "gamma": gamma, "clob": clob,
                                "source_urls": [GAMMA_URL + "?condition_ids=" + b["condition_id"]
                                                + "&closed=true", CLOB_URL + b["condition_id"]]}],
                                "proof_id")
                            proven.add(str(b["condition_id"]))
                            captured += 1
                            totals["settled"] += int(rpc("settle_paper_inventory", {
                                "p_band": b["band_id"], "p_proof": identity}) or 0)
                        except Exception as e:   # one band's failure is counted, not raised
                            failed += 1
                            if error is None:
                                error = f"{type(e).__name__}: {str(e)[:200]}"
            except Exception as e:
                failed += 1
                error = f"{type(e).__name__}: {str(e)[:200]}"
        totals["evidence"] += captured
        totals["failed"] += failed
        if error and first_failure is None:
            first_failure = {"market_id": str(m["market_id"]), "error": error}
        outcome = ladder_outcome(len(todo), len(resolved), captured, failed)
        if outcome in ("resolved", "proven") and not missing_identity:
            totals["completed"] += 1
            completed_ids.add(str(m["market_id"]))
        rows.append({
            "market_id": str(m["market_id"]), "city_key": m["city_key"],
            "resolution_date": str(m["resolution_date"])[:10],
            "local_day_end": m["local_day_end"].isoformat(),
            "asked_at": asked_at.isoformat(), "outcome": outcome,
            "bands_total": len(ladder),
            "bands_proven": sum(1 for b in ladder if str(b.get("condition_id")) in proven),
            "venue_closed_at": max(closed_times).isoformat() if closed_times else None,
            "error": error, "trigger": trigger,
        })

    if rows and not dry_run:
        rpc("record_market_confirmation_attempts", {"p_rows": rows})
    # THE BACKLOG, including ladders backing off: every candidate whose local
    # day has ended and whose proof this run did not complete.
    ended = [local_day_end(m["resolution_date"], tz.get(m["city_key"])) for m in markets
             if str(m["market_id"]) not in completed_ids]
    ended = [e for e in ended if e is not None and e <= now]
    oldest = min(ended, default=None)
    detail = {
        "trigger": trigger, "budget_s": round(float(budget_seconds), 1), "prep_s": prep_s,
        "candidates": len(markets), "due": len(due), "asked": totals["asked"],
        "completed": totals["completed"], "unreached": unreached,
        "evidence_captured": totals["evidence"], "positions_settled": totals["settled"],
        "failed": totals["failed"], "first_failure": first_failure,
        "venue_calls": totals["venue_calls"], "no_identity_bands": totals["no_identity"],
        "void_or_split_bands": totals["void_or_split"], "not_due": counts,
        "pending_after": len(ended),
        "oldest_pending_hours": (round((now - oldest).total_seconds() / 3600, 1)
                                 if oldest else None),
        "seconds": round(time.monotonic() - started, 1), "dry_run": bool(dry_run),
    }
    # A run that ran out of budget with ladders still due is PARTIAL, not ok:
    # a half-collected backlog must not read as a healthy one.
    status = "attention" if totals["failed"] else ("partial" if unreached else "ok")
    if log and not dry_run:
        log_run(JOB, status, totals["evidence"], detail)
    detail["status"] = status
    return detail


def _safe(get, url, params):
    try:
        return get(url, params), None
    except Exception as e:
        return None, e


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--budget-seconds", type=float, default=60.0)
    ap.add_argument("--days-back", type=int, default=DAYS_BACK)
    ap.add_argument("--trigger", default="intraday")
    ap.add_argument("--dry-run", action="store_true",
                    help="ask the venue and report; write no proof, attempt or log row")
    args = ap.parse_args(argv)
    detail = run(budget_seconds=args.budget_seconds, days_back=args.days_back,
                 trigger=args.trigger, dry_run=args.dry_run)
    print(json.dumps(detail, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
