#!/usr/bin/env python3
"""Hassan's Codex test of the three-YES / three-NO strategy, 12-14 Nov 2026,
checked against the recorded book and the venue's outcomes (15 Nov 2026).
Research: nothing here prices or trades.

Hassan, 15 Nov: "im testing the 3 yes 3 no strategy in codex, form nov 12-14
its up +98% supposedly", with the trade list (data/eval/codex_test_2026-11-12/
trades.json, exactly as pasted).

PART `trades`: each listed trade against the first `d1_eve` call of its city
and day (prediction_checkpoints): is the bucket the model's top pick or the
market's favourite, does the listed price equal the recorded one (YES at the
ask, NO at one less the bid) and, where not, which later call's price it
equals; does the listed settled bucket equal the venue's winner
(fact_checkpoint_outcome); do the listed shares and P&L follow from the stake
and the price.

PART `replay`: the same trades at the recorded evening-before book with the
taker fee (tools/strategy_study.cost), under the staking the list implies: each
day six trades of one 7.5th of the equity, from $30 (a stake of 4.00, then
5.56, then 8.34 is what 30, 41.73 and 62.58 divided by 7.5 give). That
convention adds a whole day's profit before the next day's stakes; the next
evening-before orders are placed before that day settles (Codex on #515), so
`settlement_aware` replays the orders and the settlements in time order with
the cash in hand. Outcomes are the venue's; where the venue has not settled,
the list's provisional ones.
Scenarios: the list's own prices; the two trades whose bucket is not the
model's pick taken on the model's pick; Buenos Aires on 14 Nov as a loss; the
best trade removed; Shanghai on 14 Nov at its listed price; a flat stake.

PART `at_the_selection_time`: Hassan, 15 Nov: "Selection is night time beirut
time 23:30, frozen prediction forecasts for the next day" (21:30Z in November).
Each city's evening-before call is made at its own 18:00, so those of Buenos
Aires, Miami, Houston and Austin are made after 21:30Z. This replays the same
trades with each city's latest call made by then (its day-ahead call for those
four), on the listed buckets and on the model's pick at that call.

PART `form`: the rule he describes ("what historically was accurate and what
wasn't"): on each date the cities are ranked on their `d1_eve` picks that had
SETTLED by 21:30Z the evening before, by hit rate over the last WINDOW days
(0 = all history; at least MIN_PICKS picks); YES on the three best, NO on the
three worst, one dollar a trade at the recorded touch with the fee (the
study's trades, tools/city_sides_study.py). Every window is shown; window 10 is
the one the six cities fit best (and all history is shown on its own), with
ties broken at random 200 times and how far three consecutive dates can swing.

    python tools/codex_trade_check.py --out data/eval/codex_test_2026-11-12
"""
import argparse
import collections
import datetime as dt
import json
import os
import random
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import strategy_study as S  # noqa: E402
import city_sides_study as C  # noqa: E402
import own_w_forward as O  # noqa: E402  (its reader of the venue's buckets)

SELECTION_UTC = (21, 30)       # 23:30 Beirut, UTC+2 in November (Hassan, 15 Nov)
DATA = os.path.join(ROOT, "data", "eval", "codex_test_2026-11-12", "trades.json")
YEAR = 2026
START_EQUITY = 30.0
TRADES_A_DAY, SHARE_OF_EQUITY_A_DAY = 6, 0.8
STAKE_DIVISOR = TRADES_A_DAY / SHARE_OF_EQUITY_A_DAY       # 7.5
NEAR_ENOUGH = 0.035            # a listed price within this of the recorded one is that price
SAME_CALL = 0.015              # within this of a later call's price names that call
WINDOWS = (5, 7, 10, 14, 21, 28, 0)
FIT_WINDOW, MIN_PICKS, K, SEEDS = 10, 3, 3, 200


# ---------------------------------------------------------------------------
# the pasted list
# ---------------------------------------------------------------------------
def number(text):
    """-4.00 from '−$4.00', 0.63 from '63¢', None from 'Unknown'."""
    t = str(text).replace("−", "-").replace(",", "").strip()
    if t.lower() == "unknown":
        return None
    if t.endswith("¢"):
        return float(t[:-1]) / 100
    return float(t.replace("$", "").replace("+", ""))


