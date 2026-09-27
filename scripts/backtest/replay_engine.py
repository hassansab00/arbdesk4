"""Replay the consolidated strategies through the one engine (plan v2 P5.12 part 2).

Every strategy that decides through the engine (strategies.engine_views) gets
its own $1,000 shadow ledger and is walked through the venue-confirmed
city-days of the window, at the tick's checkpoints, in time order, with only
what was known at each decision:

  ladder   the engine's newest complete pricing of the market at or before the
           decision (band_probabilities, repo mirror), no older than
           LADDER_MAX_AGE_S - the same probabilities the live board showed
  book     each band's newest snapshot at or before the decision, no older
           than BOOK_MAX_AGE_S (archived intraday rows and the database's own)
  ledger   the strategy's cash and holdings from its own earlier decisions, and
           the day's realised P&L (UTC day) for the daily-loss rail

The engine decides (decision_engine.decide, the code the tick will call); an
IOC leg fills against the snapshot's depth tiers (FILL below); a market
settles at the end of its local day on the venue's winner.

S10 is not here: its view is the remaining-day ladder, which the P7.3 replay
builds, and it needs station readings as of each decision. S13 decides
nothing (research only). The station floor is not given to the strategies
(floor_c None): the engine's own same-day ladder already carries the floor.

    I=data/replay/inputs_engine_2026-09-27
    python scripts/backtest/replay_engine.py data/replay/inputs_2026-09-26/replay_inputs.json.gz \
        $I/books.json.gz data/mirror/band_probabilities --from 2026-09-12 --to 2026-09-25 \
        --out-rows $I/../engine_replay_2026-09-27.csv.gz --out-report docs/ENGINE_REPLAY_2026-09-27.md

FILL. A leg of U dollars at best ask a walks the snapshot's cumulative depth
tiers (USD offered at a+1c, +2c, +5c, +10c, +25c); each tier's dollars fill at
that tier's upper price (the conservative edge), plus the taker fee at that
price; what the tiers do not hold is not filled; nothing above the 0.97 price
rail. A NO leg's depth is the YES bids' (a NO ask is a YES bid), converted to
NO dollars at the NO price. The tiers are the only depth the archive kept for
these days: exact ladders exist only from 25 Sep (P5.13).
"""
import argparse
import bisect
import csv
import datetime as dt
import glob
import gzip
import io
import json
import math
import os
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from statistics import pstdev
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import decision_engine as de  # noqa: E402
import risk_rails  # noqa: E402
import tick  # noqa: E402
from execution_cost import fee_per_share  # noqa: E402
from strategies import engine_views as ev  # noqa: E402

LADDER_MAX_AGE_S = 6 * 3600
BOOK_MAX_AGE_S = 3 * 3600
BANKROLL = 1000.0
TIERS = ((0.01, "1c"), (0.02, "2c"), (0.05, "5c"), (0.10, "10c"), (0.25, "25c"))
STRATEGIES = tuple(s for s in ev.ENGINE_STRATEGIES if not s.startswith("s10") and s not in ev.RESEARCH_ONLY)
UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------
def _json(path):
    with (gzip.open(path, "rt") if str(path).endswith(".gz") else open(path)) as f:
        return json.load(f)


def load_ladders(mirror_dir, band_market):
    """{market: [(epoch, {band: p})]} complete runs only, time-ordered."""
    runs = defaultdict(lambda: defaultdict(dict))
    for path in sorted(glob.glob(os.path.join(mirror_dir, "*.csv.gz"))):
        with gzip.open(path, "rt") as f:
            for row in csv.DictReader(f):
                m = band_market.get(row["band_id"])
                if m is None or row["calibrated_prob"] in ("", None):
                    continue
                t = int(dt.datetime.fromisoformat(row["computed_at"].replace("Z", "+00:00")).timestamp())
                runs[m][t][row["band_id"]] = float(row["calibrated_prob"])
    return runs


