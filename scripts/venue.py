"""How the venue reads a temperature (plan v2 P2.2, P5.0 item 6).

A market settles on the WHOLE degree in its own unit, rounded half away from
zero - the rule sql/ad4_82 venue_round() applies in the database (PostgreSQL's
round() on a numeric). Every "which bucket holds this value" question has to
ask it of that whole degree, not of the raw reading: buckets are half-open
[lo, hi) with whole-degree edges, so 23.6 C is in the 23 C bucket by its raw
value and in the 24 C bucket by the venue's, and the venue decides.

Kept free of imports so the strategies, the exit rules and the probability
engine can all share the one definition.
"""
import math


def venue_read(value_local):
    """The whole degree the venue reads for a value already in the market's unit.

    Half away from zero, not Python's round() (half to even). Cut to six places
    first, so a float such as 77.49999999 cannot fall on the wrong side of a
    boundary the numeric column never had.
    """
    if value_local is None:
        return None
    v = round(float(value_local), 6)
    return math.copysign(math.floor(abs(v) + 0.5), v)


def venue_round(value_c, unit):
    """The whole degree the venue reads for a Celsius value, in the market's unit."""
    if value_c is None:
        return None
    # float first: Decimal * 9.0 raises (paper_exits.to_band_unit, 29 Sep)
    value_c = float(value_c)
    return venue_read(value_c * 9.0 / 5.0 + 32.0 if unit == "F" else value_c)
