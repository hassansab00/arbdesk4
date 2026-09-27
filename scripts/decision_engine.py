"""One engine for live and replay (plan v2 P5.12), part 1: the decision.

Every strategy supplies a VIEW - a ladder of model probabilities plus its
constraints - and this decides the rest, the same way whether the hourly tick
or the replay harness calls it:

    view -> belief (P5.3) -> holdings solver (P5.5) inside the rails (P5.9)
         -> no-trade band -> timing (P5.6) -> orders (P5.7)

It is pure. Every input is an argument - the book, the ledger, the rails, the
learned parameters, the clock - and nothing is read or written, so the tick
and the replay can only differ by what they hand it (the plan's acceptance:
the replay's decisions for a day match the live ones on >= 95% of rows).

THE VIEW. {"strategy_id", "city_key", "resolution_date", "probs": {band: p}}
and optionally:
  allow     sides the strategy may buy, default ("YES",)
  only      asset names ("<band>:YES") it may buy; every other asset is capped
            at zero (S10's one bucket is only=[target])
  lock      the book must not end below the ledger's wealth in any outcome
  sds       posterior sds to size on the worst ALPHA of draws; default: the
            belief layer's own
THE LEDGER. {"equity_usd", "cash_usd", "on_market_usd", "pnl_today_usd",
"held": {band: (yes_shares, no_shares)}} - the strategy's own shadow ledger -
and optionally "high_water_usd" (drawdown scaling) and "same_day": {city:
usd held plus reserved on this resolution date} (the cluster and correlated
rooms, P5.9 part 2).
PARAMS. The learned values (lambda, h, alpha, c_wait, belief table, timing
model, city clusters), each clipped to its bounds by the module that owns it; None anywhere
is that module's prior. Rule 11: every version used is on the decision.

PART 1 LIMITS, stated so they are not mistaken for decisions:
  - orders are IOC takers. The maker option (paper_fill_sim.choose_order_type
    with execution_cost.maker_quote) needs the fill simulator in the loop,
    which is part 2 with the replay.
  - selling is not decided here (holdings_solver.solve_book says the same):
    the strategy's own rules (S10's certainly-lost and switch) produce SELLs.
  - nothing calls this yet. Part 2 is the replay over 12-22 Sep; part 3 the
    tick, the ledgers and the decisions rows.
"""
import math

import city_clusters
import holdings_solver as hs
import paper_fill_sim
import risk_rails
import timing
from belief import ladder_posterior

ENGINE_VERSION = "engine-v1"


def against_market_assets(probs, book, allow):
    """Assets that back the view against the market's favourite (the platform's
    rule, edge_engine.against_market_rows; Hassan, 24 Sep: "never favour losing
    bets"): when the view's favourite bucket and the market's differ, YES on a
    bucket the market does not favour and NO on the one it does. Empty when
    they agree or either favourite cannot be named. The market's favourite is
    the highest YES ask."""
    priced = {b: p for b, p in probs.items() if p is not None}
    quoted = {b: float(q["ask"]) for b, q in (book or {}).items() if q and q.get("ask") is not None}
    if not priced or not quoted:
        return set()
    view_fav = max(priced, key=lambda b: (priced[b], b))
    market_fav = max(quoted, key=lambda b: (quoted[b], b))
    if view_fav == market_fav:
        return set()
    out = set()
    if "YES" in allow:
        out |= {f"{b}:YES" for b in probs if b != market_fav}
    if "NO" in allow:
        out.add(f"{market_fav}:NO")
    return out


def _price(q, key):
    v = (q or {}).get(key)
    return None if v is None else float(v)


def _ladder(ids, post, book):
    return [{"id": b, "p": post[b][0],
             "yes_price": _price(book.get(b), "ask"), "no_price": _price(book.get(b), "no_ask")}
            for b in ids]


def _held_fraction(ids, held, equity):
    """What the holdings pay in each outcome, per dollar of the ledger's wealth."""
    h = [0.0] * len(ids)
    for bid, (yes, no) in (held or {}).items():
        if bid not in ids:
            continue
        i = ids.index(bid)
        for k in range(len(ids)):
            h[k] += ((float(yes) if k == i else 0.0) + (0.0 if k == i else float(no))) / equity
    return h


def _growth(ps, wealth):
    total = 0.0
    for p, w in zip(ps, wealth):
        if p <= 0:
            continue
        if w <= 0:
            return -math.inf
        total += p * math.log(w)
    return total


