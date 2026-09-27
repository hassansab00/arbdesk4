#!/usr/bin/env python3
"""US city-days confirmed and scored the same morning (plan v2.2 P4.7).

Venue confirmations were collected once a day, in pipeline_daily at about
05:00Z. A US local day ends at 04:00-07:00Z, so it was confirmed on the NEXT
day's run and its checkpoints banked a day after every C city's. Measured
26 Sep: 25 Sep had 32 of 32 C markets confirmed at 04:58-05:04Z and 0 of 11 F;
55 US checkpoints for 25 Sep sat unbanked.

This runs inside the hourly tick at the UTC hours after US days end, with a
budget cut from the tick's own deadline, so it costs no billed minute of its
own (the tick is billed one minute whatever it does inside it). It asks the
venue only about markets whose day ended in the last two days and which are
not proved yet (paper_settlement's own sweep with days_back=2; a proved
condition drops out before it costs anything), then banks every checkpoint
whose market is now confirmed.
"""
import datetime as dt
import os
import sys
import time

JOB = "P4.7_confirm_recent"
HOURS_UTC = tuple(range(6, 18))      # after US days end (04:00-07:00Z) and UMA rules
BUDGET_S = 12.0
RESERVE_S = 8.0                     # leaves the trade step its own few seconds
DAYS_BACK = 2
MAX_EVIDENCE = 60
# The venue answers in about half a second a call (paper_settlement: ~1 band
# a second for its two calls). paper_worker.public_json waits up to 20 s,
# which inside a 52-second tick would bill a second minute; here a call that
# has not answered in 5 s is left to the next hour.
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
    import paper_settlement
    import paper_worker
    paper_worker.public_json = quick_json     # cycle() imports it at call time

    seconds = budget(os.environ.get("TICK_DEADLINE") if deadline is None else deadline,
                     now_epoch=None if real else now.timestamp())
    if seconds < 3:
        detail = {"skipped": "no time left in the tick", "budget_s": round(seconds, 1)}
        log_run(JOB, "skipped", 0, detail)
        return detail
    sweep = paper_settlement.cycle(budget_seconds=seconds, days_back=DAYS_BACK,
                                   max_new_evidence=MAX_EVIDENCE, unconfirmed_only=True)
    banked = rpc("bank_checkpoint_outcomes") or 0
    detail = {"budget_s": round(seconds, 1), "banked_checkpoints": banked,
              "evidence_captured": sweep.get("evidence_captured"),
              "candidates": sweep.get("candidates"), "unreached": sweep.get("unreached"),
              "prep_s": sweep.get("prep_s"),
              "skips": sweep.get("skips"), "trigger": "tick"}
    log_run(JOB, "attention" if sweep.get("failed") else "ok",
            (sweep.get("evidence_captured") or 0) + banked, detail)
    print(detail)
    return detail


if __name__ == "__main__":
    main()
    sys.exit(0)
