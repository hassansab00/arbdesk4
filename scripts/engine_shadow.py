"""The one engine decides at the tick's checkpoints, recorded only (plan v2
P5.12 part 3a).

For every checkpoint the tick writes, each engine strategy (P8.1: S10's three
variants, S11 and its lock, S12) decides through strategies.engine_views and
decision_engine.decide - the code the replay runs - on:

  view    the engine's own ladder (the checkpoint's probs) for S11 and S12;
          S10's remaining-day ladder (s10_shadow, same tick) for S10
  book    the CLOB top of book the tick read beside the call: YES bid and
          ask; a NO leg's ask is 1 - the YES bid and its bid 1 - the YES ask
          (one CLOB market, two tokens)
  ledger  the strategy's own shadow ledger (P5.1), read the way queue_plan
          reads it (paper_plans.rail_room): equity at cost is cash plus what
          open positions cost; the city-day holds positions plus live BUY
          reservations; plus what it holds on the same date elsewhere (P5.9
          rooms) and its realised high-water mark (v_desk_risk_state)
  params  the learned tables through their loaders (market_anchor.load,
          city_clusters.load): the priors while strategy_learning is off

and one `decisions` row is written per strategy and city-day: the action, a
reason code, g_now / g_wait, what bound it, target and held dollars, and the
versions it used. NOTHING IS ORDERED: the strategies are registered disabled,
in research (20260927150000); part 3b sends the orders to their ledgers.

A strategy that has no view here - no market to anchor on, no S10 ladder -
is a row too (NONE with its reason): what it could not decide is part of the
record, as P5.11 says. s2_combination_arb decides on the old signal path
until that path is retired, so it is not decided here (one record per id).

Bounded: the engine stops at the tick's deadline and the city-days it did not
reach are counted, not written. Never raises into the tick.
"""
import datetime as dt
import json
import re
import time
import uuid

JOB_KEY = "engine"
STRATEGIES = ("s10_winner", "s10_growth", "s10_lock", "s11_ladder", "s11_lock", "s12_no")
S10 = ("s10_winner", "s10_growth", "s10_lock")
WRITE_RESERVE_S = 3.0

# engine_views says why it has no view in words; decisions.reason_code is a
# code (^[a-z_]{1,40}$). Anything not listed is `no_view`.
WHY_CODES = (
    ("no market to anchor on", "no_market_anchor"),
    ("no ladder", "no_ladder"),
    ("no ledger", "no_ledger"),
    ("research only", "research_only"),
)


def reason_code(why):
    """A decisions.reason_code for engine_views' reason or decide's code."""
    if why is None:
        return "no_view"
    text = str(why)
    m = re.match(r"s10 (BUY|SELL|SWITCH|HOLD|WAIT|NONE)\b", text)
    if m:
        return f"s10_{m.group(1).lower()}"
    for words, code in WHY_CODES:
        if text.startswith(words):
            return code
    return text if re.fullmatch(r"[a-z_]{1,40}", text) else "no_view"


def s10_action(why):
    """S10's own action when it declined to buy (SELL / SWITCH / HOLD / WAIT
    / NONE), from engine_views' reason; NONE otherwise."""
    m = re.match(r"s10 (SELL|SWITCH|HOLD|WAIT|NONE)\b", str(why or ""))
    return m.group(1) if m else "NONE"


def engine_book(market):
    """decision_engine's book from the tick's CLOB tops {band: {bid, ask, last}}."""
    out = {}
    for b, t in (market or {}).items():
        bid, ask = t.get("bid"), t.get("ask")
        out[b] = {"bid": bid, "ask": ask,
                  "no_ask": None if bid is None else round(1.0 - float(bid), 6),
                  "no_bid": None if ask is None else round(1.0 - float(ask), 6),
                  "depth_usd": None}
    return out


def ledger(account, positions, city_day_of, live_orders, city, target, high_water=None, pnl_today=0.0):
    """decision_engine's ledger for one strategy and city-day.

    account      paper_accounts row (cash, reserved_cash)
    positions    that account's open paper_positions (band_id, side, shares, cost_basis)
    city_day_of  {band_id: (city_key, resolution_date iso)}
    live_orders  that account's queued/working BUY paper_orders (band_id, cash_ceiling)
    """
    cash = float(account.get("cash") or 0.0)
    reserved = float(account.get("reserved_cash") or 0.0)
    cost = sum(float(p.get("cost_basis") or 0.0) for p in positions)
    equity = cash + cost
    held, held_usd, same_day = {}, 0.0, {}
    key = (city, str(target))
    for p in positions:
        cd = city_day_of.get(str(p["band_id"]))
        if cd is None:
            continue
        c = float(p.get("cost_basis") or 0.0)
        if cd == key:
            y, n = held.get(str(p["band_id"]), (0.0, 0.0))
            sh = float(p.get("shares") or 0.0)
            held[str(p["band_id"])] = (y + sh, n) if p.get("side") == "YES" else (y, n + sh)
            held_usd += c
        elif cd[1] == key[1]:
            same_day[cd[0]] = same_day.get(cd[0], 0.0) + c
    reserved_here = 0.0
    for o in live_orders:
        cd = city_day_of.get(str(o["band_id"]))
        c = float(o.get("cash_ceiling") or 0.0)
        if cd == key:
            reserved_here += c
        elif cd is not None and cd[1] == key[1]:
            same_day[cd[0]] = same_day.get(cd[0], 0.0) + c
    return {"equity_usd": equity, "cash_usd": cash - reserved, "on_market_usd": held_usd + reserved_here,
            "pnl_today_usd": float(pnl_today or 0.0), "held": held, "held_usd": held_usd,
            "same_day": same_day, "high_water_usd": None if high_water is None else float(high_water)}


