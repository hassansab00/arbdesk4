"""The meta-allocator for the portfolio account (plan v2 P5.10). Runs nightly.

DECIDED 24 Sep: Hassan delegated the P5.10 decision to Claude ("do what's the
most optimal option"). The numbers are the plan's suggestions and live in
settings 'portfolio_gate' (20260924120000_portfolio_by_evidence.sql).

THREE STEPS, each only on a strategy's OWN shadow ledger - never on the
portfolio's fills, which are an example of the allocation, not evidence for it.

1. THE GATE. A strategy in shadow is promoted into the portfolio state when
   its ledger shows all four:
     * at least min_settled_dates (30) distinct settled dates;
     * at least min_decisions (60) settled decisions;
     * a positive lower one-sided 80% bound on mean log-growth per dollar;
     * no single city-day making more than max_city_day_share (25%) of the gain.
   The promotion goes through promote_strategy_to_portfolio() with the
   delegated approver on record.

2. ACTIVATION. The first time any strategy is in the portfolio state, the
   suspended portfolio account is activated at the gate's bankroll ($10,000
   paper) through activate_portfolio_account(). Never by date.

3. THE ALLOCATION. For each portfolio-state strategy, a Normal-inverse-gamma
   posterior of its log-growth per dollar, per regime (prior mean 0), mixed by
   how often it trades in each regime. One Thompson draw per strategy; weight
   proportional to max(draw, 0), water-filled under the 40% cap, floor 0.

LOG-GROWTH PER DOLLAR. A decision staking s out of ledger equity E, moving it
to E', is g = log(E'/E) / (s/E): the ledger's log-growth divided by the
fraction of it at risk. For a small stake it is the return per dollar; it
stays finite on a total loss (log(1-f)/f), which a plain log(1 + pnl/s) would
not. Decisions are taken in closing order, as strategy_lifecycle does.

RULE 11 (adaptive never means unbounded). The learned quantity is each
posterior. Prior: mean 0, worth PRIOR_KAPPA decisions, variance scale
PRIOR_BETA/(PRIOR_ALPHA-1) = 1 per dollar squared. Bounds: weights in
[0, min(cap, HARD_CAP)], sum at most 1. Minimum sample: nothing is allocated
before the gate's 60 decisions, and a regime with fewer than MIN_REGIME uses
the strategy's pooled posterior. Maximum change: a weight moves at most
MAX_STEP a night (a strategy that has left the portfolio state goes to 0 at
once - that is a lifecycle decision, not a learned one). Version: every
allocation carries ALLOCATION_VERSION and the date, is written into the
portfolio policy, and paper_plans copies it into every plan's evidence.
Not evaluated on its own data: the posterior is fitted on the shadow ledger;
its test is the portfolio account's later fills.
"""
import datetime as dt
import math
import random
import sys
from collections import defaultdict

from common import log_run, rest, rest_all, rpc

ALLOCATION_VERSION = "thompson-v1"
APPROVER = "Hassan (delegated to Claude, 24 Sep)"
Z_LOWER_80 = 0.8416
PRIOR_MU, PRIOR_KAPPA, PRIOR_ALPHA, PRIOR_BETA = 0.0, 10.0, 2.0, 1.0
MIN_REGIME = 20
MAX_STEP = 0.10
HARD_CAP = 0.40
GATE_DEFAULTS = {"bankroll_usd": 10000, "min_settled_dates": 30, "min_decisions": 60,
                 "lower_bound_level": 0.80, "max_city_day_share": 0.25, "cap_per_strategy": 0.40}
TRADE_COLUMNS = ("trade_id,net_pnl,closed_at,shares,avg_fill_price,fee_paid,gas_paid,"
                 "regime_label,city_key,resolution_date")


def _num(x):
    return float(x) if x is not None else 0.0


def decisions(starting_cash, trades):
    """One record per settled decision on a ledger, in closing order."""
    equity = float(starting_cash)
    out = []
    for t in sorted(trades, key=lambda r: (str(r["closed_at"]), str(r.get("trade_id")))):
        pnl = _num(t.get("net_pnl"))
        stake = _num(t.get("shares")) * _num(t.get("avg_fill_price")) + _num(t.get("fee_paid")) + _num(t.get("gas_paid"))
        if stake <= 0 or equity <= 0:
            equity += pnl
            continue
        f = min(stake / equity, 1.0)
        after = equity + pnl
        g = math.log(max(after, equity * 1e-9) / equity) / f
        out.append({"g": g, "pnl": pnl, "regime": t.get("regime_label") or "UNKNOWN",
                    "city": t.get("city_key"),
                    "date": str(t.get("resolution_date") or str(t["closed_at"])[:10])})
        equity = after
    return out