def bucket_lo(text):
    """74.0 from '74–75°F', 10.0 from '10°C'."""
    return float(re.split(r"[–-]", re.sub(r"[°CF]", "", text).strip())[0])


def load_trades(path=DATA):
    out = []
    for t in json.load(open(path))["trades"]:
        month, day = re.match(r"([A-Za-z]+)(\d+)", t["date"]).groups()
        out.append({
            "date": dt.date(YEAR, {"Nov": 11}[month], int(day)).isoformat(),
            "city": t["city"].lower().replace(" ", "_"), "side": t["buy"], "bucket": t["predicted_bucket"],
            "lo": bucket_lo(t["predicted_bucket"]), "price": number(t["buy_price"]), "stake": number(t["planned_stake"]),
            "shares": number(t["shares"]), "settled": t["settled_bucket"], "settled_lo": bucket_lo(t["settled_bucket"]),
            "won": t["result"].startswith("Win"), "provisional": t["result"].endswith("*"), "pnl": number(t["pnl"])})
    return out


def pnl(side_price, stake, won, fee=True):
    """Profit on `stake` dollars at this price (the stake buys stake/cost shares)."""
    cost = S.cost(side_price) if fee else side_price
    return stake / cost - stake if won else -stake


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------
def mid(quote):
    ask, bid = (quote or {}).get("ask"), (quote or {}).get("bid")
    if ask is not None and bid is not None:
        return (ask + bid) / 2
    return ask if ask is not None else (bid if bid is not None else -1)


def band_with(bands, probs, lo):
    for b in probs:
        x = bands.get(b)
        if x and x["band_lo"] is not None and abs(x["band_lo"] - lo) < 1e-9 and not x["open_low"] and not x["open_high"]:
            return b
    return None


def price_of(side, quote):
    """What the book sold: YES at the ask, NO at one less the bid; None without it."""
    q = quote or {}
    if side == "YES":
        return q.get("ask")
    return None if q.get("bid") is None else 1 - q["bid"]


def calls_of(city, date, read=S.read):
    """Every prediction_checkpoints row of this city and target date, in time order."""
    rows = [r for r in read("prediction_checkpoints") if r["city_key"] == city and r["target_date"] == date]
    return sorted(rows, key=lambda r: (r["decided_at"], r["checkpoint_id"]))


def check_trade(t, rows, outcome, bands):
    """One listed trade against the record."""
    first = next((r for r in rows if r["checkpoint"] == "d1_eve"), None)
    if first is None:
        return {"city": t["city"], "date": t["date"], "error": "no d1_eve call"}
    probs, book = json.loads(first["probs"]), json.loads(first["market"])
    top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    fav = sorted(probs, key=lambda b: (-mid(book.get(b)), b))[0]
    mine = band_with(bands, probs, t["lo"])
    rec = price_of(t["side"], book.get(mine))
    out = {"city": t["city"], "date": t["date"], "side": t["side"], "bucket": t["bucket"],
           "model_p_on_bucket": round(probs[mine], 3), "is_model_top": mine == top, "is_market_favourite": mine == fav,
           "recorded_price": None if rec is None else round(rec, 4), "listed_price": t["price"]}
    if t["price"] is not None and rec is not None:
        out["price_difference"] = round(t["price"] - rec, 4)
        if abs(t["price"] - rec) <= NEAR_ENOUGH:
            out["price_is"] = "the evening-before book"
        else:
            later = next((r for r in rows if r["checkpoint"] != "d1_eve" and r["decided_at"] > first["decided_at"]
                          and band_with(bands, json.loads(r["probs"]), t["lo"])
                          and (lambda q: q is not None and abs(q - t["price"]) <= SAME_CALL)(
                              price_of(t["side"], json.loads(r["market"]).get(band_with(bands, json.loads(r["probs"]), t["lo"])))
                              if r["market"] else None)), None)
            out["price_is"] = (f"the {later['checkpoint']} call's book ({later['decided_at'][:16]}Z)" if later
                               else "no recorded call's book")
    win = (outcome or {}).get("winner_band_id")
    if win:
        out["venue_winner_is_listed_settled_bucket"] = win == band_with(bands, probs, t["settled_lo"])
    else:
        out["venue_winner_is_listed_settled_bucket"] = None
        out["unsettled_in_the_record"] = True
        last = rows[-1]
        lp = json.loads(last["probs"])
        out["last_call_top_bucket_p"] = [last["checkpoint"], round(max(lp.values()), 3),
                                         band_with(bands, lp, t["settled_lo"]) == sorted(lp.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]]
    if t["price"] is not None and t["shares"] is not None:
        shares = t["stake"] / t["price"]
        out["arithmetic"] = {"shares_difference": round(shares - t["shares"], 3),
                             "pnl_difference": None if t["pnl"] is None else round(pnl(t["price"], t["stake"], t["won"], fee=False) - t["pnl"], 3),
                             "fee_not_in_the_list": round(S.cost(t["price"]) * shares - t["price"] * shares, 4)}
    out["_top"], out["_mine"], out["_book"], out["_probs"], out["_win"] = top, mine, book, probs, win
    out["_decided_at"], out["_settled_at"] = first["decided_at"], (outcome or {}).get("settled_at") or None
    return out


