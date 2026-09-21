"""Five of nine strategies could never reach a desk, and it was insert order.

WHAT THE BOARD SAID: s3 "firing, but nothing has filled yet" on 978 signals,
s6 the same on 1,338, s5 on 37. Read as three strategies whose conditions the
market never quite meets.

WHAT WAS ACTUALLY HAPPENING. signal_engine writes every strategy in ONE batch
with ONE fired_at, so `order by fired_at desc, signal_id` is really "order by
whatever order the engine inserted them". The 15:09:53.937872 batch - a single
microsecond shared by four strategies:

    s1_buy_low_sell_signal   27 signals, ids 6308-6334   offered 1st
    s3_concentration         31 signals, ids 6335-6365   offered 2nd
    s4_tail_fade              7 signals, ids 6366-6372   offered 3rd
    s6_anchor_insurance      29 signals, ids 6373-6401   offered 4th

The cap is ten plans per desk per cycle and s1 has twenty-seven signals ahead
of s3's first. s1 takes the entire budget before s3 is ever considered, every
cycle, for as long as s1 keeps firing. Thirty days of it:

    s1   912 signals -> 395 plans
    s3   978 signals ->   1 plan
    s6 1,338 signals ->   1 plan
    s5    37 signals ->   0 plans

This is the SAME starvation cycle()'s own comment describes for desks - "the
first desk in account_id order consumed the whole budget and returned, so
every desk after it got nothing" - fixed there, left here. It is worse here,
because a starved desk looks switched off while a starved strategy looks like
one the market never suits.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import paper_plans as pp  # noqa: E402


T1 = "2026-09-21T15:09:53.937872+00:00"
T0 = "2026-09-21T15:05:00.000000+00:00"


def sig(sid, strategy, fired=T1):
    return {"signal_id": sid, "strategy_id": strategy, "fired_at": fired}


def batch(counts, fired=T1, start=1000):
    """One fired_at, strategies laid out consecutively as the engine writes."""
    rows, sid = [], start
    for strategy, n in counts:
        for _ in range(n):
            rows.append(sig(sid, strategy, fired))
            sid += 1
    return rows


def test_the_live_batch_no_longer_starves_the_later_strategies():
    """The measured batch. With a ten-plan cap, all four must appear in the
    first ten offers - under insert order the first ten are all s1."""
    rows = batch([("s1", 27), ("s3", 31), ("s4", 7), ("s6", 29)])

    before = [r["strategy_id"] for r in rows[:10]]
    assert set(before) == {"s1"}, "the fixture no longer reproduces the starvation"

    after = [r["strategy_id"] for r in pp.interleave_by_strategy(rows)[:10]]
    assert set(after) == {"s1", "s3", "s4", "s6"}, (
        f"the first ten offers are {sorted(set(after))} - a strategy is still "
        "structurally unable to reach a desk inside the plan cap"
    )


def test_each_strategy_is_offered_its_best_before_any_gets_its_second():
    """The rule, stated as the desk loop states it."""
    out = pp.interleave_by_strategy(batch([("s1", 3), ("s3", 3), ("s6", 3)]))
    assert [r["strategy_id"] for r in out[:3]] == ["s1", "s3", "s6"]
    assert [r["strategy_id"] for r in out[3:6]] == ["s1", "s3", "s6"]


def test_nothing_is_dropped_or_duplicated():
    rows = batch([("s1", 27), ("s3", 31), ("s4", 7), ("s6", 29)])
    out = pp.interleave_by_strategy(rows)
    assert len(out) == len(rows)
    assert {r["signal_id"] for r in out} == {r["signal_id"] for r in rows}


def test_fresher_batches_still_come_first():
    """Interleaving is WITHIN a fired_at. Staleness is a real disqualifier -
    publish_paper_plan refuses anything older than fifteen minutes - so a
    stale signal must never be promoted above a fresh one for fairness."""
    older = batch([("s3", 2)], fired=T0, start=1)
    newer = batch([("s1", 2)], fired=T1, start=100)
    out = pp.interleave_by_strategy(newer + older)
    assert [r["fired_at"] for r in out] == [T1, T1, T0, T0], (
        "a stale signal was interleaved ahead of a fresh one"
    )


def test_a_strategy_that_fires_once_is_not_pushed_behind_a_prolific_one():
    """s5 fires 37 times in thirty days against s6's 1,338. Rarity must not
    cost it its place."""
    out = pp.interleave_by_strategy(batch([("s6", 50), ("s5", 1)]))
    assert [r["strategy_id"] for r in out[:2]] == ["s6", "s5"]


def test_one_strategy_alone_is_unchanged():
    rows = batch([("s1", 5)])
    assert pp.interleave_by_strategy(rows) == rows


def test_empty_is_empty():
    assert pp.interleave_by_strategy([]) == []


def test_the_cycle_actually_uses_it():
    """The helper is worthless if cycle() still reads the raw order."""
    import inspect
    body = inspect.getsource(pp.cycle)
    assert "interleave_by_strategy(" in body, (
        "cycle() fetches signals without interleaving them, so the plan cap is "
        "still handed out in insert order"
    )


# --- and the sentence that explains a refusal has to be readable -----------
#
# Decimal keeps the exponent arithmetic gave it and an f-string prints it, so
# the refusals the desk showed Hassan read:
#
#   "Reaching the 5E+3-share venue minimum costs 0.4429 per share more than
#    the touch"
#   "Below the venue minimum order size: 15.39 shares against a 1E+2-share
#    floor, limited by book depth"
#
# in the one sentence whose whole job is explaining why it would not trade.


def test_a_refusal_never_reads_in_scientific_notation():
    from decimal import Decimal
    assert pp.plain(Decimal("5E+3")) == "5000"
    assert pp.plain(Decimal("1E+2")) == "100"
    assert pp.plain(Decimal("41.67")) == "41.67"
    assert pp.plain(Decimal("0.02")) == "0.02"


def test_normalize_would_have_made_it_worse():
    """The obvious fix is Decimal.normalize(). It is the wrong one: it turns
    100 INTO 1E+2, which is the bug pointing the other way."""
    from decimal import Decimal
    assert str(Decimal("100").normalize()) == "1E+2"
    assert pp.plain(Decimal("100")) == "100"


def test_every_venue_minimum_refusal_goes_through_it():
    """A reason built without plain() is one Decimal away from unreadable."""
    import inspect
    import re
    body = inspect.getsource(pp)
    for line in body.splitlines():
        if "venue minimum" in line and "{" in line:
            for field in re.findall(r"\{([a-z_]+)[\}\.\)]", line):
                if field in ("floor", "depth", "quantity", "affordable"):
                    assert f"plain({field})" in line, (
                        f"{field!r} is interpolated raw into: {line.strip()}"
                    )