def gate(decs, cfg):
    """(passed, stats, failures) for one strategy's shadow record."""
    n = len(decs)
    dates = len({d["date"] for d in decs})
    stats = {"decisions": n, "settled_dates": dates}
    failures = []
    if n < cfg["min_decisions"]:
        failures.append(f"{n} decisions < {cfg['min_decisions']}")
    if dates < cfg["min_settled_dates"]:
        failures.append(f"{dates} settled dates < {cfg['min_settled_dates']}")
    if n >= 2:
        gs = [d["g"] for d in decs]
        mean = sum(gs) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in gs) / (n - 1))
        lower = mean - Z_LOWER_80 * sd / math.sqrt(n)
        stats.update(mean_g=round(mean, 6), lower_80=round(lower, 6))
        if lower <= 0:
            failures.append(f"lower 80% bound {lower:.5f} <= 0")
    else:
        failures.append("fewer than 2 decisions")
    gain = sum(d["pnl"] for d in decs)
    stats["gain_usd"] = round(gain, 2)
    if gain <= 0:
        failures.append(f"no gain ({gain:.2f})")
    else:
        by_cd = defaultdict(float)
        for d in decs:
            by_cd[(d["city"], d["date"])] += d["pnl"]
        (top_city, top_date), top = max(by_cd.items(), key=lambda kv: kv[1])
        share = top / gain
        stats["top_city_day"] = {"city": top_city, "date": top_date, "share": round(share, 4)}
        if share > cfg["max_city_day_share"]:
            failures.append(f"{top_city} {top_date} made {share:.0%} of the gain > {cfg['max_city_day_share']:.0%}")
    return (not failures), stats, failures


def posterior(gs):
    """Normal-inverse-gamma posterior (mu, kappa, alpha, beta) of the mean log-growth."""
    n = len(gs)
    if n == 0:
        return PRIOR_MU, PRIOR_KAPPA, PRIOR_ALPHA, PRIOR_BETA
    xbar = sum(gs) / n
    ss = sum((x - xbar) ** 2 for x in gs)
    kappa = PRIOR_KAPPA + n
    mu = (PRIOR_KAPPA * PRIOR_MU + n * xbar) / kappa
    alpha = PRIOR_ALPHA + n / 2
    beta = PRIOR_BETA + ss / 2 + PRIOR_KAPPA * n * (xbar - PRIOR_MU) ** 2 / (2 * kappa)
    return mu, kappa, alpha, beta


def draw_mean(post, rng):
    mu, kappa, alpha, beta = post
    sigma2 = beta / rng.gammavariate(alpha, 1.0)
    return rng.gauss(mu, math.sqrt(sigma2 / kappa))


def strategy_draw(decs, rng):
    """One Thompson draw of a strategy's log-growth per dollar, mixed over its regimes."""
    if not decs:
        return draw_mean(posterior([]), rng)
    pooled = posterior([d["g"] for d in decs])
    by_regime = defaultdict(list)
    for d in decs:
        by_regime[d["regime"]].append(d["g"])
    total = 0.0
    for regime in sorted(by_regime):
        gs = by_regime[regime]
        post = posterior(gs) if len(gs) >= MIN_REGIME else pooled
        total += len(gs) / len(decs) * draw_mean(post, rng)
    return total


def capped_weights(draws, cap):
    """Weights proportional to max(draw, 0), each at most cap, summing to at most 1."""
    pos = {s: max(d, 0.0) for s, d in draws.items()}
    w = {s: 0.0 for s in draws}
    free = {s for s, d in pos.items() if d > 0}
    mass = 1.0
    while free:
        total = sum(pos[s] for s in free)
        over = {s for s in free if mass * pos[s] / total > cap}
        if not over:
            for s in free:
                w[s] = mass * pos[s] / total
            break
        for s in over:
            w[s] = cap
        mass -= cap * len(over)
        free -= over
        if mass <= 1e-12:
            break
    return w


def step_limited(target, previous, max_step=MAX_STEP):
    """Each weight within max_step of last night's; the total never above 1."""
    out = {}
    for s, t in target.items():
        p = float(previous.get(s, 0.0))
        out[s] = min(max(t, p - max_step, 0.0), p + max_step)
    total = sum(out.values())
    if total > 1.0:
        ups = {s: out[s] - float(previous.get(s, 0.0)) for s in out if out[s] > float(previous.get(s, 0.0))}
        excess = total - 1.0
        room = sum(ups.values())
        for s, u in ups.items():
            out[s] -= excess * u / room
    # Rounded DOWN to 4 places, so the cap and the total still hold; the inner
    # round() keeps float noise (0.35 - 0.10 = 0.2499999...) from costing a step.
    return {s: math.floor(round(v * 10000, 6)) / 10000 for s, v in out.items()}