# ---------------------------------------------------------------------------
# the replay
# ---------------------------------------------------------------------------
def equity_path(days, start=START_EQUITY):
    """days: [[(price, won), ...] per day]; each day every trade is equity / 7.5."""
    eq, path = start, []
    for trades in days:
        stake = eq / STAKE_DIVISOR
        day_pnl = sum(pnl(p, stake, w) for p, w in trades)
        eq += day_pnl
        path.append({"stake_a_trade": round(stake, 2), "pnl": round(day_pnl, 2), "equity": round(eq, 2)})
    return path


def scenario(name, days):
    path = equity_path(days)
    staked = sum(p["stake_a_trade"] * len(d) for p, d in zip(path, days))
    total = sum(p["pnl"] for p in path)
    return {"scenario": name, "days": path, "equity": path[-1]["equity"],
            "return_on_start": round(path[-1]["equity"] / START_EQUITY - 1, 4),
            "pnl_per_dollar_staked": round(total / staked, 4)}


def when(stamp):
    return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def settlement_aware(entries, start=START_EQUITY):
    """The same trades as an account that only has what has settled (Codex on
    #515: the list's staking adds a whole day's profit before the next day's
    orders, which the next evening-before orders, placed before the day settles,
    could not use).

    entries: [(ordered_at, settled_at, price, won)]. Orders go in time order; a
    position settled at or before an order's time has paid out by then. An
    order is equity-realised / 7.5, and no more than the cash in hand: an
    order the cash cannot cover is cut to the cash (and counted)."""
    cash, realised, open_, orders, cut, stakes = start, start, [], sorted(entries, key=lambda e: e[0]), 0, []
    for at, settle_at, price, won in orders:
        for pos in [o for o in open_ if o[0] <= at]:
            open_.remove(pos)
            cash += pos[2]
            realised += pos[2] - pos[1]
        want = realised / STAKE_DIVISOR
        stake = min(want, max(cash, 0.0))
        cut += int(stake < want - 1e-9)
        cash -= stake
        stakes.append(round(stake, 2))
        open_.append((settle_at, stake, stake / S.cost(price) if won else 0.0))
    for settle_at, stake, payout in open_:
        cash += payout
    return {"equity": round(cash, 2), "return_on_start": round(cash / start - 1, 4), "orders": len(orders),
            "orders_cut_to_the_cash_in_hand": cut, "stakes": stakes, "smallest_stake": min(stakes), "largest_stake": max(stakes)}