def complete_runs(runs, bands_of):
    out = {}
    for m, rs in runs.items():
        want = {b["band_id"] for b in bands_of.get(m, [])}
        ok = [(t, p) for t, p in sorted(rs.items()) if set(p) == want and abs(sum(p.values()) - 1) < 1e-3]
        if ok:
            out[m] = ok
    return out


def index_books(rows, columns):
    """{band: ([epochs], [snapshot dicts])} time-ordered."""
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(dict(zip(columns, r)))
    out = {}
    for b, snaps in by.items():
        snaps.sort(key=lambda s: s["epoch"])
        out[b] = ([s["epoch"] for s in snaps], snaps)
    return out


def as_of(series, t, max_age):
    """The newest item at or before t, no older than max_age; else None."""
    times, items = series
    i = bisect.bisect_right(times, t) - 1
    if i < 0 or t - times[i] > max_age:
        return None
    return items[i]


def model_spread(models_path):
    """{(city, date): pstdev of the seven models' lead-1 maxima} (P2.9's record)."""
    vals = defaultdict(list)
    with gzip.open(models_path, "rt") as f:
        for row in csv.DictReader(f):
            if row["lead_days"] == "1" and row["tmax_c"] not in ("", None):
                vals[(row["city_key"], row["for_date"])].append(float(row["tmax_c"]))
    return {k: pstdev(v) for k, v in vals.items() if len(v) >= 4}


# ---------------------------------------------------------------------------
# the fill
# ---------------------------------------------------------------------------
def fill(leg, snap, max_price):
    """(shares, usd paid incl. fee) for one IOC leg against the tiers."""
    want = float(leg["usd"])
    if want <= 0 or snap is None:
        return 0.0, 0.0
    if leg["side"] == "YES":
        best = snap.get("best_ask")
        depth = [snap.get(f"ask_usd_{k}") for _d, k in TIERS]
        scale = 1.0
    else:
        best = snap.get("no_best_ask")
        if best is None and snap.get("best_bid") is not None:
            best = 1 - float(snap["best_bid"])
        depth = [snap.get(f"bid_usd_{k}") for _d, k in TIERS]
        bid = snap.get("best_bid")
        # YES bid dollars -> shares -> NO dollars at the NO price
        scale = (float(best) / float(bid)) if (best is not None and bid) else 0.0
    if best is None:
        return 0.0, 0.0
    best = float(best)
    shares = paid = 0.0
    before = 0.0
    for (d, _k), cum in zip(TIERS, depth):
        if cum is None:
            break
        price = min(best + d, 1.0)
        if price > max_price:
            break
        room = max(float(cum) * scale - before, 0.0)
        before = float(cum) * scale
        take = min(room, want - paid)
        if take <= 0:
            continue
        unit = price + fee_per_share(price)
        shares += take / unit
        paid += take
        if paid >= want - 1e-9:
            break
    return shares, paid


# ---------------------------------------------------------------------------
# one strategy, walked through time
# ---------------------------------------------------------------------------
def plan(markets, peaks, date_from, date_to, ladders):
    """Time-ordered events: ('decide', t, market, checkpoint) and ('settle', t, market)."""
    events = []
    for m in markets.values():
        if not (date_from <= m["date"] <= date_to) or m["market_id"] not in ladders:
            continue
        day = m["date"]
        times = tick.decision_times(day, m["tz"], peaks.get((m["city"], day.month)))
        for cp in tick.CHECKPOINTS:
            if cp in times:
                events.append((int(times[cp][1].timestamp()), 0, "decide", m["market_id"], cp))
        end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=ZoneInfo(m["tz"]))
        events.append((int(end.timestamp()), 1, "settle", m["market_id"], None))
    events.sort()
    return events


