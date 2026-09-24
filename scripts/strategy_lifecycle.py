"""The automatic strategy transitions (plan v2 P5.2). Runs nightly.

Two moves need nobody:

  shadow -> suspended   when the strategy's LIVE record on its own shadow
                        ledger says it loses: at least MIN_DECISIONS settled
                        decisions, and the upper one-sided 90% bound of its
                        mean log-growth per decision below zero. A strategy
                        that might be breaking even is left alone; one that is
                        losing beyond its own noise is stopped.

  suspended -> shadow   RETEST_DAYS after it was suspended, for a re-test on
                        fresh decisions. Suspension is a pause, not a verdict.

The third automatic trigger in the plan, a fixed-rail breach, needs the rails
(P5.9, pending Hassan's approval of the numbers). It is not guessed at here.

Everything else - into the portfolio, retirement, back to research - is a
person's decision and goes through the operator page.

THE RECORD. A settled decision is a closed paper_trades row on the
strategy's shadow ledger. Its log-growth is log(E_after / E_before), where E
is the ledger's equity: starting cash plus every earlier closed trade's net
P&L, in closing order. So each decision is weighed by what it did to the
ledger, and a string of small losses on a shrinking ledger counts as the
compounding it is.

THE BOUND. mean + z * sd / sqrt(n), z = 1.2816 (one-sided 90%). With n >= 40
the normal approximation to the mean is sound; below 40 nothing moves.
MIN_DECISIONS, the 90% level and RETEST_DAYS are the plan's numbers.
"""
import datetime as dt
import math
import sys

from common import log_run, rest, rest_all, rpc

MIN_DECISIONS = 40
Z_UPPER_90 = 1.2816
RETEST_DAYS = 14


def log_growths(starting_cash, trades):
    """Per-decision log-growth of a ledger, from its closed trades in closing order."""
    equity = float(starting_cash)
    out = []
    for t in sorted(trades, key=lambda r: (str(r["closed_at"]), str(r.get("trade_id")))):
        pnl = float(t.get("net_pnl") or 0.0)
        after = equity + pnl
        if equity <= 0 or after <= 0:
            # A ledger wiped out is the worst record there is; count it as a
            # very large loss rather than a math error.
            out.append(-10.0)
            equity = max(after, 1e-9)
            continue
        out.append(math.log(after / equity))
        equity = after
    return out


def upper_bound(growths):
    """(mean, one-sided 90% upper bound) of the mean log-growth, or None under MIN_DECISIONS."""
    n = len(growths)
    if n < MIN_DECISIONS:
        return None
    mean = sum(growths) / n
    var = sum((g - mean) ** 2 for g in growths) / (n - 1)
    return mean, mean + Z_UPPER_90 * math.sqrt(var / n)


def decide(state_row, ledger, trades, now):
    """The move for one strategy, or None: (to_state, reason)."""
    state = state_row["state"]
    if state == "shadow":
        if not ledger:
            return None
        b = upper_bound(log_growths(ledger["starting_cash"], trades))
        if b is None:
            return None
        mean, hi = b
        if hi < 0:
            return ("suspended",
                    f"losing on its shadow ledger: {len(trades)} settled decisions, mean log-growth "
                    f"{mean:.5f}, upper 90% bound {hi:.5f} < 0")
        return None
    if state == "suspended":
        since = dt.datetime.fromisoformat(str(state_row["since"]).replace("Z", "+00:00"))
        if now - since >= dt.timedelta(days=RETEST_DAYS):
            return ("shadow", f"re-test after {RETEST_DAYS} days suspended (since {since:%Y-%m-%d})")
    return None


def main(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    states = rest("strategy_state", [("select", "strategy_id,state,since"), ("limit", "1000")])
    ledgers = {r["strategy_id"]: r for r in rest(
        "paper_accounts", [("select", "account_id,strategy_id,starting_cash"),
                           ("kind", "eq.shadow"), ("status", "neq.retired"), ("limit", "1000")])}
    moves, looked = [], {}
    for s in states:
        sid = s["strategy_id"]
        led = ledgers.get(sid)
        trades = []
        if s["state"] == "shadow" and led:
            trades = rest_all("paper_trades",
                              {"select": "trade_id,net_pnl,closed_at", "account_id": f"eq.{led['account_id']}",
                               "closed_at": "not.is.null"}, order="closed_at.asc,trade_id.asc")
        looked[sid] = {"state": s["state"], "settled": len(trades)}
        move = decide(s, led, trades, now)
        if move:
            to, why = move
            rpc("set_strategy_state", {"p_strategy_id": sid, "p_state": to, "p_reason": why})
            moves.append({"strategy_id": sid, "from": s["state"], "to": to, "reason": why})
            print(f"  {sid}: {s['state']} -> {to} ({why})")
    log_run("strategy_lifecycle", "ok", len(moves), {"moves": moves, "looked_at": looked})
    print(f"strategy lifecycle: {len(moves)} move(s) over {len(states)} strategies")
    return moves


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