def replay(checked, trades):
    by_day = collections.OrderedDict()
    for t, c in zip(trades, checked):
        by_day.setdefault(t["date"], []).append((t, c))

    def outcome(t, c):
        """True/False from the venue where it has settled, else the list's."""
        if c["_win"]:
            return (c["_mine"] == c["_win"]) if t["side"] == "YES" else (c["_mine"] != c["_win"])
        return t["won"]

    def base(use_listed=False, model_top=False, ba_loss=False, drop=None, shanghai_listed=False):
        days = []
        for d, rows in by_day.items():
            day = []
            for t, c in rows:
                if drop == (t["city"], d):
                    continue
                bucket, won = c["_mine"], outcome(t, c)
                price = price_of(t["side"], c["_book"].get(bucket))
                if model_top and not c["is_model_top"]:
                    bucket = c["_top"]
                    price = price_of(t["side"], c["_book"].get(bucket))
                    won = ((bucket == c["_win"]) if t["side"] == "YES" else (bucket != c["_win"])) if c["_win"] else t["won"]
                if use_listed and t["price"] is not None:
                    price = t["price"]
                if shanghai_listed and t["city"] == "shanghai" and d == "2026-11-14":
                    price = t["price"]
                if ba_loss and t["city"] == "buenos_aires" and d == "2026-11-14":
                    won = False
                day.append((price, won))
            days.append(day)
        return days
    pnls = []
    for t, c in zip(trades, checked):
        stake_day = None
        p = price_of(t["side"], c["_book"].get(c["_mine"]))
        pnls.append(((t["city"], t["date"]), p, outcome(t, c)))
    flat = [pnl(p, 4.0, w) / 4.0 for _k, p, w in pnls]
    # the best trade at the listed staking, by profit on the recorded book
    path = equity_path(base())
    best, best_pnl = None, -1e9
    for (key, p, w), t in zip(pnls, trades):
        day_i = list(by_day).index(t["date"])
        v = pnl(p, path[day_i]["stake_a_trade"], w)
        if v > best_pnl:
            best, best_pnl = key, v
    scenarios = [
        scenario("the recorded evening-before book, with the fee", base()),
        scenario("the same, at the list's own prices where it gives one (no fee in the list; fee added here)", base(use_listed=True)),
        scenario("the two trades whose bucket is not the model's pick, taken on the model's pick", base(model_top=True)),
        scenario("Buenos Aires on 14 Nov as a loss (the venue has not settled it)", base(ba_loss=True)),
        scenario("Shanghai on 14 Nov at its listed 80 cents", base(shanghai_listed=True)),
        scenario("without the best trade", base(drop=best)),
    ]
    total_pnl = sum(pnl(p, path[list(by_day).index(t["date"])]["stake_a_trade"], w) for (k, p, w), t in zip(pnls, trades))
    unsettled = [t for t, c in zip(trades, checked) if c["_settled_at"] is None]
    last = max(when(c["_settled_at"]) for c in checked if c["_settled_at"])

    def entries(ba_loss=False):
        out = []
        for t, c in zip(trades, checked):
            won = outcome(t, c)
            if ba_loss and t["city"] == "buenos_aires" and t["date"] == "2026-11-14":
                won = False
            # a position the venue has not settled pays after every order in the list
            out.append((when(c["_decided_at"]), when(c["_settled_at"]) if c["_settled_at"] else last + dt.timedelta(days=1),
                        price_of(t["side"], c["_book"].get(c["_mine"])), won))
        return out
    aware = {"as_listed": settlement_aware(entries()), "buenos_aires_on_14_nov_as_a_loss": settlement_aware(entries(True)),
             "positions_not_settled_in_the_record": len(unsettled)}
    return {"stake_rule": f"each day every trade is equity / {STAKE_DIVISOR:g}, from ${START_EQUITY:g}",
            "settlement_aware": aware,
            "best_trade": {"trade": list(best), "pnl": round(best_pnl, 2), "share_of_all_profit": round(best_pnl / total_pnl, 3)},
            "flat_stake_pnl_per_dollar": round(float(np.mean(flat)), 4), "trades": len(flat),
            "scenarios": scenarios}


def selection_time(date):
    """23:30 Beirut on the evening before the target date, in UTC (Hassan, 15 Nov:
    "Selection is night time beirut time 23:30"; Lebanon is UTC+2 in November)."""
    d = dt.date.fromisoformat(date) - dt.timedelta(days=1)
    return dt.datetime(d.year, d.month, d.day, SELECTION_UTC[0], SELECTION_UTC[1], tzinfo=dt.timezone.utc)


