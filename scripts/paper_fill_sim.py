"""Order manager and fill simulator (plan v2 P5.7), the parts that decide and simulate.

ORDER TYPES. IOC taker (as today), LIMIT resting with a TTL of at most the next
tick, and CANCEL_REPLACE at each tick. choose_order_type() compares the
expected growth of the taker and maker options (the maker one priced by P5.4:
P_fill and adverse selection). A resting order is only considered where it can
pay: at least MAKER_MIN_HOURS to the close and a spread of at least
MAKER_MIN_SPREAD (the plan's 3 h and 4c). Where both are eligible the higher
expected growth wins, and a tie goes to the maker - that is the plan's bias.

MAKER FILLS, SIMULATED CONSERVATIVELY. A resting bid at b fills only from
trade prints on its own token at or below b, printed AFTER it was placed, and
only once those prints have used up the QUEUE AHEAD: the depth at b or better
on the bid side when it was placed, which the venue fills first. The fill price
is b. Partial fills are allowed. A print with no token recorded cannot be
attributed to a side and is not used (trades_observed: 22,800 of 87,446 rows
had no token_id on 25 Sep). Prints are timed by traded_at - observed_at is
null on every row today.

ADVERSE SELECTION. The mid 15 minutes after each fill is recorded, from the
first book snapshot at or after that moment, within MARK_WINDOW_MIN. It is
what P5.8 learns the adverse term from; no snapshot means no mark, never a
guessed one.

LINKED LEGS (baskets, locks). The least liquid leg goes first; if a later leg
fails, the caller re-runs the solver with the holdings it now has (P5.12)
rather than forcing an unwind. leg_order() is the first half of that.
"""
import datetime as dt

MAKER_MIN_HOURS = 3.0
MAKER_MIN_SPREAD = 0.04
ADVERSE_MARK_MIN = 15
MARK_WINDOW_MIN = 60
_EPS = 1e-9


def _t(x):
    if isinstance(x, dt.datetime):
        return x
    return dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def choose_order_type(g_taker, g_maker, hours_to_close, spread):
    """('IOC' | 'LIMIT' | None, why). None: neither option is worth taking."""
    maker_ok = (g_maker is not None and hours_to_close is not None and spread is not None
                and hours_to_close >= MAKER_MIN_HOURS and spread >= MAKER_MIN_SPREAD - _EPS)
    options = []
    if g_taker is not None and g_taker > 0:
        options.append(("IOC", g_taker))
    if maker_ok and g_maker > 0:
        options.append(("LIMIT", g_maker))
    if not options:
        return None, "no option has positive expected growth"
    # max() keeps the first of equals, so listing LIMIT first on a tie is the bias.
    kind, g = max(sorted(options, key=lambda o: o[0] != "LIMIT"), key=lambda o: o[1])
    why = f"{kind} expected growth {g:.6f}"
    if not maker_ok and g_maker is not None:
        why += f" (resting not considered: needs >= {MAKER_MIN_HOURS} h to close and >= {MAKER_MIN_SPREAD} spread)"
    return kind, why


def queue_ahead(bids, price):
    """Shares resting at `price` or better on the bid side - filled before a new bid at `price`."""
    return sum(float(l["size"]) for l in bids or [] if float(l["price"]) >= float(price) - _EPS)


def simulate_bid(order, prints):
    """Fills of one resting bid, from recorded trade prints.

    order  {"token_id", "price", "shares", "placed_at", "expires_at", "queue_ahead"}
    prints [{"token_id", "traded_at", "price", "size"}], any order
    """
    b, want, queue = float(order["price"]), float(order["shares"]), float(order.get("queue_ahead") or 0.0)
    start, end = _t(order["placed_at"]), _t(order["expires_at"])
    usable, unattributed = [], 0
    for p in prints:
        if p.get("token_id") is None:
            unattributed += 1
            continue
        if p["token_id"] != order["token_id"]:
            continue
        at = _t(p["traded_at"])
        if start < at <= end and float(p["price"]) <= b + _EPS:
            usable.append((at, float(p["size"])))
    usable.sort()
    fills, traded, filled = [], 0.0, 0.0
    for at, size in usable:
        before = traded
        traded += size
        # Shares that reach this bid: what traded past the queue, less what it already got.
        reach = max(0.0, traded - queue) - max(0.0, before - queue)
        take = min(reach, want - filled)
        if take > _EPS:
            fills.append({"at": at, "shares": take, "price": b})
            filled += take
        if filled >= want - _EPS:
            break
    status = "filled" if filled >= want - _EPS else ("partial" if filled > _EPS else "unfilled")
    return {"status": status, "filled": filled, "fills": fills, "price": b,
            "traded_at_or_below": traded, "queue_ahead": queue, "unattributed_prints": unattributed}


def adverse_marks(fills, books):
    """The mid ADVERSE_MARK_MIN after each fill, from book snapshots [{"observed_at", "best_bid", "best_ask"}]."""
    snaps = sorted(((_t(s["observed_at"]), s) for s in books
                    if s.get("best_bid") is not None and s.get("best_ask") is not None),
                   key=lambda x: x[0])
    out = []
    for f in fills:
        target = _t(f["at"]) + dt.timedelta(minutes=ADVERSE_MARK_MIN)
        mark = next(((t, s) for t, s in snaps
                     if target <= t <= target + dt.timedelta(minutes=MARK_WINDOW_MIN)), None)
        if mark is None:
            out.append({**f, "mid_after": None, "adverse": None})
            continue
        mid = (float(mark[1]["best_bid"]) + float(mark[1]["best_ask"])) / 2
        out.append({**f, "mid_after": mid, "marked_at": mark[0], "adverse": f["price"] - mid})
    return out


def leg_order(legs):
    """Linked legs, least liquid first: the leg most likely to fail goes before the others commit."""
    return sorted(legs, key=lambda l: (float(l.get("depth_usd") or 0.0), str(l.get("band_id"))))
