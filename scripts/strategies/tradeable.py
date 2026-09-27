"""What a view-and-constraints strategy may buy (plan v2 P8.2), shared so each
strategy states its constraint instead of re-deriving it.

  ruled_out    buckets the day can no longer settle in, judged on a station
               floor only (P3.1, venue-rounded; a model value is never a floor)
  buyable      the platform's own tradeability for one side of one bucket
               (edge_engine.classify_tradeability: no dead book, and for YES
               an ask of at least settings.tradeability_yes's minimum). The
               far-from-forecast rule is left out: it measures distance from
               the day-ahead centre, and a view is its own centre.

On the real 26 Sep noon ladders Tokyo's book had 23-24 C at 0.998 (it won) and
24-25 C at 0.003 with no bid while the engine gave 24-25 C 0.34: a dead book is
the market saying the day is settled, and a model that disagrees sees a growth
there that is not real (P7.5). S2 is the exception - an arbitrage needs every
leg, dead ones included - and says so in its own file.
"""
import market_state
from edge_engine import DEFAULT_MIN_PRICE_YES, classify_tradeability
from probability_engine import TRAJECTORY_MAX_READING_AGE_MIN, impossible_band_ids

# A floor built from a thermometer: the station observation series, or a live
# row that is a station's.
STATION_BASES = ("station", "series")
MAX_READING_AGE_MIN = TRAJECTORY_MAX_READING_AGE_MIN


def _f(v):
    return None if v is None else float(v)


def ruled_out(floor_c, floor_basis, unit, bands):
    if floor_c is None or floor_basis not in STATION_BASES:
        return set()
    return impossible_band_ids(float(floor_c), unit, bands)


def readings_usable(floor_basis, reading_age_min):
    """(ok, reason): a fresh station reading under the day's floor."""
    if floor_basis not in STATION_BASES:
        return False, "no station reading under the floor"
    if reading_age_min is None or float(reading_age_min) > MAX_READING_AGE_MIN:
        return False, "the station reading is stale"
    return True, None


def no_price(q):
    """The NO side's price: its own ask, else 1 - the YES bid (the plan's, P8.2)."""
    q = q or {}
    if q.get("no_ask") is not None:
        return _f(q["no_ask"])
    bid = _f(q.get("bid"))
    return None if bid is None else 1 - bid


def buyable(book, band_id, side="YES", min_price_yes=DEFAULT_MIN_PRICE_YES):
    """(ok, reason) for buying `side` of this bucket at the book's price."""
    q = (book or {}).get(band_id) or {}
    ask, bid = _f(q.get("ask")), _f(q.get("bid"))
    state = market_state.classify(bid, ask)
    price = ask if side == "YES" else no_price(q)
    return classify_tradeability(side, price, 0, state, min_price_yes=min_price_yes)
