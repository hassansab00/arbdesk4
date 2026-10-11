"""The execution and capacity audit's rules (tools/wxpredict/execution.py): the
ladder rebuilt from the archived depth tiers by the platform's own rule
(sql/ad4_13_reconcile.sql, ad4_synth_levels), NO bought on the YES bids, the
one-share rule applied level by level, and shared cash with settlement."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import execution as ex  # noqa: E402

TIERS = [10.0, 30.0, 30.0, 60.0, 100.0, 150.0]       # cumulative USD at 1, 2, 5, 10, 25 c and the side


def test_the_ladder_is_ad4_synth_levels():
    lv = ex.synth_levels(0.40, TIERS, is_ask=True)
    assert [x["price"] for x in lv] == [0.40, 0.42, 0.50, 0.65, 0.66], "the 5c tier added nothing and is skipped"
    assert [x["size"] for x in lv] == [25.0, round(20 / 0.42, 4), 60.0, round(40 / 0.65, 4), round(50 / 0.66, 4)]
    bids = ex.synth_levels(0.38, TIERS, is_ask=False)
    assert [x["price"] for x in bids] == [0.38, 0.36, 0.28, 0.13, 0.12]
    assert ex.synth_levels(None, TIERS, True) == [] and ex.synth_levels(0.0, TIERS, True) == []


def test_no_is_bought_on_the_yes_bids():
    no = ex.as_no_asks(ex.synth_levels(0.38, TIERS, is_ask=False))
    assert [x["price"] for x in no] == [0.62, 0.64, 0.72, 0.87, 0.88]


def test_each_level_must_pass_the_rule_and_the_touch_first():
    lv = ex.synth_levels(0.40, TIERS, is_ask=True)
    shares, usd, fee = ex.size_entry(lv, p=0.55, theta=0.05, ticket_usd=1000, min_shares=5)
    assert shares == pytest.approx(25 + 20 / 0.42 + 60) and usd == pytest.approx(10 + 20 + 30), \
        "levels up to 0.50 (0.55 - 0.05); 0.65 is past the rule"
    assert fee == pytest.approx(0.05 * (25 * 0.4 * 0.6 + 20 / 0.42 * 0.42 * 0.58 + 60 * 0.5 * 0.5))
    assert ex.size_entry(lv, p=0.44, theta=0.05, ticket_usd=1000, min_shares=5) is None, "the touch fails the rule"
    small = ex.size_entry(lv, p=0.55, theta=0.05, ticket_usd=10, min_shares=5)
    assert small[0] == pytest.approx(25) and small[1] == pytest.approx(10), "the ticket stops the walk"
    assert ex.size_entry(lv, p=0.55, theta=0.05, ticket_usd=1, min_shares=5) is None, "below the minimum order"


def test_the_stress_charges_the_touch_tier_and_keeps_the_entries():
    lv = ex.synth_levels(0.40, TIERS, is_ask=True)
    base = ex.size_entry(lv, p=0.55, theta=0.05, ticket_usd=1000, min_shares=5)
    hit = ex.size_entry(lv, p=0.55, theta=0.05, ticket_usd=1000, min_shares=5, touch_penalty=0.01)
    assert hit[0] == base[0], "the same shares: the stress changes the price, not the entry"
    assert hit[1] == pytest.approx(base[1] + 25 * 0.01), "the 25 shares of the first tier, a cent dearer each"
    assert ex.size_entry(lv, p=0.46, theta=0.05, ticket_usd=1000, min_shares=5, touch_penalty=0.01) is not None, \
        "an entry the one-share rule took at the recorded touch is still made"


def test_the_unmeasured_rest_of_the_side_is_never_reached():
    lv = ex.synth_levels(0.10, TIERS, is_ask=True)            # levels 0.10 .. 0.35 and the rest at 0.36
    shares, usd, _ = ex.size_entry(lv, p=0.99, theta=0.05, ticket_usd=10_000, min_shares=1)
    assert usd == pytest.approx(100.0), "the walk stops at 25 cents from the touch"


def test_cash_is_shared_and_comes_back_at_settlement():
    e = lambda t, s, cd, usd, win: {"t": t, "settle_t": s, "city_day": cd, "usd": usd, "fee": 0.0,
                                    "shares": usd * 2, "win": win}
    taken, skipped = ex.simulate([e(1, 10, "a", 60, True), e(2, 10, "b", 60, False),
                                  e(11, 20, "c", 60, False), e(12, 20, "c", 60, False)],
                                 bankroll=100, city_day_cap=1.0)
    assert [x["t"] for x in taken] == [1, 11] and skipped == {"capital": 1, "city_day_cap": 1}, \
        "t=2: 60 of 100 is locked until t=10, 40 free; t=11: the win's 120 is back; t=12: a second 60 on city-day c passes the cap (100)"
    taken, skipped = ex.simulate([e(1, 10, "a", 6, True), e(2, 10, "a", 6, True)], bankroll=100, city_day_cap=0.1)
    assert len(taken) == 1 and skipped == {"capital": 0, "city_day_cap": 1}


def test_the_funding_order_does_not_depend_on_the_list():
    """Entries at one instant compete for the cash and the city-day cap; the
    winner must not depend on how the list was built (Codex on #379)."""
    import random
    base = [{"t": 5, "settle_t": 99, "city_day": "x", "band_id": f"b{i}", "side": s, "usd": 40.0, "fee": 0.0,
             "shares": 80.0, "win": i % 2 == 0} for i in range(4) for s in ("yes", "no")]
    first, _ = ex.simulate(base, bankroll=100, city_day_cap=1.0)
    for seed in range(5):
        shuffled = base[:]
        random.Random(seed).shuffle(shuffled)
        again, _ = ex.simulate(shuffled, bankroll=100, city_day_cap=1.0)
        assert [(e["band_id"], e["side"]) for e in again] == [(e["band_id"], e["side"]) for e in first]
    assert [(e["band_id"], e["side"]) for e in first] == [("b0", "no"), ("b0", "yes")], "band, then side"
