"""S10, the max-temperature winner (plan v2 P7.5): a view plus constraints.

S10 holds, per city-day, the YES of the one bucket it expects to win, and
moves only when the evidence says it should. It has no thresholds of its own:

  view         the remaining-day ladder (P7.2, `s10_shadow_checkpoints.probs`)
               through the belief layer (P5.3) -> p_post, p_sd per bucket;
  constraints  YES only; at most one bucket per city-day (s10_lock excepted,
               below); no bucket the day has already ruled out; only a bucket
               the platform calls tradeable (edge_engine.classify_tradeability:
               not a dead book, ask at least the YES minimum);
  switching    only when the growth gained beats the cost of leaving the held
               bucket plus h_switch (prior 0.005, bounds [0.001, 0.02]);
  lost         a held bucket the station's floor has ruled out is sold at the
               bid at once, whatever else is true.

Three variants, each its own strategy and shadow ledger once wired, so the
evidence decides between them:

  s10_winner   the most probable bucket (Hassan's thesis in its pure form);
  s10_growth   the bucket whose YES grows the ledger most - the second most
               likely when it is much cheaper (holdings_solver.best_single_bucket);
  s10_lock     anchored on the most probable bucket, and it trades only a book
               that cannot end below what it cost whatever bucket wins (the old
               S6 equal-shares insurance, holdings_solver.solve_book(lock=True)).

PART 1 IS THE DECISION ALONE. `decide()` is pure: every input is an argument,
nothing is read or written. It is not in strategies.REGISTRY and no strategies
row exists for it, so nothing live calls it. Shadow trading (P7.6) needs the one
engine for live and replay (P5.12) - the tick calling belief -> solver ->
timing -> order - and a consumer for SELL and SWITCH, which the paper path does
not have today (paper_plans reads ENTER signals only).

Units. Every growth here is expected log-growth per unit of the ledger's wealth
at the Kelly stake, fee-inclusive (holdings_solver.single_bucket_growth), so the
entry cost of a bucket is already inside its growth (holdings_solver.worth_trading
says the same). The exit cost is the wealth given up by selling the held shares
at the net bid rather than holding them at their posterior value.
"""
import holdings_solver as hs
import market_state
from belief import ladder_posterior
from edge_engine import DEFAULT_MIN_PRICE_YES, classify_tradeability
from execution_cost import fee_per_share
from probability_engine import TRAJECTORY_MAX_READING_AGE_MIN, impossible_band_ids

VARIANTS = ("s10_winner", "s10_growth", "s10_lock")
VIEW_VERSION = "s10-view-v1"

H_SWITCH_PRIOR = 0.005
H_SWITCH_BOUNDS = (0.001, 0.02)
# A reading older than this describes another hour: the engine's own limit for
# a live reading (probability_engine.TRAJECTORY_MAX_READING_AGE_MIN).
MAX_READING_AGE_MIN = TRAJECTORY_MAX_READING_AGE_MIN
# A floor built from a thermometer: the station observation series, or a live
# row that is a station's. A model value is never a floor (P2.7).
STATION_BASES = ("station", "series")


def h_switch(value=None):
    """h_switch clipped to its bounds; the prior when nothing was learned."""
    lo, hi = H_SWITCH_BOUNDS
    return min(max(H_SWITCH_PRIOR if value is None else float(value), lo), hi)


def view(probs, table=None, cluster=None, checkpoint=None):
    """{band_id: (p_post, p_sd)}: the ladder through the belief layer. The same
    for every variant - they differ only in constraints."""
    return ladder_posterior(probs, table, cluster, checkpoint)


def readings_usable(floor_basis, reading_age_min):
    """(ok, reason). S10 buys and switches only on a fresh station reading."""
    if floor_basis not in STATION_BASES:
        return False, "no station reading under the floor"
    if reading_age_min is None or float(reading_age_min) > MAX_READING_AGE_MIN:
        return False, "the station reading is stale"
    return True, None


def ruled_out(floor_c, floor_basis, unit, bands):
    """Band ids the day can no longer settle in (P3.1, venue-rounded), judged
    on a station floor only."""
    if floor_c is None or floor_basis not in STATION_BASES:
        return set()
    return impossible_band_ids(float(floor_c), unit, bands)