def decision_row(run_id, decided_at, checkpoint_id, strategy_id, city, target, d=None, why=None):
    """One decisions row from decide's output, or from the reason there was none."""
    if d is None:
        action = s10_action(why) if strategy_id in S10 else "NONE"
        return {"run_id": run_id, "tick_id": run_id, "decided_at": decided_at, "checkpoint_id": checkpoint_id,
                "strategy_id": strategy_id, "city_key": city, "resolution_date": str(target),
                "action": action, "reason_code": reason_code(why), "g_now": None, "g_wait": None,
                "binding": [], "target_usd": None, "held_usd": None, "n_signals": 0,
                "params_version": None}
    return {"run_id": run_id, "tick_id": run_id, "decided_at": decided_at, "checkpoint_id": checkpoint_id,
            "strategy_id": strategy_id, "city_key": city, "resolution_date": str(target),
            "action": d["action"], "reason_code": reason_code(d.get("reason_code")),
            "g_now": _num(d.get("g_now")), "g_wait": _num(d.get("g_wait")),
            "binding": [str(b)[:40] for b in (d.get("binding") or [])],
            "target_usd": _num(d.get("target_usd")), "held_usd": _num(d.get("held_usd")),
            "n_signals": len(d.get("orders") or []),
            "params_version": json.dumps(d.get("versions") or {}, sort_keys=True, default=str)}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def decide_all(checkpoints, s10_ladders, bands_of, unit_of, floors, ledgers, params, anchor_table,
               deadline, run_id, decided_at):
    """(rows, detail). checkpoints: [(checkpoint_id, row)] written this tick,
    latest per city-day. deadline: time.monotonic() to stop at."""
    import decision_engine as de
    from strategies import engine_views as ev

    rows, skipped, reached = [], 0, 0
    for checkpoint_id, row in checkpoints:
        if time.monotonic() >= deadline:
            skipped += 1
            continue
        reached += 1
        city, target, name = row["city_key"], str(row["target_date"]), row["checkpoint"]
        bands = bands_of.get((city, target)) or []
        floor = floors.get(city)
        floor_c = floor[1] if floor and floor[0] == target else None
        book = engine_book(row.get("market"))
        for sid in STRATEGIES:
            probs = s10_ladders.get((city, target, name)) if sid in S10 else row.get("probs")
            led = ledgers.get(sid)
            if led is None:
                rows.append(decision_row(run_id, decided_at, checkpoint_id, sid, city, target,
                                         why="no ledger"))
                continue
            lg = led(city, target)
            if not probs:
                rows.append(decision_row(run_id, decided_at, checkpoint_id, sid, city, target,
                                         why="no ladder"))
                continue
            held_s10 = None
            if sid in S10:
                yes = [(b, y) for b, (y, _n) in lg["held"].items() if y > 0]
                held_s10 = {"band_id": yes[0][0], "shares": yes[0][1]} if yes else None
            ctx = {"bands": bands, "unit": unit_of.get(city, "C"), "probs": dict(probs), "book": book,
                   "floor_c": floor_c, "floor_basis": floor[2] if floor and len(floor) > 2 else None,
                   "reading_age_min": None, "held": held_s10, "checkpoint": name,
                   "anchor": {"table": anchor_table}}
            view, ebook, why = ev.engine_input(sid, ctx)
            if view is None:
                rows.append(decision_row(run_id, decided_at, checkpoint_id, sid, city, target, why=why))
                continue
            d = de.decide(view, book=ebook, ledger=lg, params=params)
            rows.append(decision_row(run_id, decided_at, checkpoint_id, sid, city, target, d=d))
    return rows, {"city_days": len(checkpoints), "reached": reached, "out_of_time": skipped}


