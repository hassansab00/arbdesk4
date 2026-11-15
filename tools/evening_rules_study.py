#!/usr/bin/env python3
"""The evening-before call, where Hassan trades (15 Nov 2026). Research:
nothing here prices or trades.

Hassan, 15 Nov: "We're trading evening before / night before the trade date"
and "im testing the 3 yes 3 no strategy in codex, form nov 12-14 its up +98%
supposedly".

Every first `d1_eve` call with a graded outcome and its recorded book
(prediction_checkpoints.market). The model's pick is the served ladder's top
bucket. The market's favourite is the bucket with the highest price: the
midpoint where the book has both sides, else its ask, else its bid (ties to
the smallest bucket id). Trades are tools/strategy_study.py's: the touch, the
taker fee, one dollar a trade, held to settlement.

  agreement  how often the model's pick is the market's favourite, and how
             often each is right when they agree and when they do not
  rules      nine rules on that agreement, all written before any was scored
             and all scored together: on the dates to 31 Oct, on 1-14 Nov,
             and on both
  sizing     the trades the one rule with a positive estimate would need
             before its interval could clear zero
  costs      the market's favourite at the midpoint with no fee, against the
             same calls at the ask with the fee: what a taker gives up
  sides      the three-best YES / three-worst NO rule (tools/
             city_sides_study.py) on 12-14 Nov, and how far its return swings
             over three dates: every run of three consecutive traded dates

Intervals are 90%, resampling dates (strategy_study.result).

    python tools/evening_rules_study.py --out data/eval/evening_rules_2026-11-15
"""
import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import strategy_study as S  # noqa: E402
import city_sides_study as C  # noqa: E402
import postpeak_forward as P  # noqa: E402  (its spread by date and power constant)

CHECKPOINT = "d1_eve"
MIN_P = 0.5
REPLAY_FROM, REPLAY_TO = "2026-11-12", "2026-11-14"
RULES = {
    "agree_yes": ("They agree: YES on the shared pick", lambda r: r["yes_top"] if r["agree"] else None),
    "disagree_yes_favourite": ("They disagree: YES on the market's favourite", lambda r: None if r["agree"] else r["yes_fav"]),
    "disagree_no_model": ("They disagree: NO on the model's pick", lambda r: None if r["agree"] else r["no_top"]),
    "disagree_yes_model": ("They disagree: YES on the model's pick", lambda r: None if r["agree"] else r["yes_top"]),
    "disagree_no_favourite": ("They disagree: NO on the market's favourite", lambda r: None if r["agree"] else r["no_fav"]),
    "model_sure_yes": ("The model gives its pick 50% or more: YES", lambda r: r["yes_top"] if r["p"] >= MIN_P else None),
    "agree_and_sure_yes": ("They agree and the model gives 50% or more: YES",
                           lambda r: r["yes_top"] if r["agree"] and r["p"] >= MIN_P else None),
    "favourite_yes": ("YES on every market favourite", lambda r: r["yes_fav"]),
    "agree_no": ("They agree: NO on the shared pick", lambda r: r["no_top"] if r["agree"] else None),
}


def price_of(quote):
    """What the book says a bucket is worth: the midpoint, else the ask, else the bid."""
    ask, bid = (quote or {}).get("ask"), (quote or {}).get("bid")
    if ask is not None and bid is not None:
        return (ask + bid) / 2
    return ask if ask is not None else (bid if bid is not None else -1)


def call(city, date, probs, book, winner):
    """One evening-before call: both picks and the trades the book offered on them."""
    top, p = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    fav = sorted(probs, key=lambda b: (-price_of(book.get(b)), b))[0]

    def yes(b):
        ask = (book.get(b) or {}).get("ask")
        return None if ask is None or not 0 < ask < 1 else {
            "date": date, "city": city, "price": ask, "cost": S.cost(ask), "win": int(b == winner)}

    def no(b):
        bid = (book.get(b) or {}).get("bid")
        return None if bid is None or not 0 < bid < 1 else {
            "date": date, "city": city, "price": 1 - bid, "cost": S.cost(1 - bid), "win": int(b != winner)}
    q = book.get(fav) or {}
    return {"date": date, "city": city, "p": p, "agree": top == fav, "model_hit": int(top == winner),
            "favourite_hit": int(fav == winner), "yes_top": yes(top), "no_top": no(top), "yes_fav": yes(fav),
            "no_fav": no(fav), "favourite_ask": q.get("ask"), "favourite_bid": q.get("bid")}


def calls(read=S.read):
    first = {}
    for r in read("prediction_checkpoints"):
        k = (r["city_key"], r["target_date"])
        if r["checkpoint"] == CHECKPOINT and (k not in first or (r["decided_at"], r["checkpoint_id"])
                                              < (first[k]["decided_at"], first[k]["checkpoint_id"])):
            first[k] = r
    ids = {r["checkpoint_id"] for r in first.values()}
    outcome = {r["checkpoint_id"]: r for r in read("fact_checkpoint_outcome") if r["checkpoint_id"] in ids}
    out = []
    for (city, date), r in first.items():
        f = outcome.get(r["checkpoint_id"])
        if (not f or not f["settled_at"] or not f["winner_band_id"] or f["ladder_has_winner"] not in S.TRUE
                or not r["market"] or not r["probs"]):
            continue
        out.append(call(city, date, json.loads(r["probs"]), json.loads(r["market"]), f["winner_band_id"]))
    return out