def buyable(book, band_id, min_price_yes=DEFAULT_MIN_PRICE_YES):
    """(ok, reason): the platform's own YES tradeability for this bucket's book.

    On the real 26 Sep noon ladders Tokyo's book had 23-24 C at 0.998 (it won)
    and 24-25 C at 0.003 with no bid; the engine gave 24-25 C 0.34, so its
    "growth" at 0.003 was enormous and entirely illusory. A dead book is the
    market saying the day is settled. The far-from-forecast rule is left out:
    it measures distance from the day-ahead centre, and S10's ladder is its own.
    """
    q = (book or {}).get(band_id) or {}
    ask, bid = q.get("ask"), q.get("bid")
    state = market_state.classify(None if bid is None else float(bid), None if ask is None else float(ask))
    return classify_tradeability("YES", None if ask is None else float(ask), 0, state,
                                 min_price_yes=min_price_yes)


def _ask(book, band_id):
    q = (book or {}).get(band_id) or {}
    return q.get("ask")


def _net_bid(book, band_id):
    q = (book or {}).get(band_id) or {}
    bid = q.get("bid")
    if bid is None or float(bid) <= 0:
        return None
    return float(bid) - fee_per_share(float(bid))


def most_probable(post, candidates):
    """The candidate with the highest p_post; ties go to the lower band id."""
    ranked = sorted(candidates, key=lambda b: (-post[b][0], b))
    return ranked[0] if ranked else None