# ---------------------------------------------------------------------------
# I/O, called from tick.run; never raises
# ---------------------------------------------------------------------------
def read_ledgers(rest, rest_all, now):
    """{strategy_id: f(city, target) -> ledger} from the strategies' shadow ledgers."""
    accounts = rest("paper_accounts", [("select", "account_id,strategy_id,cash,reserved_cash"),
                                       ("kind", "eq.shadow"), ("status", "eq.active"),
                                       ("strategy_id", f"in.({','.join(STRATEGIES)})")])
    if not accounts:
        return {}
    ids = ",".join(str(a["account_id"]) for a in accounts)
    positions = rest_all("paper_positions", [("select", "account_id,band_id,side,shares,cost_basis"),
                                             ("account_id", f"in.({ids})"), ("shares", "gt.0")],
                         order="account_id.asc,band_id.asc")
    orders = rest_all("paper_orders", [("select", "account_id,band_id,cash_ceiling"),
                                       ("account_id", f"in.({ids})"), ("status", "in.(queued,working)"),
                                       ("action", "eq.BUY")], order="account_id.asc")
    risk = {str(r["account_id"]): r for r in rest("v_desk_risk_state", [
        ("select", "account_id,high_water"), ("account_id", f"in.({ids})")]) or []}
    today = now.date().isoformat()
    closed = rest_all("paper_trades", [("select", "account_id,net_pnl"), ("account_id", f"in.({ids})"),
                                       ("closed_at", f"gte.{today}")], order="account_id.asc")
    band_ids = sorted({str(p["band_id"]) for p in positions} | {str(o["band_id"]) for o in orders})
    city_day_of = {}
    if band_ids:
        bands = rest_all("bands", [("select", "band_id,market_id"), ("band_id", f"in.({','.join(band_ids)})")],
                         order="band_id.asc")
        mids = sorted({str(b["market_id"]) for b in bands})
        mk = {str(m["market_id"]): (m["city_key"], str(m["resolution_date"])) for m in rest_all(
            "markets", [("select", "market_id,city_key,resolution_date"), ("market_id", f"in.({','.join(mids)})")],
            order="market_id.asc")} if mids else {}
        city_day_of = {str(b["band_id"]): mk.get(str(b["market_id"])) for b in bands
                       if mk.get(str(b["market_id"]))}
    out = {}
    for a in accounts:
        aid = str(a["account_id"])
        pos = [p for p in positions if str(p["account_id"]) == aid]
        live = [o for o in orders if str(o["account_id"]) == aid]
        hw = (risk.get(aid) or {}).get("high_water")
        pnl = sum(float(t.get("net_pnl") or 0.0) for t in closed if str(t["account_id"]) == aid)
        out[a["strategy_id"]] = (lambda c, t, a=a, pos=pos, live=live, hw=hw, pnl=pnl:
                                 ledger(a, pos, city_day_of, live, c, t, hw, pnl))
    return out


def record(written_rows, s10_ladders, bands_by_market, market_of, unit_of, floors, now, deadline,
           dry_run=False):
    """Decide for the checkpoints the tick just wrote, and write the rows."""
    from common import rest, rest_all, insert
    import city_clusters
    import market_anchor
    t0 = time.monotonic()
    out = {"strategies": len(STRATEGIES), "written": 0}
    try:
        if not written_rows:
            out["city_days"] = 0
            return out
        keys = {(r["city_key"], str(r["target_date"]), r["checkpoint"]) for r in written_rows}
        ids = {}
        cities = sorted({k[0] for k in keys})
        dates = sorted({k[1] for k in keys})
        for r in rest_all("prediction_checkpoints", [
                ("select", "checkpoint_id,city_key,target_date,checkpoint,engine_version"),
                ("city_key", f"in.({','.join(cities)})"), ("target_date", f"in.({','.join(dates)})")],
                order="checkpoint_id.asc"):
            ids[(r["city_key"], str(r["target_date"]), r["checkpoint"], r.get("engine_version"))] = r["checkpoint_id"]
        latest = {}
        for r in written_rows:
            k = (r["city_key"], str(r["target_date"]))
            if k not in latest or r["local_decision_time"] > latest[k]["local_decision_time"]:
                latest[k] = r
        checkpoints = [(ids.get((r["city_key"], str(r["target_date"]), r["checkpoint"], r.get("engine_version"))), r)
                       for r in latest.values()]
        bands_of = {}
        for (city, target), _r in latest.items():
            m = market_of.get((city, target))
            if m:
                bands_of[(city, target)] = sorted(
                    bands_by_market.get(m["market_id"], []),
                    key=lambda b: (not b.get("open_low"), b["band_lo"] if b.get("band_lo") is not None else -1e9))
        ledgers = read_ledgers(rest, rest_all, now)
        params = {"clusters": city_clusters.load(rest)}
        anchor_table = market_anchor.load(rest)
        run_id = str(uuid.uuid4())
        rows, detail = decide_all(checkpoints, s10_ladders, bands_of, unit_of, floors, ledgers, params,
                                  anchor_table, deadline - WRITE_RESERVE_S, run_id,
                                  now.isoformat())
        out.update(detail)
        out["actions"] = {}
        for r in rows:
            out["actions"][r["action"]] = out["actions"].get(r["action"], 0) + 1
        if rows and not dry_run:
            out["written"] = insert("decisions", rows)
        elif rows:
            out["would_write"] = len(rows)
        out["run_id"] = run_id
    except Exception as e:                       # noqa: BLE001 - never into the tick
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