def share(rows, key):
    return round(float(np.mean([r[key] for r in rows])), 4) if rows else None


def rules(rows):
    out = {}
    for name, (label, pick) in RULES.items():
        ts = [t for t in (pick(r) for r in rows) if t]
        out[name] = {"rule": label, "development": S.result([t for t in ts if t["date"] <= S.DEV_LAST]),
                     "holdout": S.result([t for t in ts if t["date"] >= S.HOLD_FIRST]), "both": S.result(ts)}
    return out


def costs(rows):
    """The favourite where the book has both sides: at the midpoint with no
    fee, and at the ask with the fee."""
    both = [r for r in rows if r["favourite_ask"] is not None and r["favourite_bid"] is not None
            and 0 < r["favourite_bid"] < r["favourite_ask"] < 1]
    mid = [{"date": r["date"], "win": r["favourite_hit"], "cost": (r["favourite_ask"] + r["favourite_bid"]) / 2} for r in both]
    ask = [{"date": r["date"], "win": r["favourite_hit"], "cost": S.cost(r["favourite_ask"])} for r in both]
    return {"at_the_midpoint_no_fee": S.result(mid), "at_the_ask_with_the_fee": S.result(ask),
            "mean_spread": round(float(np.mean([r["favourite_ask"] - r["favourite_bid"] for r in both])), 4),
            "mean_fee_a_share": round(float(np.mean([S.cost(r["favourite_ask"]) - r["favourite_ask"] for r in both])), 4)}


def sides(trades):
    """The three-and-three rule at this call: 12-14 Nov, and every run of three
    consecutive traded dates."""
    ps, out = C.picks(trades, CHECKPOINT), {}
    for metric in C.METRICS:
        legs = C.walk_legs(ps, metric)
        ts = legs["yes"] + legs["no"]
        by = {}
        for t in ts:
            by.setdefault(t["date"], []).append(t["win"] / t["cost"] - 1.0)
        ds = sorted(by)
        runs = [float(np.mean([z for d in ds[i:i + 3] for z in by[d]])) for i in range(len(ds) - 2)]
        replay = [t for t in ts if REPLAY_FROM <= t["date"] <= REPLAY_TO]
        out[metric] = {
            "replay_12_to_14_nov": {"trades": len(replay), "won": sum(t["win"] for t in replay),
                                    "return_per_dollar": round(float(np.mean([t["win"] / t["cost"] - 1 for t in replay])), 4)},
            "three_date_runs": {"runs": len(runs), "lowest": round(min(runs), 4), "median": round(float(np.median(runs)), 4),
                                "highest": round(max(runs), 4), "at_or_over_0.98": sum(x >= 0.98 for x in runs),
                                "above_0": sum(x > 0 for x in runs)},
            "swing_of_one_trade": round(float(np.std([z for d in ds for z in by[d]])), 4)}
    return out


def sizing(rows, name):
    """How many trades a rule needs for 80% power (one-sided 5%) at the return
    and the spread by date it has shown, and how long that is at its rate."""
    ts = [t for t in (RULES[name][1](r) for r in rows) if t]
    res, se = S.result(ts), P.spread(ts)
    days = (dt.date.fromisoformat(max(r["date"] for r in rows)) - dt.date.fromisoformat(min(r["date"] for r in rows))).days + 1
    need = len(ts) * (se * P.POWER_Z / res["return_per_dollar"]) ** 2
    return {"rule": name, "trades": len(ts), "calendar_days": days, "trades_a_calendar_day": round(len(ts) / days, 2),
            "standard_error_by_date": round(se, 4), "trades_for_80pc_power_at_this_return": int(round(need)),
            "calendar_days_at_this_rate": int(round(need / (len(ts) / days)))}


def study():
    rows = calls()
    agree, differ = [r for r in rows if r["agree"]], [r for r in rows if not r["agree"]]
    trades, _calls = S.candidates()
    return {"checkpoint": CHECKPOINT, "calls": len(rows), "first_date": min(r["date"] for r in rows),
            "last_date": max(r["date"] for r in rows), "development_to": S.DEV_LAST, "holdout_from": S.HOLD_FIRST,
            "agreement": {"share_agree": share(rows, "agree"), "model_right": share(rows, "model_hit"),
                          "favourite_right": share(rows, "favourite_hit"),
                          "agree": {"calls": len(agree), "pick_right": share(agree, "model_hit")},
                          "disagree": {"calls": len(differ), "model_right": share(differ, "model_hit"),
                                       "favourite_right": share(differ, "favourite_hit")}},
            "rules": rules(rows), "sizing": sizing(rows, "agree_and_sure_yes"), "costs": costs(rows),
            "three_and_three": sides(trades)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    out = study()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "result.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