def _book_wealth(ids, ladder, allow, weights, cash, held_frac):
    names, x = hs.assets(ladder, allow)
    w = [cash] + [weights.get(n[0], 0.0) for n in names[1:]]
    return [sum(w[j] * x[j][k] for j in range(len(w))) + held_frac[k] for k in range(len(ids))]


def decide(view, *, book, ledger, rails=None, halted=False, params=None, state=None,
           hours_to_close=None):
    """One decision for one strategy and city-day.

    book   {band: {"ask", "bid", "no_ask", "no_bid", "depth_usd"}} at the decision
    state  timing's state for the best new asset: {"hours_to_peak", "regime",
           "gap_prev", "depth"}; p and ask are filled in here
    Returns {"action", "reason_code", "orders", "g_now", "g_target", "g_wait",
    "binding", "target_usd", "held_usd", "p_post", "versions", "timing"}.
    """
    params = params or {}
    rails = rails or dict(risk_rails.DEFAULTS)
    ids = list(view["probs"])
    equity = float(ledger["equity_usd"])
    cash = float(ledger.get("cash_usd", equity))
    held = ledger.get("held") or {}
    held_usd = float(ledger.get("held_usd", 0.0))
    allow = tuple(view.get("allow") or ("YES",))
    out = {"strategy_id": view.get("strategy_id"), "city_key": view.get("city_key"),
           "resolution_date": view.get("resolution_date"), "engine_version": ENGINE_VERSION,
           "action": "NONE", "reason_code": None, "orders": [], "g_now": None, "g_target": None,
           "g_wait": None, "binding": [], "target_usd": round(held_usd, 2), "held_usd": round(held_usd, 2),
           "timing": None, "drawdown_scale": None,
           "versions": {"engine": ENGINE_VERSION,
                        "belief": (params.get("belief_table") or {}).get("version", "prior"),
                        "lambda": params.get("lambda_version", "prior"),
                        "h": params.get("h_version", "prior"),
                        "timing": timing.TIMING_VERSION,
                        "market_anchor": view.get("anchor"),
                        "clusters": city_clusters.version_of(params.get("clusters"))}}
    holding = any(float(y) > 0 or float(n) > 0 for y, n in held.values())
    idle = "HOLD" if holding else "NONE"

    if halted:
        out.update(action=idle, reason_code="halted")
        return out
    if equity <= 0:
        out.update(action="NONE", reason_code="no_equity")
        return out
    if risk_rails.daily_loss_hit(rails, equity, float(ledger.get("pnl_today_usd", 0.0))):
        out.update(action=idle, reason_code="daily_loss")
        return out

    post = ladder_posterior(view["probs"], params.get("belief_table"),
                            view.get("cluster"), view.get("checkpoint"))
    out["p_post"] = {b: post[b][0] for b in ids}
    ladder = _ladder(ids, post, book)
    sds = view.get("sds")
    if sds is None:
        sds = [post[b][1] for b in ids]
    names, _x = hs.assets(ladder, allow)
    caps = None
    if view.get("only") is not None:
        keep = set(view["only"])
        caps = {n[0]: 0.0 for n in names[1:] if n[0] not in keep}
    # AGAINST THE MARKET, unless proven (edge_engine.against_market_gate: at
    # least 30 disagreement days with the view ahead, lower 90% bound > 0). The
    # lock's book cannot lose, so it is not a bet against anyone and is exempt.
    blocked = set()
    if params.get("against_market_gate_on", True) and not view.get("lock"):
        blocked = against_market_assets(view["probs"], book, allow)
        if blocked:
            caps = dict(caps or {}, **{a: 0.0 for a in blocked})
            out["binding"] = ["against_market"]
    if len(names) == 1 or (caps is not None and all(n[0] in caps for n in names[1:])):
        out.update(action=idle, reason_code="against_market" if blocked else "nothing_tradeable")
        return out

    # The plan's order (P5.5): the Kelly book under the strategy's own
    # constraints, times lambda, THEN the city-day rail. Capping first and
    # scaling after shrinks a bet twice: on a $1,000 ledger a 50% bucket at 35c
    # grew it by 0.0012, under h's 0.002, so nothing would ever trade.
    # Scaling a book toward cash keeps a lock a lock: W' = 1 + s (W - 1).
    # The room is the tightest of the city-day rail, the cluster rail and the
    # correlation-weighted exposure on the same date (P5.9 part 2).
    on_market = float(ledger.get("on_market_usd", 0.0))
    max_spend, room_by = risk_rails.ladder_budget(rails, equity, on_market), "city_day"
    corr_room, corr_by = city_clusters.room(rails, params.get("clusters"), view.get("city_key"), equity,
                                            on_market, ledger.get("same_day"))
    if corr_room < max_spend:
        max_spend, room_by = corr_room, corr_by
    if max_spend <= 0:
        out.update(action=idle, reason_code=f"{room_by}_full", binding=[room_by])
        return out
    solved = hs.solve_book(ladder, allow=allow, caps=caps, held=held, total_usd=equity, cash_usd=cash,
                           sds=sds, alpha=params.get("alpha", hs.ALPHA_PRIOR),
                           lock=bool(view.get("lock")), max_price=rails.get("max_price"))
    frac = hs.fractional(solved, params.get("lambda", hs.LAMBDA_PRIOR))
    out["binding"] = list(out["binding"]) + [b for b in solved["binding"] if b not in out["binding"]]
    # Drawdown scaling after lambda's own bounds, so a learned lambda at its
    # floor is still cut when the ledger is down (P5.9: lambda x max(0.25, ...)).
    dd = risk_rails.drawdown_scale(equity, ledger.get("high_water_usd"))
    out["drawdown_scale"] = dd
    if dd < 1.0:
        frac = {"weights": {k: v * dd for k, v in frac["weights"].items() if v * dd > 1e-12},
                "lambda": frac.get("lambda")}
        out["binding"].append("drawdown")
    spend = sum(frac["weights"].values())
    if spend > max_spend + 1e-12:
        s = max_spend / spend if spend > 0 else 0.0
        frac = {"weights": {k: v * s for k, v in frac["weights"].items() if v * s > 1e-12},
                "lambda": frac.get("lambda")}
        out["binding"].append(room_by)

    c0 = cash / equity
    held_frac = _held_fraction(ids, held, equity)
    ps = [post[b][0] for b in ids]
    w_now = [c0 + h for h in held_frac]
    # The solver's weights are fractions of the ledger's total wealth, drawn
    # from its free cash c0; fractional() scales them, and what is not bought
    # stays cash.
    spent = sum(frac["weights"].values())
    w_target = _book_wealth(ids, ladder, allow, frac["weights"], c0 - spent, held_frac)
    g_now, g_target = _growth(ps, w_now), _growth(ps, w_target)
    out.update(g_now=g_now, g_target=g_target)

    if view.get("lock") and min(w_target) < 1.0 - 1e-9:
        out.update(action=idle, reason_code="lock_breaks")
        return out
    if not frac["weights"] or not hs.worth_trading(g_target, g_now, params.get("h", hs.H_PRIOR)):
        out.update(action=idle, reason_code="no_trade_band")
        return out

    best = max(frac["weights"].items(), key=lambda kv: (kv[1], kv[0]))[0]
    band, side = best.rsplit(":", 1)
    q = book.get(band) or {}
    st = dict(state or {})
    st["p"] = post[band][0] if side == "YES" else 1.0 - post[band][0]
    st["ask"] = _price(q, "ask" if side == "YES" else "no_ask")
    st.setdefault("depth", _price(q, "depth_usd"))
    act, why = timing.decide(g_target - g_now, st, params.get("timing_model"),
                             c_wait=params.get("c_wait", timing.C_WAIT_PRIOR))
    out["timing"] = why
    out["g_wait"] = why.get("g_wait")
    if not act:
        out.update(action="WAIT", reason_code="timing")
        return out

    legs = []
    for name, w in frac["weights"].items():
        b, sd = name.rsplit(":", 1)
        price = _price(book.get(b), "ask" if sd == "YES" else "no_ask")
        usd = w * equity
        legs.append({"band_id": b, "side": sd, "order_type": "IOC", "limit_price": price,
                     "usd": round(usd, 2), "shares": usd / hs.effective_cost(price),
                     "depth_usd": _price(book.get(b), "depth_usd")})
    out["orders"] = paper_fill_sim.leg_order(legs)
    new_usd = sum(l["usd"] for l in legs)
    out.update(action="BUY", reason_code="enter", target_usd=round(held_usd + new_usd, 2))
    return out