def target(variant, post, book, candidates, min_price_yes=DEFAULT_MIN_PRICE_YES):
    """(band_id, growth, why) the variant would hold now; (None, 0.0, why) if none.

    s10_winner and s10_lock want the most probable possible bucket and nothing
    else: if its book is not tradeable they want nothing. s10_growth picks the
    best growth among the tradeable ones."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown S10 variant {variant!r}")
    if variant == "s10_growth":
        ladder = [{"id": b, "p": post[b][0], "yes_price": _ask(book, b)}
                  for b in sorted(candidates) if buyable(book, b, min_price_yes)[0]]
        best, g = hs.best_single_bucket(ladder)
        return best, g, None if best else "no tradeable bucket grows the ledger"
    top = most_probable(post, candidates)
    if top is None:
        return None, 0.0, "every bucket is ruled out"
    ok, why = buyable(book, top, min_price_yes)
    if not ok:
        return None, 0.0, f"the top bucket is not tradeable ({why})"
    return top, hs.single_bucket_growth(post[top][0], _ask(book, top)), None


def _lock_book(post, book, bands, anchor, held_shares, ledger_usd, cash_usd):
    """The best book over the WHOLE ladder that cannot end below the ledger's
    wealth, if one holds the anchor; else None. Every bucket stays in: the lock
    is about what the venue pays, not what our reading rules out."""
    ids = [b["band_id"] for b in bands]
    if any(_ask(book, b) is None for b in ids):
        return None                      # a bucket without an ask: no equal-shares book
    ladder = [{"id": b, "p": post[b][0], "yes_price": _ask(book, b), "no_price": None} for b in ids]
    out = hs.solve_book(ladder, allow=("YES",), held=held_shares, total_usd=ledger_usd,
                        cash_usd=cash_usd, lock=True)
    if f"{anchor}:YES" not in out["weights"]:     # holdings_solver.assets names
        return None
    if min(out["wealth_by_outcome"].values()) < 1.0 - 1e-9:
        return None
    return out


def decide(variant, *, bands, unit, probs, book, floor_c=None, floor_basis=None,
           reading_age_min=None, held=None, ledger_usd=1.0, cash_usd=None,
           belief_table=None, cluster=None, checkpoint=None, h=None,
           min_price_yes=DEFAULT_MIN_PRICE_YES):
    """One S10 decision for one city-day.

    bands     the ladder in venue order: dicts with band_id, band_lo, band_hi,
              open_low, open_high.
    probs     {band_id: p} from the remaining-day model.
    book      {band_id: {"ask": .., "bid": ..}} at the decision.
    floor_c / floor_basis / reading_age_min
              the day's running maximum, what it rests on, and how old the
              newest station reading is.
    held      None, or {"band_id": .., "shares": ..} - the one bucket held.
    ledger_usd, cash_usd
              the ledger's wealth and the part free to spend (s10_lock sizes on them).

    Returns a dict with action in BUY / SELL / SWITCH / HOLD / WAIT / NONE (the
    decision log's actions, plus SWITCH, P7.5), the target, the posterior, and
    traded_is_top: whether what it trades is the predicted top bucket, so
    accuracy and trading stay separable.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown S10 variant {variant!r}")
    ids = [b["band_id"] for b in bands]
    post = view({b: probs.get(b, 0.0) for b in ids}, belief_table, cluster, checkpoint)
    top = most_probable(post, ids)
    lost = ruled_out(floor_c, floor_basis, unit, bands)
    candidates = [b for b in ids if b not in lost]
    hs_ = h_switch(h)
    out = {"variant": variant, "view_version": VIEW_VERSION, "h_switch": hs_,
           "p_post": {b: post[b][0] for b in ids}, "p_sd": {b: post[b][1] for b in ids},
           "predicted_top": top, "ruled_out": sorted(lost), "held": (held or {}).get("band_id"),
           "target": None, "growth": 0.0, "action": "NONE", "reason": None, "book": None,
           "traded_is_top": None}

    held_id = (held or {}).get("band_id")
    if held_id is not None and held_id in lost:
        out.update(action="SELL", target=None, reason="the held bucket is certainly lost: sell at the bid",
                   traded_is_top=(held_id == top))
        return out

    ok, why = readings_usable(floor_basis, reading_age_min)
    if not ok:
        out.update(action="WAIT" if held_id is None else "HOLD", reason=why)
        return out

    goal, g, why_not = target(variant, post, book, candidates, min_price_yes)
    out.update(target=goal, growth=g)

    if variant == "s10_lock":
        anchor = goal
        if held_id is not None:
            # A lock book already ends at or above its cost whatever wins; moving
            # its anchor is P5.12's (the whole book re-solved), not part 1's.
            out.update(action="HOLD", target=anchor, reason="a lock book is held")
            return out
        if anchor is None:
            out.update(action="NONE", reason=why_not)
            return out
        book_out = _lock_book(post, book, bands, anchor, None, ledger_usd, cash_usd)
        if book_out is None or book_out["growth"] <= 0:
            out.update(action="NONE", target=anchor, reason="no book that cannot lose holds the top bucket")
            return out
        out.update(action="BUY", target=anchor, growth=book_out["growth"], book=book_out["weights"],
                   reason="a book that cannot lose, anchored on the top bucket",
                   traded_is_top=(anchor == top))
        return out

    if held_id is None:
        if goal is None or g <= 0:
            out.update(action="NONE", reason=why_not or "the target's YES does not grow the ledger at its ask")
            return out
        out.update(action="BUY", reason="enter the target", traded_is_top=(goal == top))
        return out

    if goal is None or goal == held_id:
        out.update(action="HOLD", reason="the held bucket is still the target", traded_is_top=(held_id == top))
        return out

    p_held = post[held_id][0]
    g_held = hs.single_bucket_growth(p_held, _ask(book, held_id))
    net_bid = _net_bid(book, held_id)
    if net_bid is None:
        out.update(action="HOLD", reason="no bid to leave the held bucket", traded_is_top=(held_id == top))
        return out
    shares = float((held or {}).get("shares") or 0.0)
    exit_cost = max(0.0, shares * (p_held - net_bid)) / float(ledger_usd)
    gain = g - g_held
    out.update(gain=gain, exit_cost=exit_cost, g_held=g_held)
    if gain > exit_cost + hs_:
        out.update(action="SWITCH", reason="the growth gained beats the exit cost and h_switch",
                   traded_is_top=(goal == top))
    else:
        out.update(action="HOLD", reason="inside the switching hysteresis", traded_is_top=(held_id == top))
    return out
