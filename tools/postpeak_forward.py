#!/usr/bin/env python3
"""The post-peak rule in shadow (`postpeak-v1`, docs/POSTPEAK_PREREG.md): its
forward record, counted and then looked at twice. Research: nothing here
prices, places an order or writes to the database.

THE RULE, fixed on 15 Nov 2026 (the strategy study's section 6, unchanged):
at the first `postpeak_1h` call of a city and day, buy YES on the served
ladder's top bucket when the model gives it MIN_P or more and its best ask on
the book recorded with that call is above 0 and below MAX_ASK. One dollar a
trade at that ask plus the taker fee, held to settlement. One trade at most
per city and day.

WHAT IT READS: the nightly mirror in the repository (prediction_checkpoints,
fact_checkpoint_outcome). The first call is the one with the earliest
(decided_at, checkpoint_id), as everywhere else. No new schedule (Rule 7).

THE LOOKS. A look is a function of the record alone, so running it twice
tells nobody anything new. Look k is read on whole target dates: every trade
whose target date is no later than the first date by which LOOKS[k] graded
trades exist. A trade is graded when its call has a settled outcome with a
winner that is on the served ladder. A look waits while any trade on its
dates is still unsettled and younger than SETTLE_DAYS; an older one is
reported as ungraded, and the result is also given with every ungraded trade
counted as a loss.

    python tools/postpeak_forward.py --design data/eval/postpeak   # the study dates, for the pre-registration
    python tools/postpeak_forward.py --count                      # counts only: no win, no return
    python tools/postpeak_forward.py --look 1 --out data/eval/postpeak
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_study as S  # noqa: E402  (the study's cost, interval and mirror reader, unchanged)

RULE = "postpeak-v1"
CHECKPOINT = "postpeak_1h"
MIN_P = 0.90
MAX_ASK = 0.97
STUDY_FROM, STUDY_TO = "2026-10-17", "2026-11-14"   # wxrd3 serves this call from 17 Oct; the study read to 14 Nov
FIRST_FORWARD_DATE = "2026-11-16"   # 15 Nov is neither: some of its calls were made before this was written
LOOKS = {1: 110, 2: 220}            # graded trades; --design derives 220, and 110 is half of it
SETTLE_DAYS = 7
POWER_Z = 1.6449 + 0.8416           # one-sided 5%, 80% power


def decide(probs, book):
    """(status, top bucket, its probability, its ask) for one served call.
    status is "trade" or why not."""
    if not probs:
        return "no_ladder", None, None, None
    top, p = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    ask = ((book or {}).get(top) or {}).get("ask")
    if p < MIN_P:
        return "below_min_p", top, p, ask
    if not book:
        return "no_book", top, p, None
    if ask is None or ask <= 0:
        return "no_ask", top, p, None
    if ask >= MAX_ASK:
        return "ask_at_or_over_max", top, p, ask
    return "trade", top, p, ask


def grade(top, outcome):
    """"win", "loss", or why the trade cannot be graded."""
    if not outcome or not outcome.get("settled_at"):
        return "not_settled"
    if not outcome.get("winner_band_id"):
        return "no_winner"
    if outcome.get("ladder_has_winner") not in S.TRUE:
        return "winner_not_on_ladder"
    return "win" if top == outcome["winner_band_id"] else "loss"


def calls(first_date, last_date=None, read=S.read):
    """One row per first post-peak call on these target dates."""
    first = {}
    for r in read("prediction_checkpoints"):
        if r["checkpoint"] != CHECKPOINT or r["target_date"] < first_date or (last_date and r["target_date"] > last_date):
            continue
        k = (r["city_key"], r["target_date"])
        if k not in first or (r["decided_at"], r["checkpoint_id"]) < (first[k]["decided_at"], first[k]["checkpoint_id"]):
            first[k] = r
    ids = {r["checkpoint_id"] for r in first.values()}
    outcome = {r["checkpoint_id"]: r for r in read("fact_checkpoint_outcome") if r["checkpoint_id"] in ids}
    out = []
    for (city, date), r in sorted(first.items(), key=lambda kv: (kv[1]["decided_at"], kv[1]["checkpoint_id"])):
        probs = json.loads(r["probs"]) if r.get("probs") else None
        book = json.loads(r["market"]) if r.get("market") else None
        status, top, p, ask = decide(probs, book)
        row = {"city": city, "date": date, "checkpoint_id": r["checkpoint_id"], "decided_at": r["decided_at"],
               "engine_version": r.get("engine_version"), "served_by": r.get("forecast_model") or r.get("model_path"),
               "status": status, "top": top, "p": p, "price": ask}
        if ask is not None and 0 < ask < MAX_ASK:   # the rule's trades, and the control below MIN_P
            row["cost"] = S.cost(ask)
            row["grade"] = grade(top, outcome.get(r["checkpoint_id"]))
            row["win"] = {"win": 1, "loss": 0}.get(row["grade"])
        out.append(row)
    return out


def control(rows):
    """The same call and price limit where the model gave its top pick less
    than MIN_P: what the 0.90 adds."""
    return [r for r in rows if r["status"] == "below_min_p" and r.get("win") is not None]


def spread(trades):
    """The standard error of the return per dollar, resampling dates as S.result does."""
    by = collections.defaultdict(list)
    for t in trades:
        by[t["date"]].append(t["win"] / t["cost"] - 1.0)
    ds = sorted(by)
    rng = np.random.default_rng(S.SEED)
    st = [np.mean([z for i in rng.integers(0, len(ds), len(ds)) for z in by[ds[i]]]) for _ in range(S.BOOT)]
    return float(np.std(st))


def beside(graded):
    """The diagnostics the pre-registration names. None of them picks a rule."""
    by_date = collections.defaultdict(float)
    for t in graded:
        by_date[t["date"]] += t["win"] / t["cost"] - 1.0
    best = max(by_date, key=lambda d: (by_date[d], d)) if by_date else None
    return {"best_date": best,
            "without_the_best_date": S.result([t for t in graded if t["date"] != best]),
            "asks_of_0.50_or_more": S.result([t for t in graded if t["price"] >= 0.5]),
            "by_served_by": {k: S.result([t for t in graded if t["served_by"] == k])
                             for k in sorted({t["served_by"] or "" for t in graded})},
            "mean_model_probability": round(float(np.mean([t["p"] for t in graded])), 4) if graded else None,
            "cities": len({t["city"] for t in graded})}


def design(read=S.read):
    """The rule on the dates the study read (they chose it: not evidence), and
    the trade count that follows from their spread."""
    rows = calls(STUDY_FROM, STUDY_TO, read)
    trades = [r for r in rows if r["status"] == "trade" and r.get("win") is not None]
    res, se = S.result(trades), spread(trades)
    days = (dt.date.fromisoformat(STUDY_TO) - dt.date.fromisoformat(STUDY_FROM)).days + 1
    need = len(trades) * (se * POWER_Z / res["return_per_dollar"]) ** 2
    final = int(math.ceil(need / 10.0) * 10)
    return {"rule": RULE, "min_p": MIN_P, "max_ask": MAX_ASK, "from": STUDY_FROM, "to": STUDY_TO,
            "calendar_days": days, "first_calls": len(rows),
            "by_status": dict(collections.Counter(r["status"] for r in rows)),
            "ungraded_trades": sum(r["status"] == "trade" and r.get("win") is None for r in rows),
            "result": res, "standard_error_by_date": round(se, 4),
            "trades_a_calendar_day": round(len(trades) / days, 2),
            "trades_for_80pc_power_at_this_return": round(need, 1),
            "last_look_trades": final, "first_look_trades": final // 2,
            "calendar_days_to_the_last_look_at_this_rate": int(math.ceil(final / (len(trades) / days))),
            "losses": [{"city": t["city"], "date": t["date"], "ask": t["price"], "p": round(t["p"], 3)}
                       for t in trades if not t["win"]],
            "lowest_asks": sorted(t["price"] for t in trades)[:5],
            "control_below_min_p": S.result(control(rows)),
            **beside(trades)}


def cutoff(rows, need):
    """(cutoff date, why not) for a look needing `need` graded trades: the
    first target date by which that many exist, once nothing on or before it
    is still waiting to settle."""
    trades = [r for r in rows if r["status"] == "trade"]
    graded = sorted(r["date"] for r in trades if r["win"] is not None)
    if len(graded) < need:
        return None, f"{len(graded)} graded trades of {need}"
    day = graded[need - 1]
    last = dt.date.fromisoformat(max(r["date"] for r in rows))
    waiting = [r for r in trades if r["date"] <= day and r["grade"] == "not_settled"
               and (last - dt.date.fromisoformat(r["date"])).days <= SETTLE_DAYS]
    if waiting:
        return None, f"{len(waiting)} trades on or before {day} not settled yet"
    return day, None


def verdict(k, res, without_best):
    """The pre-registered reading of look k."""
    r, (lo, hi) = res["return_per_dollar"], res["ci90"]
    if k == 1:
        return "failed: stopped" if r <= 0 else "continues to the last look"
    if hi < 0 or r <= 0:
        return "failed"
    if lo > 0 and without_best.get("return_per_dollar", 0) > 0:
        return "positive research evidence"
    return "insufficient evidence"


def count(read=S.read):
    """Counts only. No win, no price and no return is computed or shown."""
    rows = calls(FIRST_FORWARD_DATE, None, read)
    trades = [r for r in rows if r["status"] == "trade"]
    out = {"rule": RULE, "counted_only": "no win, no return", "from": FIRST_FORWARD_DATE,
           "to": max((r["date"] for r in rows), default=None), "first_calls": len(rows),
           "by_status": dict(collections.Counter(r["status"] for r in rows)),
           "trades": len(trades), "trades_graded": sum(r["win"] is not None for r in trades),
           "trades_not_graded": dict(collections.Counter(r["grade"] for r in trades if r["win"] is None)),
           "dates_with_a_trade": len({r["date"] for r in trades}), "looks": {}}
    for k, need in LOOKS.items():
        day, why = cutoff(rows, need) if rows else (None, f"0 graded trades of {need}")
        out["looks"][str(k)] = {"needs": need, "due": day is not None, **({"dates_to": day} if day else {"waiting": why})}
    return out


def look(k, read=S.read):
    rows = calls(FIRST_FORWARD_DATE, None, read)
    day, why = cutoff(rows, LOOKS[k]) if rows else (None, "no forward call yet")
    if day is None:
        raise SystemExit(f"look {k} is not due: {why}")
    rows = [r for r in rows if r["date"] <= day]
    trades = [r for r in rows if r["status"] == "trade"]
    graded = [r for r in trades if r["win"] is not None]
    res, extra = S.result(graded), beside(graded)
    worst = S.result(graded + [dict(r, win=0) for r in trades if r["win"] is None])
    return {"rule": RULE, "look": k, "from": FIRST_FORWARD_DATE, "to": day,
            "by_status": dict(collections.Counter(r["status"] for r in rows)),
            "trades_not_graded": dict(collections.Counter(r["grade"] for r in trades if r["win"] is None)),
            "result": res, "verdict": verdict(k, res, extra["without_the_best_date"]),
            "with_ungraded_as_losses": worst,
            "control_below_min_p": S.result(control(rows)), **extra}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--design")
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--look", type=int, choices=sorted(LOOKS))
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.design:
        out, path = design(), os.path.join(args.design, "design_2026-11-15.json")
    elif args.look:
        if not args.out:
            ap.error("--look needs --out")
        out, path = look(args.look), os.path.join(args.out, f"look_{args.look}.json")
    else:
        out, path = count(), (os.path.join(args.out, "counts.json") if args.out else None)
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