def at_selection(trades, checked, rows_of, bands):
    """The same trades with only what existed at the selection time: each city's
    latest call made no later than 23:30 Beirut on the evening before. The
    evening-before call of a city is made at its own 18:00, which for Buenos
    Aires, Miami, Houston and Austin is after that. Two replays on those calls'
    books: the listed bucket, and the model's pick at that call."""
    out = []
    for t, c in zip(trades, checked):
        sel = selection_time(t["date"])
        rows = [r for r in rows_of[(t["city"], t["date"])] if when(r["decided_at"]) <= sel]
        row = {"city": t["city"], "date": t["date"], "side": t["side"], "listed_call_is_after_the_selection": when(c["_decided_at"]) > sel}
        if not rows:
            out.append(dict(row, available_call=None))
            continue
        r = rows[-1]
        probs, book = json.loads(r["probs"]), json.loads(r["market"]) if r["market"] else {}
        top = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        listed = band_with(bands, probs, t["lo"])

        def wins(b):
            """Does this bucket win: the venue's winner, else the list's settled bucket."""
            if c["_win"]:
                return b == c["_win"]
            x = bands.get(b)
            return bool(x and x["band_lo"] is not None and abs(x["band_lo"] - t["settled_lo"]) < 1e-9)
        row.update({"available_call": r["checkpoint"], "available_call_at": r["decided_at"][:16] + "Z",
                    "listed_bucket_is_the_models_pick": listed == top,
                    "_listed": (price_of(t["side"], book.get(listed)), (wins(listed) if t["side"] == "YES" else not wins(listed))),
                    "_top": (price_of(t["side"], book.get(top)), (wins(top) if t["side"] == "YES" else not wins(top))),
                    "_decided": sel, "_settled": when(c["_settled_at"]) if c["_settled_at"] else None})
        out.append(row)
    return out


def selection_replays(rows):
    """Day-end staking and the settlement-aware account on each bucket choice."""
    by = {}
    last = max(r["_settled"] for r in rows if r.get("_settled"))
    for key, label in (("_listed", "the listed buckets"), ("_top", "the model's pick at that call")):
        usable = [r for r in rows if r.get("available_call") and r[key][0] is not None]
        days = collections.defaultdict(list)
        for r in sorted(usable, key=lambda r: r["date"]):
            days[r["date"]].append((r[key][0], r[key][1]))
        day = scenario(label, [days[d] for d in sorted(days)])
        entries = [(r["_decided"], r["_settled"] or last + dt.timedelta(days=1), r[key][0], r[key][1]) for r in usable]
        by[key[1:]] = {"trades_priced": len(usable), "trades": len(rows), "day_end_staking": day,
                       "settlement_aware": settlement_aware(entries),
                       "won": sum(r[key][1] for r in usable), "wins_by_trade": {f"{r['city']}:{r['date']}": r[key][1] for r in usable}}
    return by


def listed_equity(trades):
    """The list's own P&L as written, day by day, and the equity it implies from $30."""
    eq, days = START_EQUITY, []
    for d in sorted({t["date"] for t in trades}):
        rows = [t for t in trades if t["date"] == d]
        known = [t["pnl"] for t in rows if t["pnl"] is not None]
        stake = eq / STAKE_DIVISOR
        days.append({"date": d, "listed_stake": rows[0]["stake"], "stake_from_equity": round(stake, 2),
                     "trades": len(rows), "trades_with_a_listed_pnl": len(known), "listed_pnl": round(sum(known), 2)})
        if len(known) == len(rows):
            eq += sum(known)
    return days


# ---------------------------------------------------------------------------
# the ranking the six cities fit
# ---------------------------------------------------------------------------
def city_tables(ps, dates, window):
    """{date: {city: hits[]}}: the picks settled by the selection time (23:30
    Beirut on the evening before, Hassan's own; 21:30Z), within `window` days of
    the date (0 = all history)."""
    out = {}
    for d in dates:
        cut = selection_time(d)
        hist = collections.defaultdict(list)
        for p in ps:
            if p["date"] >= d or p["settled"] > cut:
                continue
            if window and (dt.date.fromisoformat(d) - dt.date.fromisoformat(p["date"])).days > window:
                continue
            hist[p["city"]].append(p["hit"])
        out[d] = hist
    return out


def ranked(hist, seed=None):
    """Cities with MIN_PICKS picks, best hit rate first; ties by more picks then name,
    or at random when a seed is given."""
    seen = [c for c, v in hist.items() if len(v) >= MIN_PICKS]
    if seed is None:
        return sorted(seen, key=lambda c: (-np.mean(hist[c]), -len(hist[c]), c))
    rng = random.Random(f"{seed}")
    return sorted(seen, key=lambda c: (-np.mean(hist[c]), rng.random()))


