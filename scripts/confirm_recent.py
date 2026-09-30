#!/usr/bin/env python3
"""Ladders confirmed by the venue the hour their day ends (plan v2.2 P4.7).

Venue confirmations were collected once a day, in pipeline_daily at about
05:00Z. A US local day ends at 04:00-07:00Z, so it was confirmed on the NEXT
day's run and its checkpoints banked a day after every C city's. Measured
26 Sep: 25 Sep had 32 of 32 C markets confirmed at 04:58-05:04Z and 0 of 11 F;
55 US checkpoints for 25 Sep sat unbanked.

This runs inside the hourly tick, with a budget cut from the tick's own
deadline, so it costs no billed minute of its own (the tick is billed one
minute whatever it does inside it). Then it banks every checkpoint whose
market is now confirmed.

30 SEP: THE QUEUE, NOT THE BAND SWEEP. It ran paper_settlement.cycle() over
bands, two venue calls per band, and reached 2-34 of ~150 candidate bands a
run (29 Sep 06:36Z - 30 Sep 08:36Z; 4 of 15 runs skipped with under 1 s). It
now runs confirm_queue.run(): one Gamma call per LADDER, the oldest unmet
need first, a backoff for ladders the venue has not closed. Eligibility is the
city's own clock (confirm_queue.local_day_end), so it runs every hour, not
only 06-17Z: a Tokyo day ends at 15:00Z, a London one at 23:00Z.
"""
import datetime as dt
import os
import sys
import time

JOB = "P4.7_confirm_recent"
HOURS_UTC = tuple(range(24))        # eligibility is local (confirm_queue.queue_order)
BUDGET_S = 12.0
RESERVE_S = 8.0                     # leaves the trade step its own few seconds
DAYS_BACK = 3
# The venue answers in about half a second a call (tested 30 Sep: 0.66 s for an
# 11-band Gamma request, 0.64 s for one CLOB market). A call that has not
# answered in 5 s is left to the next hour: paper_worker.public_json's 20 s
# wait, inside a 52-second tick, would bill a second minute.
REQUEST_TIMEOUT_S = 5


def quick_json(url, params):
    import requests
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def budget(deadline=None, now_epoch=None, budget_s=BUDGET_S):
    """Seconds this step may spend: BUDGET_S, or less when the tick's deadline
    (TICK_DEADLINE, epoch seconds) is nearer. Never negative."""
    if deadline in (None, ""):
        return budget_s
    left = float(deadline) - (time.time() if now_epoch is None else now_epoch) - RESERVE_S
    return max(0.0, min(budget_s, left))


def main(now=None, deadline=None):
    real = now is None
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.hour not in HOURS_UTC:
        return {}
    from common import rpc, log_run
    import confirm_queue

    seconds = budget(os.environ.get("TICK_DEADLINE") if deadline is None else deadline,
                     now_epoch=None if real else now.timestamp())
    if seconds < 3:
        detail = {"skipped": "no time left in the tick", "budget_s": round(seconds, 1)}
        log_run(JOB, "skipped", 0, detail)
        return detail
    sweep = confirm_queue.run(budget_seconds=seconds, days_back=DAYS_BACK, now=now,
                              get=quick_json, trigger="tick", log=False)
    banked = rpc("bank_checkpoint_outcomes") or 0
    detail = {"budget_s": round(seconds, 1), "banked_checkpoints": banked,
              "evidence_captured": sweep.get("evidence_captured"),
              "due": sweep.get("due"), "asked": sweep.get("asked"),
              "completed": sweep.get("completed"), "unreached": sweep.get("unreached"),
              "pending_after": sweep.get("pending_after"),
              "oldest_pending_hours": sweep.get("oldest_pending_hours"),
              "prep_s": sweep.get("prep_s"), "venue_calls": sweep.get("venue_calls"),
              "failed": sweep.get("failed"), "first_failure": sweep.get("first_failure"),
              "trigger": "tick"}
    status = "attention" if sweep.get("failed") else ("partial" if sweep.get("unreached") else "ok")
    log_run(JOB, status, (sweep.get("evidence_captured") or 0) + banked, detail)
    print(detail)
    return detail


if __name__ == "__main__":
    main()
    sys.exit(0)
