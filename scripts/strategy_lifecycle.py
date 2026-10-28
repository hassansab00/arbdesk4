"""The automatic strategy transitions (plan v2 P5.2). Runs nightly.

Two moves need nobody:

  shadow -> suspended   (and portfolio -> suspended, plan v2 P5.10)
                        when the strategy's LIVE record on its own shadow
                        ledger says it loses: at least MIN_DECISIONS settled
                        decisions, and the upper one-sided 90% bound of its
                        mean log-growth per decision below zero. A strategy
                        that might be breaking even is left alone; one that is
                        losing beyond its own noise is stopped.

  suspended -> shadow   RETEST_DAYS after it was suspended, for a re-test on
                        fresh decisions. Suspension is a pause, not a verdict.

The third automatic trigger in the plan, a fixed-rail breach, needs the rails
(P5.9, pending Hassan's approval of the numbers). It is not guessed at here.

Everything else - retirement, back to research - is a person's decision and
goes through the operator page. Into the portfolio is meta_allocator.py's
evidence gate, on Hassan's delegation (P5.10).

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
from export_paper_trades import read_existing as archived_trades

MIN_DECISIONS = 40
Z_UPPER_90 = 1.2816
RETEST_DAYS = 14


def log_growths(starting_cash, trades, opened_since=None):
    """Compound the ledger; optionally score only decisions opened in a retest.

    Earlier and late-closing old trades still affect equity. They cannot count
    as fresh evidence and immediately suspend the same strategy a second time.
    """
    equity = float(starting_cash)
    out = []
    boundary = dt.datetime.fromisoformat(opened_since.replace('Z', '+00:00')) if opened_since else None
    for t in sorted(trades, key=lambda r: (str(r["closed_at"]), str(r.get("trade_id")))):
        opened = t.get('opened_at')
        eligible = boundary is None or (opened is not None and
            dt.datetime.fromisoformat(str(opened).replace('Z', '+00:00')) >= boundary and
            dt.datetime.fromisoformat(str(t['closed_at']).replace('Z', '+00:00')) >= boundary)
        pnl = float(t.get("net_pnl") or 0.0)
        after = equity + pnl
        if equity <= 0 or after <= 0:
            # A ledger wiped out is the worst record there is; count it as a
            # very large loss rather than a math error.
            if eligible:
                out.append(-10.0)
            equity = max(after, 1e-9)
            continue
        if eligible:
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
    # A strategy in the portfolio is judged on the same record - its own shadow
    # ledger, which keeps trading beside the portfolio (P5.10) - and leaves the
    # portfolio the same way it would leave shadow.
    if state in ("shadow", "portfolio"):
        if not ledger:
            return None
        growths = log_growths(ledger["starting_cash"], trades, state_row.get('retest_since'))
        b = upper_bound(growths)
        if b is None:
            return None
        mean, hi = b
        if hi < 0:
            return ("suspended",
                    f"losing on its shadow ledger: {len(growths)} settled decisions, mean log-growth "
                    f"{mean:.5f}, upper 90% bound {hi:.5f} < 0"
                    + (f"; re-test opened since {state_row['retest_since']}" if state_row.get('retest_since') else ""))
        return None
    if state == "suspended":
        since = dt.datetime.fromisoformat(str(state_row["since"]).replace("Z", "+00:00"))
        if now - since >= dt.timedelta(days=RETEST_DAYS):
            return ("shadow", f"re-test after {RETEST_DAYS} days suspended (since {since:%Y-%m-%d})")
    return None


def main(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    states = rest("strategy_state", [("select", "strategy_id,state,since"), ("limit", "1000")])
    # The last suspension -> shadow transition anchors the fresh cohort even
    # if the strategy later changes state again (for example, promotion).
    retests = {}
    for h in rest_all('strategy_state_history', {
            'select':'history_id,strategy_id,changed_at',
            'from_state':'eq.suspended','to_state':'eq.shadow',
            'changed_at':'lte.'+now.isoformat()}, order='changed_at.asc,history_id.asc'):
        retests[h['strategy_id']] = h['changed_at']
    ledgers = {r["strategy_id"]: r for r in rest(
        "paper_accounts", [("select", "account_id,strategy_id,starting_cash"),
                           ("kind", "eq.shadow"), ("status", "neq.retired"), ("limit", "1000")])}
    archived = archived_trades()
    moves, looked = [], {}
    for s in states:
        s = dict(s, retest_since=retests.get(s['strategy_id']))
        sid = s["strategy_id"]
        led = ledgers.get(sid)
        trades = []
        if s["state"] in ("shadow", "portfolio") and led:
            trades = rest_all("paper_trades",
                              {"select": "trade_id,net_pnl,opened_at,closed_at", "account_id": f"eq.{led['account_id']}",
                               "closed_at": "lte."+now.isoformat()}, order="closed_at.asc,trade_id.asc")
            # The same archive-wins trade identity rule as the desk UI. Pruning
            # Postgres must not erase losses or restart compounding at $1,000.
            merged = {t['trade_id']:t for t in trades}
            merged.update({tid:t for tid,t in archived.items() if t.get('account_id')==led['account_id']
                and t.get('closed_at') and dt.datetime.fromisoformat(str(t['closed_at']).replace('Z','+00:00'))<=now})
            trades = list(merged.values())
        looked[sid] = {"state": s["state"], "settled": len(log_growths(led['starting_cash'], trades, s['retest_since'])) if led else 0,
                       "settled_history": len(trades), "retest_since": s['retest_since']}
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