def form_walk(ps, tables, seed=None):
    yes, no = [], []
    for d, hist in tables.items():
        order = ranked(hist, None if seed is None else f"{seed}:{d}")
        if len(order) < 2 * K:
            continue
        best, worst = set(order[:K]), set(order[-K:])
        for p in ps:
            if p["date"] != d:
                continue
            if p["city"] in best and "yes" in p:
                yes.append(p["yes"])
            if p["city"] in worst and "no" in p:
                no.append(p["no"])
    return yes, no


def three_date_runs(trades):
    by = collections.defaultdict(list)
    for t in trades:
        by[t["date"]].append(t["win"] / t["cost"] - 1.0)
    ds = sorted(by)
    runs = []
    for i in range(len(ds) - 2):
        z = [x for d in ds[i:i + 3] for x in by[d]]
        runs.append((float(np.mean(z)), ds[i], ds[i + 2], len(z)))
    return runs


def dates_needed(trades, effects=(0.10, 0.20, 0.35)):
    """Dates a forward test needs for 80% power (one-sided 5%) to put the rule's
    return per dollar above zero, at each true return, given the spread of a
    date's mean return in the record."""
    by = collections.defaultdict(list)
    for t in trades:
        by[t["date"]].append(t["win"] / t["cost"] - 1.0)
    sd = float(np.std([np.mean(v) for v in by.values()], ddof=1))
    z = 1.6449 + 0.8416
    return {"sd_of_a_dates_mean_return": round(sd, 4), "dates": len(by),
            "dates_for_80pc_power": {f"true_return_{e:+.2f}": int(np.ceil((z * sd / e) ** 2)) for e in effects}}


def fit_of_ranking(ps, tables, codex_cities):
    """How the six cities of each day sit in the ten-day table: their positions from the best (YES) and from the worst (NO)."""
    out = {}
    for d, (yes, no) in codex_cities.items():
        order = ranked(tables[d])
        out[d] = {"cities_ranked": len(order),
                  "yes_positions_from_best": sorted(order.index(c) + 1 for c in yes if c in order),
                  "no_positions_from_worst": sorted(len(order) - order.index(c) for c in no if c in order),
                  "yes_in_default_top3": len(set(order[:K]) & yes), "no_in_default_bottom3": len(set(order[-K:]) & no)}
    return out


def form(trades_by_listed):
    all_trades, _calls = S.candidates()
    ps = C.picks(all_trades, "d1_eve")
    dates = sorted({p["date"] for p in ps})
    windows = {}
    for w in WINDOWS:
        yes, no = form_walk(ps, city_tables(ps, dates, w))
        windows["all" if w == 0 else str(w)] = {"yes": S.result(yes), "no": S.result(no), "both": S.result(yes + no)}
    tables = city_tables(ps, dates, FIT_WINDOW)
    tables_all = city_tables(ps, dates, 0)
    sims = []
    for s in range(SEEDS):
        y, n = form_walk(ps, tables, seed=s)
        sims.append((S.result(y)["return_per_dollar"], S.result(n)["return_per_dollar"], S.result(y + n)["return_per_dollar"]))
    sims = np.array(sims)
    y, n = form_walk(ps, tables)
    runs = three_date_runs(y + n)
    order = sorted(runs, key=lambda r: -r[0])
    nov = next(i for i, r in enumerate(order) if r[1] == "2026-11-12")
    codex = {d: ({t["city"] for t in rows if t["side"] == "YES"}, {t["city"] for t in rows if t["side"] == "NO"})
             for d, rows in trades_by_listed.items()}
    return {"window_used": FIT_WINDOW, "min_picks": MIN_PICKS, "k": K, "dates_traded": len({t["date"] for t in y + n}),
            "first_date": min(t["date"] for t in y + n), "last_date": max(t["date"] for t in y + n),
            "windows": windows,
            "random_tie_breaks": {"seeds": SEEDS, "both_mean": round(float(sims[:, 2].mean()), 4),
                                  "both_5th_pct": round(float(np.percentile(sims[:, 2], 5)), 4),
                                  "both_95th_pct": round(float(np.percentile(sims[:, 2], 95)), 4),
                                  "share_of_seeds_above_0": round(float((sims[:, 2] > 0).mean()), 3),
                                  "yes_mean": round(float(sims[:, 0].mean()), 4), "no_mean": round(float(sims[:, 1].mean()), 4)},
            "three_date_runs": {"runs": len(runs), "median": round(float(np.median([r[0] for r in runs])), 4),
                                "share_above_0": round(float(np.mean([r[0] > 0 for r in runs])), 3),
                                "best": {"return_per_dollar": round(order[0][0], 4), "from": order[0][1], "to": order[0][2],
                                         "trades": order[0][3]},
                                "nov_12_to_14": {"return_per_dollar": round(order[nov][0], 4), "rank_among_runs": nov + 1,
                                                 "trades": order[nov][3]}},
            "sizing": dates_needed(y + n),
            "fit_of_the_six_cities": fit_of_ranking(ps, tables, codex),
            "fit_of_the_six_cities_all_history": fit_of_ranking(ps, tables_all, codex)}


