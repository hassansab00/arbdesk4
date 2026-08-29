"""
AD4 market state classification (spec §2.4).

Price is checked BEFORE sides. A band at ask 0.001 with no bids is dead,
not "one-sided" - the reverse ordering mislabelled 190 of 231 rows when
this was gotten wrong earlier in the project.

**These thresholds are provisional Claude placeholders, not measurements.**
They are UI-settable and must be labelled as such wherever surfaced.
"""

DEAD_LOSER_MAX_ASK = 0.02
DEAD_WINNER_MIN_BID = 0.96
WIDE_SPREAD_MIN = 0.10


def classify(best_bid, best_ask):
    """
    best_bid / best_ask: float or None. Returns one of
    NO_BOOK / DEAD_LOSER / DEAD_WINNER / ONE_SIDED / WIDE / LIVE.
    """
    has_bid = best_bid is not None
    has_ask = best_ask is not None

    if not has_bid and not has_ask:
        return "NO_BOOK"

    # price checked before sides
    if has_ask and best_ask <= DEAD_LOSER_MAX_ASK:
        return "DEAD_LOSER"
    if has_bid and best_bid >= DEAD_WINNER_MIN_BID:
        return "DEAD_WINNER"

    if not has_bid or not has_ask:
        return "ONE_SIDED"

    spread = best_ask - best_bid
    if spread > WIDE_SPREAD_MIN:
        return "WIDE"

    return "LIVE"
