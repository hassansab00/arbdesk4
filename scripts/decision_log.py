"""The decision log (plan v2 P5.11): one row per (run, strategy, city-day).

signal_engine calls build() after it has written the run's signals, with
everything the run knew about what happened to each one, and writes the rows
to `decisions`. A city-day a strategy looked at and passed on is a row too
(NONE / no_signal) - that is the point: the record of what a strategy chose
not to do is what separates its judgement from its coverage.

THE ACTION, per strategy and city-day, from the strongest thing that happened:

  SELL  exit             an EXIT signal was written
  BUY   enter            an ENTER signal with a size above zero was written
  HOLD  already_decided  its signal repeated one written inside the TTL
                         (the earlier decision stands; nothing new is sent)
  WAIT  no_cost_version  its entry was held: the run had no cost version
  NONE  conflict         its entry was blocked by the conflict rules
  NONE  sized_to_zero    it fired, and the sizing gave it nothing
  HOLD  holding          no signal, and its ledger holds the city-day
  NONE  no_signal        no signal, nothing held

g_now / g_wait and binding stay empty here: nothing in this engine computes
them yet. The timing rule (P5.6) and the solver (P5.5, live at P5.12) fill
them; a number this module does not have is not written.
"""
from collections import defaultdict

OUTCOMES = ("written", "deduped", "held_no_cost_version", "blocked")


def _usd(sig):
    shares = float(getattr(sig, "suggested_shares", 0) or 0)
    price = float(getattr(sig, "price_at_fire", 0) or 0)
    return shares * price


def verdict(outcomes, held_usd):
    """(action, reason_code) for one strategy and city-day.

    outcomes: [(signal, outcome)] with outcome one of OUTCOMES.
    """
    written = [s for s, o in outcomes if o == "written"]
    if any(s.action == "EXIT" for s in written):
        return "SELL", "exit"
    if any(s.action == "ENTER" and _usd(s) > 0 for s in written):
        return "BUY", "enter"
    kinds = {o for _s, o in outcomes}
    if "deduped" in kinds:
        return "HOLD", "already_decided"
    if "held_no_cost_version" in kinds:
        return "WAIT", "no_cost_version"
    if "blocked" in kinds:
        return "NONE", "conflict"
    if written:
        return "NONE", "sized_to_zero"
    if held_usd > 0:
        return "HOLD", "holding"
    return "NONE", "no_signal"


def build(run_id, decided_at, strategy_ids, city_days, outcomes, band_city_day, held, params_version):
    """The run's decision rows.

    strategy_ids   the strategies that ran
    city_days      {(city_key, resolution_date)} on the board this run
    outcomes       [(signal, outcome)] for every signal the run produced
    band_city_day  {band_id: (city_key, resolution_date)}
    held           {(strategy_id, city_key, resolution_date): usd at cost}
    """
    by_key = defaultdict(list)
    unmapped = 0
    for sig, outcome in outcomes:
        cd = band_city_day.get(str(sig.band_id))
        if cd is None:
            unmapped += 1
            continue
        by_key[(sig.strategy_id, cd[0], str(cd[1]))].append((sig, outcome))
    keys = {(sid, c, str(d)) for sid in strategy_ids for c, d in city_days}
    keys |= set(by_key)
    keys |= {k for k, v in held.items() if v > 0 and k[0] in set(strategy_ids)}
    rows = []
    for sid, city, date in sorted(keys):
        outs = by_key.get((sid, city, date), [])
        held_usd = float(held.get((sid, city, date), 0.0))
        action, reason = verdict(outs, held_usd)
        target = sum(_usd(s) for s, o in outs if o == "written" and s.action == "ENTER")
        rows.append({
            "run_id": run_id, "decided_at": decided_at.isoformat(),
            "strategy_id": sid, "city_key": city, "resolution_date": date,
            "action": action, "reason_code": reason,
            "target_usd": round(held_usd + target, 2) if action == "BUY" else round(held_usd, 2),
            "held_usd": round(held_usd, 2),
            "n_signals": len(outs), "params_version": params_version,
        })
    return rows, unmapped


def held_by_city_day(positions, ledger_strategy, band_city_day):
    """{(strategy_id, city_key, date): cost basis} from the ledgers' open positions."""
    out = defaultdict(float)
    for p in positions:
        sid = ledger_strategy.get(str(p.get("account_id")))
        cd = band_city_day.get(str(p.get("band_id")))
        if sid is None or cd is None:
            continue
        out[(sid, cd[0], str(cd[1]))] += float(p.get("cost_basis") or 0)
    return dict(out)
