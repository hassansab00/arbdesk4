"""The engine's BUYs reach its shadow ledgers, filled inside the tick (plan v2
P5.12 part 3b).

engine_shadow decides at each checkpoint and writes `decisions`. For a
strategy that is switched on (strategies.enabled: shadow or portfolio), each
BUY becomes one plan on the strategy's own shadow ledger:

  legs      decision_engine's IOC orders at the ask the tick read, as
            queue_plan wants them: {band_id, side, shares, limit_price,
            cash_ceiling}. The reserve covers the order filling entirely at
            its limit plus the venue fee at the worst price it can fill at
            (FEE_RATE, 0.05 on all 1,729 books the paper engine captured
            16-27 Sep), and a cent.
  dropped   a leg worth less than the venue's minimum order at its limit
            (MIN_ORDER_USD: 5 USDC on every one of those books) is not sent -
            the fill simulator would refuse it after spending a book capture
            on it - and is recorded in the plan's evidence instead.
  evidence  the decision (id, run, checkpoint, versions), each leg's
            posterior (legs_p, which the position records at entry) and the
            weighted net edge per share queue_plan checks against the
            ledger's min_edge (0 on a shadow ledger).

public.publish_engine_plan queues it through arbdesk_private.queue_plan -
every rail, cap and holding check - or leaves it 'blocked' with the reason.
The orders expire in 5 minutes, so the tick fills them itself, now:
claim_account_order per ledger, paper_worker.fill_order (the book captured at
that moment, the same simulator and completion as every paper order). Ledgers
fill in parallel; one ledger's legs one at a time (one lease per account).

Bounded: no fill starts after the deadline, at most MAX_FILLS per tick; what
is not filled expires and its reserve is released (expire_paper_commands).
Never raises into the tick.
"""
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

FEE_RATE = 0.05          # measured: every paper_book_evidence fee_rate, 16-27 Sep
MIN_ORDER_USD = 5.0      # measured: every paper_book_evidence min_order_size (USDC), 16-27 Sep
SHARE_STEP = 0.01
MAX_FILLS = 12           # prior: per tick, so the minute cannot be spent on fills
FILL_SECONDS = 2.5       # prior: a fill is not started with less than this left


def _floor_step(x, step=SHARE_STEP):
    return math.floor(x / step + 1e-9) * step


def _ceil_cents(x):
    return math.ceil(x * 100 - 1e-9) / 100


def worst_fee_per_share(limit, rate=FEE_RATE):
    """The largest fee a share can carry when it fills at or below `limit`:
    rate x q x (1 - q) rises with q up to a half."""
    q = min(float(limit), 0.5)
    return rate * q * (1.0 - q)


def plan_legs(orders, p_post):
    """(legs, dropped, legs_p) from decision_engine's orders.

    orders  [{band_id, side, limit_price, usd, shares, ...}] (decide's IOC legs)
    p_post  {band_id: posterior P(YES)} (decide's p_post)
    """
    legs, dropped, legs_p = [], [], {}
    for o in orders or []:
        band, side = str(o["band_id"]), o["side"]
        limit = o.get("limit_price")
        if limit is None or not (0.0 < float(limit) < 1.0):
            dropped.append({"band_id": band, "side": side, "why": "no price to buy at"})
            continue
        limit = float(limit)
        shares = _floor_step(float(o.get("shares") or 0.0))
        if shares <= 0 or shares * limit < MIN_ORDER_USD:
            dropped.append({"band_id": band, "side": side, "usd": round(shares * limit, 2),
                            "why": f"below the venue minimum of {MIN_ORDER_USD:g} USDC"})
            continue
        ceiling = _ceil_cents(shares * limit + shares * worst_fee_per_share(limit) + 0.01)
        legs.append({"band_id": band, "side": side, "shares": f"{shares:.2f}", "limit_price": f"{limit:g}",
                     "cash_ceiling": f"{ceiling:.2f}"})
        p_yes = p_post.get(band)
        if p_yes is not None:
            legs_p[f"{band}:{side}"] = {"p": round(float(p_yes) if side == "YES" else 1.0 - float(p_yes), 6)}
    return legs, dropped, legs_p


def fit_to_room(legs, room_usd):
    """(legs, None) when their cash ceilings fit `room_usd`, else (legs scaled
    to fit, {"room_usd", "before", "after"}).

    The rails sum each leg's cash ceiling - its cost at the limit, the worst
    fee and a cent, rounded up - against the city-day and cluster rooms;
    decide sizes the city-day on cost at the fee its price carries. A plan
    sized to the rail therefore asks a few cents more than it allows and is
    refused whole: 8 Oct 11:36Z, the twins' first BUYs, "Rail: 30.05 on milan"
    and "30.01 on tel_aviv" against $30.00. Every leg's shares are scaled by
    one factor, floored to the share step, until the ceilings fit; the room is
    taken in whole cents, down."""
    if room_usd is None or not legs:
        return legs, None
    room = math.floor(float(room_usd) * 100 + 1e-6) / 100
    before = round(sum(float(l["cash_ceiling"]) for l in legs), 2)
    if before <= room:
        return legs, None
    f = room / before
    out, after = [], before
    for _ in range(40):
        out = []
        for l in legs:
            limit = float(l["limit_price"])
            shares = _floor_step(float(l["shares"]) * f)
            ceiling = _ceil_cents(shares * limit + shares * worst_fee_per_share(limit) + 0.01)
            out.append(dict(l, shares=f"{shares:.2f}", cash_ceiling=f"{ceiling:.2f}"))
        after = round(sum(float(l["cash_ceiling"]) for l in out), 2)
        if after <= room:
            break
        f *= 0.995
    return out, {"room_usd": f"{room:.2f}", "before": f"{before:.2f}", "after": f"{after:.2f}"}


