#!/usr/bin/env python3
"""Hassan's six frozen cities (15 Nov 2026). Research: nothing here prices or trades.

Hassan, 15 Nov: "Is this strategy profitable. And how much profit did we make to
date. We will pick 6 cities from now on and freeze and trade on them that means
each day contains the same cities. BUY YES on predicted bucket: Shanghai,
Helsinki, Amsterdam. BUY NO on predicted bucket: Houston, Austin, Sao Paulo."
He records the trades 23:00-02:00 Beirut (21:00Z-00:00Z).

THE RULE, as stated: each evening, YES on the model's evening-before pick in
Shanghai, Helsinki and Amsterdam, and NO on its evening-before pick in Houston,
Austin and Sao Paulo. One dollar a trade, held to settlement, the taker fee on
the price paid (strategy_study.cost); YES at the ask, NO at one less the bid.

THREE WAYS TO PRICE IT, on every date with a graded outcome:
  call     the evening-before call's frozen book (what the list of 12-14 Nov used)
  window   the live top of book at each hourly capture inside his window
           (live_capture 'books', at :36: 21:36Z, 22:36Z, 23:36Z), from 12 Oct on
  both are shown beside the same rule on every city: the base rate

THE CAVEAT THIS CANNOT REMOVE: the six cities were chosen on the record this
measures, so their own record is in-sample. A city's accuracy rank does not
carry into the next dates at this call (tools/city_sides_study.py, rank
correlation +0.06), so the base rate is the fair expectation unless these six
prove different.

    python tools/frozen6_study.py --out data/eval/frozen6_2026-11-15
    python tools/frozen6_study.py --ledger        # day by day from 16 Nov: one set of numbers
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import gzip
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import strategy_study as S  # noqa: E402

YES_CITIES = ("shanghai", "helsinki", "amsterdam")
NO_CITIES = ("houston", "austin", "sao_paulo")
CAPTURES = ("21:36Z", "22:36Z", "23:36Z")
FIRST_FORWARD_DATE = "2026-11-16"
CODEX_FROM, CODEX_TO = "2026-11-12", "2026-11-14"
csv.field_size_limit(sys.maxsize)


def when(stamp):
    return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def evening_calls(read=S.read):
    """{(city, date): {"top": band, "winner": band, "settled": dt}} from each first
    d1_eve call with a graded outcome."""
    first = {}
    for r in read("prediction_checkpoints"):
        k = (r["city_key"], r["target_date"])
        if r["checkpoint"] == "d1_eve" and r["probs"] and (
                k not in first or (r["decided_at"], r["checkpoint_id"]) < (first[k]["decided_at"], first[k]["checkpoint_id"])):
            first[k] = r
    outcome = {r["checkpoint_id"]: r for r in read("fact_checkpoint_outcome")}
    out = {}
    for k, r in first.items():
        f = outcome.get(r["checkpoint_id"])
        if not f or not f["settled_at"] or not f["winner_band_id"] or f["ladder_has_winner"] not in S.TRUE:
            continue
        probs = json.loads(r["probs"])
        out[k] = {"top": sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0], "winner": f["winner_band_id"],
                  "book": json.loads(r["market"]) if r["market"] else {}, "settled": when(f["settled_at"])}
    return out


def window_books(calls):
    """{(city, date): {capture: [bid, ask, bid size, ask size, ..]}} for each pick's bucket
    on the hourly books captures at 21:36Z-23:36Z of the evening before."""
    wanted = collections.defaultdict(dict)      # target date -> {band: city}
    for (city, date), c in calls.items():
        wanted[date][c["top"]] = city
    out = collections.defaultdict(dict)
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "mirror", "live_capture", "live_capture-*.csv.gz"))):
        with gzip.open(path, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                if r["kind"] != "books" or not r["captured_at"][11:13] in ("21", "22", "23") or r["captured_at"][14:16] != "36":
                    continue
                at = when(r["captured_at"])
                label = at.strftime("%H:%MZ")
                if label not in CAPTURES:
                    continue
                date = (at.date() + dt.timedelta(days=1)).isoformat()
                if date not in wanted:
                    continue
                tops = json.loads(r["payload"])["tops"]
                for band, city in wanted[date].items():
                    if tops.get(band):
                        out[(city, date)][label] = tops[band]
    return out


def trade(side, price, won, date):
    return {"date": date, "price": price, "cost": S.cost(price), "win": int(won)}


def call_trades(calls, cities_by_side):
    """Trades at the evening call's frozen book."""
    out = {"yes": [], "no": []}
    for (city, date), c in calls.items():
        q = c["book"].get(c["top"]) or {}
        if city in cities_by_side["yes"] and q.get("ask") is not None and 0 < q["ask"] < 1:
            out["yes"].append(dict(trade("YES", q["ask"], c["top"] == c["winner"], date), city=city))
        if city in cities_by_side["no"] and q.get("bid") is not None and 0 < q["bid"] < 1:
            out["no"].append(dict(trade("NO", 1 - q["bid"], c["top"] != c["winner"], date), city=city))
    return out


