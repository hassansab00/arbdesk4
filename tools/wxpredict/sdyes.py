"""The same-day YES pilot's entry rule (docs/SAME_DAY_YES_PREREG.md).

Plain Python, so CI tests it without the research stack
(tests/test_wxpredict_sdyes.py). The development run (sdyes_dev.py) and the
forward shadow both call it; the frozen values below are the candidate's.

One entry at most per city-day, YES only, held to settlement. At each
decision every bucket's YES asks are walked for a ticket of TICKET_USD,
level by level while the model's probability is at least THETA above the
level's price plus the venue's taker fee at that price, never past MAX_SLIP
from the touch. A bucket qualifies when the walk buys at least the minimum
order. The city-day's entry is the first decision at which any bucket
qualifies; at that decision, the bucket with the largest edge (probability
minus cost per share), the lowest bucket on a tie. Whatever buys, the
average cost per share (price and fee) is at least THETA below p, because
every level bought is.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "scripts"))
import cost_model  # noqa: E402

VERSION = "sdyes-v1"
CITIES = ("jeddah", "istanbul")      # Jeddah the primary, Istanbul the comparison
WINDOW_LOCAL_H = (10, 17)            # decisions from 10:00 to before 17:00, the city's clock
MODEL = "p_weather"                  # the remaining-day model alone (sdyes_dev.py's choice, 12 Oct)
THETA = 0.10                         # sdyes_dev.py's choice on 13 Sep - 8 Oct (data/eval/wxpredict/sdyes_dev_2026-10-12.json)
MODEL_FILE = "data/models/sdyes/sdyes-v1_remaining.txt.gz"
MODEL_SHA256 = "f6f72d55e83f0fb45677a34677cb6e367c67afa9e3525b0055936d37eb0f2e0e"   # of the uncompressed model text
LATENCY_S = 60                       # the entry is judged on the first books answered at least this long after t
BOOK_WITHIN_S = 300                  # ... and no later than this after t; otherwise skipped, 'no fresh book'
FIRST_FORWARD_DATE = "2026-10-13"
FIRST_LOOK_DATES = 20                # settled target dates with a valid decision before the first look
SECOND_LOOK_DATES = 40
TICKET_USD = 10.0
MIN_SHARES = 5.0                     # assumed where the book does not state its minimum order
MAX_SLIP = 0.25
FEE_RATE = cost_model.DEFAULT_TAKER_RATE


def fee_per_share(price, rate=FEE_RATE):
    return cost_model.taker_fee(1.0, price, rate)


def walk_yes(levels, p, theta, ticket_usd=TICKET_USD, min_shares=MIN_SHARES, max_slip=MAX_SLIP,
             touch_penalty=0.0):
    """levels: the YES asks [{price, size}] best first. Returns (shares, usd,
    fee) or None. A level is bought while p - (price + fee per share) >=
    theta; the touch must qualify. touch_penalty: every share from the first
    level costs that much more (a stress on the archive's touch-priced first
    tier); which entries are made does not change."""
    if not levels:
        return None
    touch = float(levels[0]["price"])
    shares = usd = fee = 0.0
    for i, lv in enumerate(levels):
        price, size = float(lv["price"]), float(lv["size"])
        if price - touch > max_slip + 1e-12 or p - price - fee_per_share(price) < theta - 1e-12:
            break
        take = min(size, (ticket_usd - usd) / price)
        if take <= 0:
            break
        paid = price + (touch_penalty if i == 0 else 0.0)
        shares += take
        usd += take * paid
        fee += take * fee_per_share(paid)
        if usd >= ticket_usd - 1e-9:
            break
    if shares < min_shares - 1e-9:
        return None
    return shares, usd, fee


def choose(qualifying):
    """qualifying: [{t, bucket, edge, ...}] for one city-day, every bucket
    that qualified at some decision. The entry: the earliest t; at that t
    the largest edge, then the lowest bucket. None when nothing qualified."""
    if not qualifying:
        return None
    t0 = min(q["t"] for q in qualifying)
    return min((q for q in qualifying if q["t"] == t0), key=lambda q: (-q["edge"], q["bucket"]))


def settle(entry, won):
    """Net profit of an entry held to settlement: a share pays 1 if its bucket won."""
    return (entry["shares"] if won else 0.0) - entry["usd"] - entry["fee"]