def net_edge_per_share(legs, legs_p, rate=FEE_RATE):
    """Share-weighted (posterior - cost at the limit, fee included); None
    without every leg's posterior (queue_plan then refuses the plan)."""
    total, edge = 0.0, 0.0
    for leg in legs:
        p = (legs_p.get(f"{leg['band_id']}:{leg['side']}") or {}).get("p")
        if p is None:
            return None
        q, limit = float(leg["shares"]), float(leg["limit_price"])
        edge += q * (p - (limit + rate * limit * (1.0 - limit)))
        total += q
    return round(edge / total, 6) if total > 0 else None


def build(decision_id, decision_row, d, run_id, checkpoint_id):
    """(legs, evidence) for one BUY decision, or (None, why)."""
    legs, dropped, legs_p = plan_legs(d.get("orders"), d.get("p_post") or {})
    legs, fitted = fit_to_room(legs, d.get("room_usd"))
    if fitted:
        kept = [l for l in legs if float(l["shares"]) * float(l["limit_price"]) >= MIN_ORDER_USD]
        dropped += [{"band_id": l["band_id"], "side": l["side"],
                     "usd": round(float(l["shares"]) * float(l["limit_price"]), 2),
                     "why": f"below the venue minimum of {MIN_ORDER_USD:g} USDC once fitted to the rails"}
                    for l in legs if l not in kept]
        legs = kept
    if not legs:
        return None, "every leg below the venue minimum or unpriced"
    edge = net_edge_per_share(legs, legs_p)
    evidence = {"source": "engine", "decision_id": decision_id, "run_id": run_id, "checkpoint_id": checkpoint_id,
                "strategy_id": decision_row["strategy_id"], "city_key": decision_row["city_key"],
                "resolution_date": decision_row["resolution_date"], "engine_version": d.get("engine_version"),
                "versions": d.get("versions"), "g_now": d.get("g_now"), "g_target": d.get("g_target"),
                "target_usd": d.get("target_usd"), "legs_p": legs_p, "dropped": dropped,
                "net_edge_per_share": None if edge is None else f"{edge:.6f}", "fitted_to_room": fitted,
                "execution_assumption": "Independent IOC legs at the tick's ask; filled against the book "
                                        "captured at fill time"}
    return (legs, evidence), None


def fill_ledgers(account_ids, deadline, claim, fill_one, max_fills=MAX_FILLS):
    """Fill every queued order of these ledgers until the deadline.
    Returns {status: n} over the orders completed."""
    budget, lock = {"left": max_fills}, threading.Lock()
    out = {}

    def take():
        with lock:
            if budget["left"] <= 0:
                return False
            budget["left"] -= 1
            return True

    def one_ledger(aid):
        done = []
        while time.monotonic() + FILL_SECONDS < deadline and take():
            order = claim(aid)
            if not order:
                with lock:
                    budget["left"] += 1
                break
            try:
                result = fill_one(order)
                done.append((result or {}).get("status") or "unknown")
            except Exception as e:                        # noqa: BLE001 - never into the tick
                done.append(f"error: {type(e).__name__}")
        return done

    ids = list(dict.fromkeys(str(a) for a in account_ids))
    if not ids:
        return out
    with ThreadPoolExecutor(max_workers=len(ids)) as pool:
        for statuses in pool.map(one_ledger, ids):
            for s in statuses:
                out[s] = out.get(s, 0) + 1
    return out


def refusal(e):
    """The database's reason for refusing one order: common.rpc puts the
    PostgREST body, whose `message` is the RAISE, in the exception."""
    text = str(e)
    body = text.split(": ", 1)[1] if ": " in text else ""
    try:
        text = json.loads(body).get("message") or body
    except (ValueError, AttributeError):
        text = body or text
    return text[:80]


def sell_leg(sell):
    """(shares, limit) for an exit at the bid, or (None, why)."""
    limit = sell.get("limit_price")
    if limit is None or not (0.0 < float(limit) < 1.0):
        return None, "no bid to sell at"
    shares = _floor_step(float(sell.get("shares") or 0.0))
    if shares <= 0 or shares * float(limit) < MIN_ORDER_USD:
        return None, f"below the venue minimum of {MIN_ORDER_USD:g} USDC"
    return (shares, float(limit)), None


