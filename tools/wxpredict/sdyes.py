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


# ---------------------------------------------------------------------------
# the forward decisions (WP5 step 3): which books judge a decision, and how
# ---------------------------------------------------------------------------
def fresh_answer(polls, t, icao, latency_s=LATENCY_S, within_s=BOOK_WITHIN_S):
    """When the books that judge a decision at t were answered: the first clean
    books poll (source clob_books, status 200, no error) that asked for the
    station's city, answered from t + latency_s to t + within_s. None when
    there is none: the decision is skipped, 'no fresh book'. polls: [{source,
    stations, answered_at (unix seconds), status_code, error}]."""
    best = None
    for p in polls:
        if p.get("source") != "clob_books" or p.get("status_code") != 200 or p.get("error"):
            continue
        a = p.get("answered_at")
        if icao not in (p.get("stations") or []) or a is None or not (t + latency_s <= a <= t + within_s):
            continue
        if best is None or a < best:
            best = a
    return best


def ask_levels(book):
    """A focus_book payload's asks ([[price, shares], ...], best first) as walk_yes levels."""
    return [{"price": float(p), "size": float(s)} for p, s in ((book or {}).get("asks") or [])]


def min_order(book):
    """The book's own minimum order; MIN_SHARES where it states none."""
    m = (book or {}).get("min")
    return MIN_SHARES if m in (None, "") else float(m)


def market_probs(books):
    """The market-only control: each bucket's price as the price record prices
    a book (the midpoint, an empty bid as 0 and an empty ask as 1), normalised
    over the ladder. None when a bucket has no book."""
    prices = []
    for b in books:
        if b is None or (b.get("bid") is None and b.get("ask") is None):
            return None
        bid, ask = b.get("bid"), b.get("ask")
        prices.append(((0.0 if bid is None else float(bid)) + (1.0 if ask is None else float(ask))) / 2)
    total = sum(prices)
    return [p / total for p in prices] if total > 0 else None


def judge(t, probs, books, theta=THETA):
    """One decision: for each bucket of the ladder (probs and books in ladder
    order; a book None where the bucket has none), the walk and whether it
    qualified, or why not: 'no probability', 'no book', 'no ask', 'below
    theta' (the touch with its fee leaves less than theta) or 'below the
    minimum order'."""
    out = []
    for i, (p, b) in enumerate(zip(probs, books)):
        row = {"t": t, "bucket": i, "p": p, "touch": None, "qualified": False, "reason": None}
        lv = ask_levels(b)
        if p is None:
            row["reason"] = "no probability"
        elif b is None:
            row["reason"] = "no book"
        elif not lv:
            row["reason"] = "no ask"
        else:
            row["touch"] = lv[0]["price"]
            w = walk_yes(lv, p, theta, min_shares=min_order(b))
            if w is None:
                short = p - lv[0]["price"] - fee_per_share(lv[0]["price"]) < theta - 1e-12
                row["reason"] = "below theta" if short else "below the minimum order"
            else:
                shares, usd, fee = w
                row.update(shares=shares, usd=usd, fee=fee, cost=(usd + fee) / shares,
                           edge=p - (usd + fee) / shares, qualified=True)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# the forward ledger (WP5 step 3): every decision, every bucket, every skip
# ---------------------------------------------------------------------------
WHO = ("candidate", "market", "rd1")       # sdyes-v1, the market-only control, the S10 incumbent


def ledger(decisions, polls, books_at, rd1_at):
    """The forward record of docs/SAME_DAY_YES_PREREG.md. decisions: [{city,
    date, event_id, t, icao, band_ids, probs (the candidate's, ladder order;
    None when it has none), no_probs (why the candidate has none: 'no row',
    'no running maximum'), row_hash (sha256 of the decision's live row), skip
    (a reason the whole decision is skipped, or None)}]. books_at(at) ->
    {band_id: focus_book payload} as captured at or before `at`. rd1_at(city,
    date, t) -> {band_id: p} of the newest rd1 checkpoint at or before t, or
    None. Returns (rows, entries): a row for every bucket each who judged at
    each decision, or one row for a decision skipped whole, with its reason;
    and each who's entry per city-day (sdyes.choose), not settled.

    A decision is skipped whole only when no fresh book judges it (or the
    caller says so): a candidate without a row or a running maximum still
    leaves the controls their decision on the same books (Codex on #392).
    Every row carries the version, the model's sha256 and the decision's row
    hash, so a recorded probability is bound to its inputs (Codex on #392)."""
    rows, qualifying = [], {}
    for d in decisions:
        base = {"version": VERSION, "model_sha256": MODEL_SHA256, "row_hash": d.get("row_hash"),
                "city": d["city"], "date": d["date"], "event_id": d["event_id"], "t": d["t"]}
        answered = None if d.get("skip") else fresh_answer(polls, d["t"], d["icao"])
        if d.get("skip") or answered is None:
            rows.append(dict(base, who=None, bucket=None, band_id=None, books_at=None, qualified=False,
                             reason=d.get("skip") or "no fresh book"))
            continue
        held = books_at(answered)
        books = [held.get(b) for b in d["band_ids"]]
        q = market_probs(books)
        r = rd1_at(d["city"], d["date"], d["t"])
        probs = {"candidate": d["probs"], "market": q,
                 "rd1": None if r is None else [r.get(b) for b in d["band_ids"]]}
        for who in WHO:
            if probs[who] is None:
                rows.append(dict(base, who=who, bucket=None, band_id=None, books_at=answered, qualified=False,
                                 reason="no market price on every bucket" if who == "market" else
                                 "no rd1 checkpoint" if who == "rd1" else d.get("no_probs") or "no model row"))
                continue
            for j in judge(d["t"], probs[who], books):
                row = dict(base, who=who, band_id=d["band_ids"][j["bucket"]], books_at=answered, **j)
                rows.append(row)
                if row["qualified"]:
                    qualifying.setdefault((who, d["city"], d["date"]), []).append(row)
    entries = {k: choose(v) for k, v in qualifying.items()}
    return rows, entries


def counts(rows, entries):
    """What may be read before the first look: decisions, skips by reason, the
    dates with a judged decision and the number of entries. No probability,
    price or profit. A date with a judged decision is not yet a valid date:
    the pre-registration's valid date is a settled one, counted by the
    settlement pass (Codex on #392)."""
    decisions = {(r["city"], r["date"], r["t"]) for r in rows}
    skipped = {}
    for r in rows:
        if r["who"] is None:
            skipped[r["reason"]] = skipped.get(r["reason"], 0) + 1
    judged = sorted({r["date"] for r in rows if r["who"] is not None})
    return {"decisions": len(decisions), "decisions_skipped": skipped, "dates_with_a_judged_decision": judged,
            "entries": {who: sum(1 for k in entries if k[0] == who) for who in WHO}}