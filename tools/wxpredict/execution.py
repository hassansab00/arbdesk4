"""What a one-share signal could have bought at size, and what the cash allows.

Plain Python, so CI tests it without the research stack
(tests/test_wxpredict_execution.py); tools/wxpredict/capacity.py reads the
books and runs it.

The archive keeps no full ladder (raw_book is stripped), only the cumulative
dollars within 1, 2, 5, 10 and 25 cents of the touch and the whole side. A
ladder is rebuilt from them by the platform's own rule (sql/ad4_13_reconcile.sql,
ad4_synth_levels): each tier's added dollars at the tier's outer edge, the
first at the touch, and the rest of the side one cent past 25 cents, where a
walk capped at 25 cents never reaches it. The first tier is priced at the
touch, which the rule's own note calls optimistic (about 0.2% on its test
ladder): size_entry's touch_penalty charges every share taken from it more.

On the venue a NO ask is the YES bid's complement (equal within 0.001 on
99.93% of 31,654 late-September rows), so NO is bought by walking the YES bids
at 1 - price.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), "scripts"))
import cost_model  # noqa: E402

TIERS = (0.00, 0.02, 0.05, 0.10, 0.25, 0.26)   # ad4_synth_levels' offsets; the last is the unmeasured rest


def synth_levels(touch, cumulative, is_ask):
    """[{price, size}] best first, from the touch and the cumulative USD at
    1, 2, 5, 10, 25 cents and the whole side (None where not recorded)."""
    if touch is None or touch <= 0:
        return []
    sign = 1 if is_ask else -1
    out, prev = [], 0.0
    for off, cum in zip(TIERS, cumulative):
        if cum is None:
            continue
        inc = cum - prev
        prev = max(prev, cum)
        if inc <= 0:
            continue
        price = round(min(0.999, max(0.001, touch + sign * off)), 4)
        out.append({"price": price, "size": round(inc / price, 4)})
    return out


def as_no_asks(bid_levels):
    """YES bids [{price, size}] -> NO asks: price 1 - p for the same shares."""
    return [{"price": round(1 - lv["price"], 4), "size": lv["size"]} for lv in bid_levels]


def size_entry(levels, p, theta, ticket_usd, min_shares, max_touch_slip=0.25, touch_penalty=0.0):
    """Buy level by level while the probability is at least theta above the
    level's price (the one-share rule, applied to every level), up to
    ticket_usd, never past max_touch_slip from the touch. Nothing below
    min_shares. touch_penalty: every share taken from the first tier costs
    that much more (a stress on the touch's price; which entries are made
    does not change). Returns (shares, usd, fee) or None."""
    ok = [lv for lv in levels if p - lv["price"] >= theta]
    if not ok or ok[0] is not levels[0]:
        return None                                     # the touch itself must qualify, as the one-share rule
    fill = cost_model.walk_ladder(ok, usd_budget=ticket_usd, max_slippage=max_touch_slip)
    if fill["shares"] < min_shares - 1e-9:
        return None
    usd, fee, left = fill["usd_spent"], 0.0, fill["shares"]
    for i, lv in enumerate(ok):                         # the taker fee at each level's own price
        take = min(left, lv["size"])
        price = lv["price"] + (touch_penalty if i == 0 else 0.0)
        usd += take * (price - lv["price"])
        fee += cost_model.taker_fee(take, price)
        left -= take
        if left <= 1e-12:
            break
    return fill["shares"], usd, fee


def funding_order(e):
    """Entries are funded in time order; entries at the same instant by band
    and side, a fixed order that does not depend on how the list was built
    and does not look at the probability or the outcome (Codex on #379)."""
    return (e["t"], str(e.get("band_id", "")), str(e.get("side", "")))


def simulate(entries, bankroll, city_day_cap):
    """Shared cash: each entry {t, settle_t, city_day, usd, fee, shares, win}
    in funding_order pays usd + fee at t and returns shares (if it won) at
    settle_t. An entry the free cash cannot pay, or that would put more than
    city_day_cap of the bankroll on one city-day, is skipped. Returns the
    taken entries and the skips by reason."""
    cash, locked, per_city_day, taken, skipped = bankroll, [], {}, [], {"capital": 0, "city_day_cap": 0}
    for e in sorted(entries, key=funding_order):
        for item in [x for x in locked if x[0] <= e["t"]]:
            cash += item[1]
            locked.remove(item)
        cost = e["usd"] + e["fee"]
        if per_city_day.get(e["city_day"], 0.0) + cost > city_day_cap * bankroll + 1e-9:
            skipped["city_day_cap"] += 1
            continue
        if cost > cash + 1e-9:
            skipped["capital"] += 1
            continue
        cash -= cost
        per_city_day[e["city_day"]] = per_city_day.get(e["city_day"], 0.0) + cost
        locked.append((e["settle_t"], e["shares"] if e["win"] else 0.0))
        taken.append(e)
    return taken, skipped