def send_exits(exits, accounts, enabled, decision_ids, deadline, rpc, fill_one, dry_run=False):
    """Carry out S10's SELLs and SWITCHes, one at a time: submit the exit,
    fill it now, and for a SWITCH whose sale filled, hand back its buy half.
    Returns (detail, [(row, d_buy, checkpoint_id)] to send as BUYs)."""
    out = {"exits": len(exits), "submitted": 0, "skipped": {}, "fills": {}, "switch_buys": 0}
    buys = []
    for ex in exits:
        row = ex["row"]
        sid = row["strategy_id"]
        key = (sid, row["city_key"], str(row["resolution_date"]))
        why = None
        if sid not in enabled:
            why = "strategy not switched on"
        elif sid not in accounts:
            why = "no shadow ledger"
        elif decision_ids.get(key) is None:
            why = "decision id not found"
        leg = None
        if why is None:
            leg, why = sell_leg(ex["sell"])
        if why is None and time.monotonic() + 2 * FILL_SECONDS >= deadline:
            why = "no time left in the tick"
        if why:
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
            continue
        if dry_run:
            out["submitted"] += 1
            continue
        shares, limit = leg
        try:
            rpc("submit_engine_exit", {"p_account": accounts[sid], "p_decision": decision_ids[key],
                                       "p_band": ex["sell"]["band_id"], "p_side": ex["sell"]["side"],
                                       "p_shares": round(shares, 2), "p_limit": limit})
        except Exception as e:                            # noqa: BLE001 - one refusal is not the tick's
            why = f"refused: {refusal(e)}"
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
            continue
        out["submitted"] += 1
        status = "not claimed"
        try:
            order = rpc("claim_account_order", {"p_account": accounts[sid]})
            if order:
                status = (fill_one(order) or {}).get("status") or "unknown"
        except Exception as e:                            # noqa: BLE001 - never into the tick
            status = f"error: {type(e).__name__}"
        out["fills"][status] = out["fills"].get(status, 0) + 1
        if ex["kind"] == "SWITCH" and status in ("filled", "partial") and ex.get("buy"):
            buys.append((row, ex["buy"], ex["checkpoint_id"]))
            out["switch_buys"] += 1
    return out, buys


def send(buys, accounts, enabled, decision_ids, run_id, deadline, rpc, rest, fill_one, dry_run=False, exits=(),
         max_fills=MAX_FILLS):
    """Carry out S10's exits (send_exits), then publish a plan per BUY of a
    switched-on strategy - and per switch whose sale filled - and fill them.

    buys          [(decision_row, d, checkpoint_id)] - the BUY rows engine_shadow wrote
    accounts      {strategy_id: account_id} of the shadow ledgers
    enabled       strategy ids switched on
    decision_ids  {(strategy_id, city_key, resolution_date): decision_id} of this run
    exits         engine_shadow.exit_of's exits
    """
    out = {"buys": len(buys), "published": 0, "queued": 0, "blocked": {}, "skipped": {}, "fills": {}}
    t0 = time.monotonic()
    if not dry_run:
        # An order left from an earlier tick holds its reserve and its band
        # until something expires it; this tick's claims must find only its own.
        out["expired_before"] = rpc("expire_paper_commands", {})
    if exits:
        out["exit_orders"], switch_buys = send_exits(exits, accounts, enabled, decision_ids, deadline, rpc,
                                                     fill_one, dry_run=dry_run)
        buys = list(buys) + switch_buys
    queued_accounts, plan_ids = [], []
    for row, d, checkpoint_id in buys:
        sid = row["strategy_id"]
        key = (sid, row["city_key"], str(row["resolution_date"]))
        why = None
        if sid not in enabled:
            why = "strategy not switched on"
        elif sid not in accounts:
            why = "no shadow ledger"
        elif decision_ids.get(key) is None:
            why = "decision id not found"
        if why:
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
            continue
        built, why = build(decision_ids[key], row, d, run_id, checkpoint_id)
        if built is None:
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
            continue
        legs, evidence = built
        if dry_run:
            out["published"] += 1
            continue
        try:
            plan_ids.append(rpc("publish_engine_plan", {"p_account": accounts[sid],
                                                        "p_decision": decision_ids[key],
                                                        "p_legs": legs, "p_evidence": evidence}))
        except Exception as e:                            # noqa: BLE001 - one refusal is not the tick's
            why = f"refused: {refusal(e)}"
            out["skipped"][why] = out["skipped"].get(why, 0) + 1
            continue
        out["published"] += 1
        queued_accounts.append(accounts[sid])
    if plan_ids:
        for p in rest("paper_trade_plans", [("select", "plan_id,status,reason"),
                                            ("plan_id", f"in.({','.join(str(i) for i in plan_ids)})")]) or []:
            if p["status"] == "queued":
                out["queued"] += 1
            else:
                r = str(p.get("reason") or p["status"])[:80]
                out["blocked"][r] = out["blocked"].get(r, 0) + 1
    if out["queued"] and not dry_run:
        out["fills"] = fill_ledgers(queued_accounts, deadline,
                                    lambda aid: rpc("claim_account_order", {"p_account": aid}), fill_one,
                                    max_fills=max_fills)
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out