def window_trades(calls, books, cities_by_side, label):
    out = {"yes": [], "no": []}
    for (city, date), tops in books.items():
        top = tops.get(label)
        if not top:
            continue
        c = calls[(city, date)]
        bid, ask = top[0], top[1]
        if city in cities_by_side["yes"] and ask is not None and 0 < ask < 1:
            out["yes"].append(dict(trade("YES", ask, c["top"] == c["winner"], date), city=city, size=top[3]))
        if city in cities_by_side["no"] and bid is not None and 0 < bid < 1:
            out["no"].append(dict(trade("NO", 1 - bid, c["top"] != c["winner"], date), city=city, size=top[2]))
    return out


def summary(trades):
    """Return per dollar with its interval, and the profit of $100 a trade."""
    both = trades["yes"] + trades["no"]
    res = {"yes": S.result(trades["yes"]), "no": S.result(trades["no"]), "both": S.result(both)}
    res["profit_of_100_a_trade"] = round(sum(100 * (t["win"] / t["cost"] - 1.0) for t in both), 2) if both else 0.0
    res["staked_at_100_a_trade"] = 100 * len(both)
    return res


def by_city(trades):
    out = {}
    for side in ("yes", "no"):
        for city in sorted({t["city"] for t in trades[side]}):
            out[f"{city}:{side.upper()}"] = S.result([t for t in trades[side] if t["city"] == city])
    return out


def window(trades, first=None, last=None):
    return {side: [t for t in v if (first is None or t["date"] >= first) and (last is None or t["date"] <= last)]
            for side, v in trades.items()}


LOOKS = (23, 46)               # valid dates; 23 is what +20% a dollar needs for 80% power (tools/codex_trade_check.py)
OFFICIAL = "22:36Z"            # the middle of the three captures in his window (01:36 would be the last)


def ledger(calls, books, from_date=FIRST_FORWARD_DATE, label=OFFICIAL):
    """Day by day from `from_date`: the six trades at the capture's live book, $100 a
    trade, settled by the venue. A date is valid when all six are priced and settled."""
    six = {"yes": set(YES_CITIES), "no": set(NO_CITIES)}
    trades = window_trades(calls, books, six, label)
    by = collections.defaultdict(list)
    for t in trades["yes"] + trades["no"]:
        if t["date"] >= from_date:
            by[t["date"]].append(t)
    days, cum = [], 0.0
    for d in sorted(by):
        pnl = round(sum(100 * (t["win"] / t["cost"] - 1.0) for t in by[d]), 2)
        cum = round(cum + pnl, 2)
        days.append({"date": d, "trades": len(by[d]), "valid": len(by[d]) == 6, "profit_of_100_a_trade": pnl, "cumulative": cum,
                     "legs": {f"{t['city']}": {"price": round(t["price"], 3), "won": t["win"]} for t in by[d]}})
    valid = [d for d in days if d["valid"]]
    res = S.result([t for d in valid for t in by[d["date"]]]) if valid else {"n": 0}
    return {"from": from_date, "capture": label, "days": days, "valid_dates": len(valid), "result": res,
            "looks": {str(n): {"needs": n, "due": len(valid) >= n} for n in LOOKS}}


def verdict(res):
    """The pre-registered reading at a look: the 90% interval of the return per dollar."""
    lo, hi = res["ci90"]
    return "positive research evidence" if lo > 0 else ("failed" if hi < 0 else "insufficient evidence")