def study():
    trades = load_trades()
    bands = O._bands()
    first_ids, rows_of = {}, {}
    for t in trades:
        rows_of[(t["city"], t["date"])] = calls_of(t["city"], t["date"])
    ids = {next(r for r in rows if r["checkpoint"] == "d1_eve")["checkpoint_id"] for rows in rows_of.values()}
    outcomes = {r["checkpoint_id"]: r for r in S.read("fact_checkpoint_outcome") if r["checkpoint_id"] in ids}
    checked = []
    for t in trades:
        rows = rows_of[(t["city"], t["date"])]
        d1 = next(r for r in rows if r["checkpoint"] == "d1_eve")
        checked.append(check_trade(t, rows, outcomes.get(d1["checkpoint_id"]), bands))
    by_day = collections.defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t)
    public = [{k: v for k, v in c.items() if not k.startswith("_")} for c in checked]
    sel_rows = at_selection(trades, checked, rows_of, bands)
    selection = {"selection_time": "23:30 Beirut = 21:30Z on the evening before the target date",
                 "trades_whose_listed_call_is_after_it": sum(r["listed_call_is_after_the_selection"] for r in sel_rows),
                 "cities_with_a_later_call": sorted({r["city"] for r in sel_rows if r["listed_call_is_after_the_selection"]}),
                 "available_calls": dict(collections.Counter(r.get("available_call") for r in sel_rows)),
                 "listed_bucket_is_the_models_pick_at_that_call": sum(bool(r.get("listed_bucket_is_the_models_pick")) for r in sel_rows),
                 "replays": selection_replays(sel_rows),
                 "trades": [{k: v for k, v in r.items() if not k.startswith("_")} for r in sel_rows]}
    priced = [c for c in public if "arithmetic" in c]
    return {
        "trades": len(trades), "dates": sorted(by_day),
        "listed": {"days": listed_equity(trades), "provisional_results": sum(t["provisional"] for t in trades),
                   "trades_without_a_listed_price": sum(t["price"] is None for t in trades)},
        "checks": public,
        "summary": {"bucket_is_the_models_pick": sum(c["is_model_top"] for c in public),
                    "bucket_is_the_markets_favourite": sum(c["is_market_favourite"] for c in public),
                    "price_is_the_evening_book": sum(c.get("price_is") == "the evening-before book" for c in public),
                    "priced_trades": sum(t["price"] is not None for t in trades),
                    "settled_bucket_is_the_venues": sum(c["venue_winner_is_listed_settled_bucket"] is True for c in public),
                    "settled_bucket_differs_from_the_venues": sum(c["venue_winner_is_listed_settled_bucket"] is False for c in public),
                    "unsettled_in_the_record": sum(bool(c.get("unsettled_in_the_record")) for c in public),
                    "shares_off_by_more_than_a_cent": sum(abs(c["arithmetic"]["shares_difference"]) > 0.011 for c in priced),
                    "pnl_off_by_more_than_a_cent": sum(c["arithmetic"]["pnl_difference"] is not None
                                                       and abs(c["arithmetic"]["pnl_difference"]) > 0.011 for c in priced),
                    "fees_not_in_the_list": round(sum(c["arithmetic"]["fee_not_in_the_list"] for c in priced), 2)},
        "replay": replay(checked, trades),
        "at_the_selection_time": selection,
        "form": form({d: rows for d, rows in by_day.items()}),
    }


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