def walk(args):
    sid, events, markets, bands_of, ladders, books, rails = args
    cash, realised = BANKROLL, 0.0
    realised_by_day = defaultdict(float)                            # UTC day -> realised P&L (the daily-loss rail)
    held = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))     # market -> band -> [yes, no]
    cost = defaultdict(float)                                       # market -> usd at cost
    rows, curve, leg_log = [], [], []
    for t, _o, kind, mid, cp in events:
        m = markets[mid]
        if kind == "settle":
            if mid in held:
                pay = sum(y if b == m["winner"] else n for b, (y, n) in held[mid].items())
                realised += pay - cost[mid]
                realised_by_day[dt.datetime.fromtimestamp(t, UTC).date()] += pay - cost[mid]
                cash += pay
                del held[mid]
                cost.pop(mid, None)
                curve.append((t, cash + sum(cost.values())))
            continue
        ladder = as_of((([x for x, _ in ladders[mid]]), [p for _, p in ladders[mid]]), t, LADDER_MAX_AGE_S)
        base = {"t": t, "strategy_id": sid, "market_id": mid, "city": m["city"], "date": m["date"].isoformat(),
                "checkpoint": cp, "winner": m["winner"]}
        if ladder is None:
            rows.append(dict(base, action="SKIP", reason="no pricing within 6 h"))
            continue
        book, snaps = {}, {}
        for b in bands_of[mid]:
            s = as_of(books[b["band_id"]], t, BOOK_MAX_AGE_S) if b["band_id"] in books else None
            if s is None:
                continue
            snaps[b["band_id"]] = s
            book[b["band_id"]] = {"ask": s["best_ask"], "bid": s["best_bid"], "no_ask": s["no_best_ask"],
                                  "depth_usd": s.get("ask_usd_5c")}
        ctx = {"bands": bands_of[mid], "unit": m["unit"], "probs": ladder, "book": book,
               "floor_c": None, "floor_basis": None, "reading_age_min": None}
        view, ebook, why = ev.engine_input(sid, ctx)
        if view is None:
            rows.append(dict(base, action="NONE", reason=why))
            continue
        equity = cash + sum(cost.values())
        today = realised_by_day.get(dt.datetime.fromtimestamp(t, UTC).date(), 0.0)
        ledger = {"equity_usd": equity, "cash_usd": cash, "on_market_usd": cost.get(mid, 0.0),
                  "pnl_today_usd": today,
                  "held": {b: tuple(v) for b, v in held.get(mid, {}).items()}, "held_usd": cost.get(mid, 0.0)}
        d = de.decide(view, book=ebook, ledger=ledger, rails=rails)
        got_sh = got_usd = 0.0
        legs = []
        for o in d["orders"]:
            sh, paid = fill(o, snaps.get(o["band_id"]), rails["max_price"])
            if sh <= 0 or paid > cash + 1e-9:
                continue
            cash -= paid
            cost[mid] += paid
            held[mid][o["band_id"]][0 if o["side"] == "YES" else 1] += sh
            got_sh += sh
            got_usd += paid
            legs.append(f"{o['band_id'][:8]}:{o['side']}:{paid:.2f}")
            # What the view said, what the book asked, what happened - per leg.
            p_view = ladder[o["band_id"]] if o["side"] == "YES" else 1 - ladder[o["band_id"]]
            won = (o["band_id"] == m["winner"]) == (o["side"] == "YES")
            leg_log.append((p_view, float(o["limit_price"]), won, paid))
        rows.append(dict(base, action=d["action"], reason=d["reason_code"], g_now=d["g_now"], g_target=d["g_target"],
                         wanted_usd=round(sum(o["usd"] for o in d["orders"]), 2), filled_usd=round(got_usd, 2),
                         legs=len(legs), fills=";".join(legs), top_is_winner=max(ladder, key=ladder.get) == m["winner"]))
    return sid, rows, {"cash": cash, "realised": realised, "open_cost": sum(cost.values()), "curve": curve,
                       "legs": leg_log}


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def summarise(rows, final, spread):
    by = defaultdict(list)
    for r in rows:
        by[r["strategy_id"]].append(r)
    out = {}
    for sid, rs in sorted(by.items()):
        acts = Counter(r["action"] for r in rs)
        traded = [r for r in rs if r.get("filled_usd", 0) > 0]
        days = {(r["market_id"]) for r in traded}
        f = final[sid]
        eq = f["cash"] + f["open_cost"]
        curve = [BANKROLL] + [v for _t, v in f["curve"]]
        peak, dd = BANKROLL, 0.0
        for v in curve:
            peak = max(peak, v)
            dd = max(dd, (peak - v) / peak)
        baskets = [r["legs"] for r in traded]
        terc = None
        if sid.startswith("s11") and traded:
            sp = sorted(spread[(r["city"], r["date"])] for r in traded if (r["city"], r["date"]) in spread)
            if len(sp) >= 9:
                lo, hi = sp[len(sp) // 3], sp[2 * len(sp) // 3]
                g = defaultdict(list)
                for r in traded:
                    s = spread.get((r["city"], r["date"]))
                    if s is None:
                        continue
                    g["low" if s <= lo else ("high" if s > hi else "mid")].append(r["legs"])
                terc = {k: (len(v), round(sum(v) / len(v), 2)) for k, v in g.items() if v}
        legs = f["legs"]
        n = len(legs)
        view = {"legs": n,
                "mean_view_p": round(sum(x[0] for x in legs) / n, 3) if n else None,
                "mean_ask": round(sum(x[1] for x in legs) / n, 3) if n else None,
                "won": round(sum(x[2] for x in legs) / n, 3) if n else None}
        out[sid] = {"view_vs_market": view, "decisions": len(rs), "actions": dict(acts), "trades": len(traded), "markets_traded": len(days),
                    "usd_filled": round(sum(r["filled_usd"] for r in traded), 2),
                    "wanted_usd": round(sum(r.get("wanted_usd", 0) or 0 for r in rs), 2),
                    "realised_pnl": round(f["realised"], 2), "equity": round(eq, 2),
                    "log_growth": round(math.log(eq / BANKROLL), 4) if eq > 0 else None,
                    "max_drawdown": round(dd, 4),
                    "mean_legs_per_trade": round(sum(baskets) / len(baskets), 2) if baskets else None,
                    "basket_by_disagreement_tercile": terc}
    return out


def report(summary, meta):
    lines = [f"# Engine replay, {meta['from']} to {meta['to']} (plan v2 P5.12 part 2)", "",
             "Generated by `scripts/backtest/replay_engine.py`; inputs in `data/replay/inputs_engine_2026-09-27/` "
             "(README there). Each strategy trades its own $1,000 shadow ledger through `decision_engine.decide` at the "
             "tick's checkpoints, with the engine's own probabilities and the book as they stood at each decision, "
             "fills walked through the archived depth tiers, and settlement on the venue's winner. The probabilities "
             "are the engine's (the live board's), so this judges the engine and the strategies' constraints on them, "
             "not a better model.", "",
             f"City-days: {meta['markets']} venue-confirmed, {meta['decisions']} strategy-decisions. Ladder no older "
             f"than {LADDER_MAX_AGE_S // 3600} h, book no older than {BOOK_MAX_AGE_S // 3600} h.", "",
             "| strategy | decisions | BUY | trades | markets | $ wanted | $ filled | realised P&L | equity | log growth | max drawdown | legs per trade |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for sid, s in summary.items():
        lines.append(f"| {sid} | {s['decisions']} | {s['actions'].get('BUY', 0)} | {s['trades']} | {s['markets_traded']} | "
                     f"{s['wanted_usd']:.2f} | {s['usd_filled']:.2f} | {s['realised_pnl']:+.2f} | {s['equity']:.2f} | "
                     f"{s['log_growth']} | {s['max_drawdown']} | {s['mean_legs_per_trade']} |")
    lines += ["", "Where each strategy traded: the view's probability for the side it bought, the book's ask, and "
              "how often that side won (per filled leg):", "",
              "| strategy | legs | view's probability | ask | won |", "|---|---|---|---|---|"]
    for sid, s in summary.items():
        v = s["view_vs_market"]
        lines.append(f"| {sid} | {v['legs']} | {v['mean_view_p']} | {v['mean_ask']} | {v['won']} |")
    lines += ["", "Actions and reasons, per strategy:", ""]
    for sid, s in summary.items():
        lines.append(f"- **{sid}**: " + ", ".join(f"{k} {v}" for k, v in sorted(s["actions"].items())))
    terc = {sid: s["basket_by_disagreement_tercile"] for sid, s in summary.items() if s["basket_by_disagreement_tercile"]}
    if terc:
        lines += ["", "S11 legs per trade by the models' day-ahead disagreement tercile (P8.3's test; tercile: (trades, mean legs)):", ""]
        for sid, t in terc.items():
            lines.append(f"- {sid}: {t}")
    lines += ["", "Not measured here: S10 (its remaining-day view needs the P7.3 replay's inputs as of each decision), "
              "maker fills, the timing model (the prior rule decided every WAIT), and the live decisions this must match "
              "(P5.12's acceptance, once the tick runs the engine).", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("replay_inputs")
    ap.add_argument("books")
    ap.add_argument("ladders_dir")
    ap.add_argument("--models", default="data/training/previous_runs/models_daily.csv.gz")
    ap.add_argument("--from", dest="date_from", default="2026-09-12")
    ap.add_argument("--to", dest="date_to", default="2026-09-25")
    ap.add_argument("--out-rows")
    ap.add_argument("--out-report")
    ap.add_argument("--processes", type=int, default=4)
    a = ap.parse_args(argv)
    inp = _json(a.replay_inputs)
    markets = {mid: {"market_id": mid, "city": c, "date": dt.date.fromisoformat(d), "winner": w, "unit": u, "tz": tz}
               for mid, c, d, w, u, tz in inp["markets"]}
    bands_of = defaultdict(list)
    for bid, mid, lo, hi, ol, oh in inp["bands"]:
        bands_of[mid].append({"band_id": bid, "band_lo": lo, "band_hi": hi, "open_low": bool(ol), "open_high": bool(oh)})
    for mid in bands_of:
        bands_of[mid].sort(key=lambda b: (not b["open_low"], b["band_lo"] if b["band_lo"] is not None else -1e9))
    peaks = {(c, int(mo)): h for c, mo, h in inp["peaks"] if h is not None}
    band_market = {b["band_id"]: mid for mid, bs in bands_of.items() for b in bs}
    ladders = complete_runs(load_ladders(a.ladders_dir, band_market), bands_of)
    bk = _json(a.books)
    books = index_books(bk["rows"], bk["columns"])
    events = plan(markets, peaks, dt.date.fromisoformat(a.date_from), dt.date.fromisoformat(a.date_to), ladders)
    rails = dict(risk_rails.DEFAULTS)
    jobs = [(sid, events, markets, bands_of, ladders, books, rails) for sid in STRATEGIES]
    with Pool(min(a.processes, len(jobs))) as pool:
        results = pool.map(walk, jobs)
    rows = [r for _sid, rs, _f in results for r in rs]
    final = {sid: f for sid, _rs, f in results}
    spread = model_spread(a.models)
    summary = summarise(rows, final, spread)
    meta = {"from": a.date_from, "to": a.date_to, "markets": len({e[3] for e in events}),
            "decisions": len(rows)}
    print(json.dumps({"meta": meta, "summary": summary}, indent=1, default=str))
    if a.out_rows:
        cols = ["t", "strategy_id", "market_id", "city", "date", "checkpoint", "winner", "action", "reason", "g_now",
                "g_target", "wanted_usd", "filled_usd", "legs", "fills", "top_is_winner"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["t"], r["strategy_id"], r["market_id"])):
            w.writerow(r)
        with gzip.open(a.out_rows, "wt") as f:
            f.write(buf.getvalue())
    if a.out_report:
        with open(a.out_report, "w") as f:
            f.write(report(summary, meta))
    return summary


if __name__ == "__main__":
    main()