def codex_test_to_date():
    """The 18 trades of the Codex test (12-14 Nov): $100 a trade, at the listed
    prices where given, else the evening call's book, and at each capture's live book."""
    import codex_trade_check as X
    trades, bands = X.load_trades(), X.O._bands()
    rows_of = {(t["city"], t["date"]): X.calls_of(t["city"], t["date"]) for t in trades}
    ids = {next(r for r in rows if r["checkpoint"] == "d1_eve")["checkpoint_id"] for rows in rows_of.values()}
    outcomes = {r["checkpoint_id"]: r for r in S.read("fact_checkpoint_outcome") if r["checkpoint_id"] in ids}
    checked = []
    for t in trades:
        rows = rows_of[(t["city"], t["date"])]
        d1 = next(r for r in rows if r["checkpoint"] == "d1_eve")
        checked.append(X.check_trade(t, rows, outcomes.get(d1["checkpoint_id"]), bands))
    caps = X.book_captures(trades, checked)
    out = {"trades": len(trades), "listed_results": {"won": sum(t["won"] for t in trades), "provisional": sum(t["provisional"] for t in trades)}}

    def flat(prices):
        pnl = [100 * (t["won"] / S.cost(p) - 100 * 0 - 1.0) if p else None for t, p in zip(trades, prices)]
        got = [x for x in pnl if x is not None]
        return {"trades_priced": len(got), "profit_of_100_a_trade": round(sum(got), 2), "staked": 100 * len(got),
                "return_per_dollar": round(sum(got) / (100 * len(got)), 4)}
    out["at_the_evening_calls_book"] = flat([c["recorded_price"] for c in checked])
    out["at_the_listed_prices_where_given"] = flat([t["price"] if t["price"] is not None else c["recorded_price"]
                                                    for t, c in zip(trades, checked)])
    for label in CAPTURES:
        prices = []
        for t in trades:
            top = caps.get((t["city"], t["date"]), {}).get(label)
            prices.append(None if not top else (top[1] if t["side"] == "YES" else (None if top[0] is None else 1 - top[0])))
        out[f"at_the_live_book_{label}"] = flat(prices)
    return out


def study():
    calls = evening_calls()
    books = window_books(calls)
    six = {"yes": set(YES_CITIES), "no": set(NO_CITIES)}
    every = {"yes": {c for c, _d in calls}, "no": {c for c, _d in calls}}
    out = {"cities": {"yes": list(YES_CITIES), "no": list(NO_CITIES)}, "first_date": min(d for _c, d in calls),
           "last_date": max(d for _c, d in calls), "first_window_date": min(d for _c, d in books), "forward_from": FIRST_FORWARD_DATE}
    at_call, all_call = call_trades(calls, six), call_trades(calls, every)
    out["call"] = {"six": summary(at_call), "every_city": summary(all_call), "by_city": by_city(at_call),
                   "since_12_nov": summary(window(at_call, CODEX_FROM, CODEX_TO)),
                   "first_half": summary(window(at_call, None, "2026-10-24")), "second_half": summary(window(at_call, "2026-10-25"))}
    out["window"] = {}
    for label in CAPTURES:
        w, a = window_trades(calls, books, six, label), window_trades(calls, books, every, label)
        out["window"][label] = {"six": summary(w), "every_city": summary(a), "by_city": by_city(w),
                                "since_12_nov": summary(window(w, CODEX_FROM, CODEX_TO)),
                                "touch_smaller_than_a_4_dollar_order": sum(1 for t in w["yes"] + w["no"] if t.get("size") is not None
                                                                          and t["size"] < 4.0 / t["price"]),
                                "trades": len(w["yes"]) + len(w["no"])}
    out["codex_test_to_date"] = codex_test_to_date()
    out["forward"] = ledger(calls, books)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--ledger", action="store_true", help="the forward ledger only: day by day from 16 Nov")
    args = ap.parse_args(argv)
    if args.ledger:
        calls = evening_calls()
        out = ledger(calls, window_books(calls))
        for d in out["days"]:
            print(d["date"], "trades", d["trades"], "valid" if d["valid"] else "partial", f"{d['profit_of_100_a_trade']:+.2f}", f"cumulative {d['cumulative']:+.2f}")
        print(f"{out['valid_dates']} valid dates; first look at {LOOKS[0]}, last at {LOOKS[1]}")
        if out["valid_dates"] >= LOOKS[0]:
            print("look 1:", verdict(out["result"]))
        return out
    if not args.out:
        ap.error("--out is needed")
    out = study()
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "result.json"), "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