def allocate(records, previous, cap, seed):
    """{strategy: weight} for the portfolio-state strategies in records."""
    rng = random.Random(seed)
    draws = {s: strategy_draw(records[s], rng) for s in sorted(records)}
    target = capped_weights(draws, cap)
    return step_limited(target, previous), draws


def load_gate():
    rows = rest("settings", [("select", "value"), ("key", "eq.portfolio_gate")])
    cfg = dict(GATE_DEFAULTS)
    if rows and isinstance(rows[0].get("value"), dict):
        cfg.update({k: v for k, v in rows[0]["value"].items() if k in GATE_DEFAULTS})
    cfg["cap_per_strategy"] = min(float(cfg["cap_per_strategy"]), HARD_CAP)
    return cfg


def main(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = load_gate()
    states = {r["strategy_id"]: r["state"] for r in
              rest("strategy_state", [("select", "strategy_id,state"), ("limit", "1000")])}
    ledgers = {r["strategy_id"]: r for r in rest(
        "paper_accounts", [("select", "account_id,strategy_id,starting_cash"),
                           ("kind", "eq.shadow"), ("status", "neq.retired"), ("limit", "1000")])}
    pf = rest("paper_accounts", [("select", "account_id,status,policy"), ("kind", "eq.portfolio"),
                                 ("status", "neq.retired"), ("limit", "1")])
    pf = pf[0] if pf else None

    records, looked, promoted = {}, {}, []
    for sid, state in sorted(states.items()):
        led = ledgers.get(sid)
        if state not in ("shadow", "portfolio") or not led:
            continue
        trades = rest_all("paper_trades", {"select": TRADE_COLUMNS, "account_id": f"eq.{led['account_id']}",
                                           "closed_at": "not.is.null"}, order="closed_at.asc,trade_id.asc")
        decs = decisions(led["starting_cash"], trades)
        passed, stats, failures = gate(decs, cfg)
        looked[sid] = {"state": state, **stats, "failures": failures}
        if state == "shadow" and passed:
            why = (f"P5.10 gate on its shadow ledger: {stats['decisions']} decisions over "
                   f"{stats['settled_dates']} dates, lower 80% bound {stats['lower_80']} > 0, "
                   f"top city-day {stats['top_city_day']['share']:.0%} of the gain")
            rpc("promote_strategy_to_portfolio", {"p_strategy_id": sid, "p_approved_by": APPROVER, "p_reason": why})
            promoted.append(sid)
            print(f"  {sid}: shadow -> portfolio ({why})")
            state = "portfolio"
        if state == "portfolio":
            records[sid] = decs

    status, detail = "ok", {"promoted": promoted, "looked_at": looked}
    if records and pf and pf["status"] != "active":
        try:
            res = rpc("activate_portfolio_account", {
                "p_bankroll": cfg["bankroll_usd"], "p_approved_by": APPROVER,
                "p_reason": f"first strategies through the P5.10 gate: {', '.join(sorted(records))}"})
            detail["activated"] = res
            pf["status"] = "active"
            pf["policy"] = {}
            print(f"  portfolio account activated at ${cfg['bankroll_usd']:,}")
        except Exception as e:                          # noqa: BLE001 - reported, not swallowed
            status, detail["activation_error"] = "attention", str(e)[:400]
            print(f"  portfolio activation refused: {e}", file=sys.stderr)

    if pf and pf["status"] == "active":
        previous = (pf.get("policy") or {}).get("allocation") or {}
        version = f"{ALLOCATION_VERSION}:{now:%Y-%m-%d}"
        weights, draws = allocate(records, previous, cfg["cap_per_strategy"], seed=version)
        rpc("set_portfolio_allocation", {"p_weights": weights, "p_version": version})
        detail.update(allocation=weights, draws={s: round(d, 6) for s, d in draws.items()}, version=version)
        for s, w in sorted(weights.items()):
            print(f"  weight {w:.4f}  {s} (draw {draws[s]:+.5f})")

    log_run("meta_allocator", status, len(promoted) + len(detail.get("allocation", {})), detail)
    print(f"meta allocator: {len(promoted)} promoted, {len(records)} in the portfolio state, "
          f"portfolio {pf['status'] if pf else 'missing'}")
    return detail


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
