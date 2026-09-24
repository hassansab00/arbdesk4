"""Execution cost model (plan v2 P5.4), on real order books.

tests/fixtures/book_snapshots_24sep.json holds two YES books exactly as
book_snapshots captured them on 24 Sep, with the numbers the pipeline stored
beside them. The walk here must reproduce those stored numbers - that is what
makes it the same book the rest of the desk sees, not a fixture of our own.
"""
import json
import math
import os
from decimal import Decimal

import pytest

import cost_model
import execution_cost as ec
import paper_plans

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPS = {s["snapshot_id"]: s for s in json.load(
    open(os.path.join(HERE, "fixtures", "book_snapshots_24sep.json")))["snapshots"]}
A = SNAPS[293949]
B = SNAPS[292972]


def _fee(p, n=1.0):
    return n * 0.05 * p * (1 - p)


# --------------------------------------------------------------------------
# Taker
# --------------------------------------------------------------------------

@pytest.mark.parametrize("snap", [A, B], ids=["293949", "292972"])
def test_the_walk_reproduces_the_depth_the_pipeline_stored(snap):
    """ask_usd_5c is the dollars resting within 5c of the touch. Buying
    exactly those shares must spend exactly that, before fees."""
    asks = snap["raw_book"]["asks"]
    touch = asks[0]["price"]
    within = sum(l["size"] for l in asks if l["price"] <= touch + 0.05 + 1e-9)
    got = ec.taker_cost(asks, within)
    assert got["fully_filled"]
    assert got["price_usd"] == pytest.approx(snap["ask_usd_5c"], abs=1e-9)


def test_every_share_pays_the_venue_fee_at_its_own_price():
    got = ec.taker_cost(A["raw_book"]["asks"], 40.0)
    # 25.61 @ .44, 10 @ .45, 4.39 @ .46
    fees = _fee(0.44, 25.61) + _fee(0.45, 10) + _fee(0.46, 4.39)
    assert got["fee_usd"] == pytest.approx(fees)
    assert got["fee_usd"] == pytest.approx(
        cost_model.taker_fee(25.61, 0.44) + cost_model.taker_fee(10, 0.45) + cost_model.taker_fee(4.39, 0.46))
    assert got["total_usd"] == pytest.approx(got["price_usd"] + fees)
    assert got["limit_price"] == 0.46
    assert got["marginal_cost_per_share"] == pytest.approx(0.46 + _fee(0.46))


def test_it_agrees_with_the_order_path_and_the_old_walker():
    asks = A["raw_book"]["asks"]
    mine = ec.taker_cost(asks, 60.0)
    limit, avg = paper_plans.ladder_cost(
        {"asks": [{"price": str(l["price"]), "size": str(l["size"])} for l in asks]}, Decimal(60))
    assert float(limit) == mine["limit_price"]
    assert float(avg) == pytest.approx(mine["price_usd"] / 60.0)
    old = cost_model.walk_ladder([{"price": l["price"], "size": l["size"]} for l in asks], shares_target=60.0)
    assert old["avg_price"] == pytest.approx(mine["price_usd"] / 60.0)


def test_the_curve_rises_with_quantity():
    curve = ec.taker_curve(B["raw_book"]["asks"])
    costs = [c for *_r, c in curve]
    assert costs == sorted(costs), "the next share never costs less than the last"
    assert curve[0][:3] == (0.0, 5.0, 0.39)
    assert curve[-1][1] == pytest.approx(sum(l["size"] for l in B["raw_book"]["asks"]))


def test_a_ladder_too_thin_says_so():
    asks = B["raw_book"]["asks"]
    depth = sum(l["size"] for l in asks)
    got = ec.taker_cost(asks, depth + 1)
    assert not got["fully_filled"] and got["shares"] == pytest.approx(depth)


def test_the_order_of_the_input_does_not_matter():
    asks = A["raw_book"]["asks"]
    assert ec.taker_cost(list(reversed(asks)), 30)["total_usd"] == \
        pytest.approx(ec.taker_cost(asks, 30)["total_usd"])


# --------------------------------------------------------------------------
# Exit
# --------------------------------------------------------------------------

def test_an_exit_walks_the_bids_dearest_first_and_pays_the_fee():
    bids = A["raw_book"]["bids"]
    got = ec.exit_proceeds(list(reversed(bids)), 20.0)
    # 5 @ .36, 15 @ .35
    gross = 5 * 0.36 + 15 * 0.35
    fees = _fee(0.36, 5) + _fee(0.35, 15)
    assert got["gross_usd"] == pytest.approx(gross)
    assert got["net_usd"] == pytest.approx(gross - fees)
    assert got["limit_price"] == 0.35 and got["fully_filled"]


# --------------------------------------------------------------------------
# Maker, under the plan's priors
# --------------------------------------------------------------------------

def test_a_bid_at_the_touch_for_an_hour_fills_one_time_in_five():
    assert ec.p_fill(0.36, 0.36, 0.44, ttl_h=1, hours_to_close=10) == pytest.approx(0.2)
    assert ec.p_fill(0.36, 0.36, 0.44, ttl_h=2, hours_to_close=10) == pytest.approx(1 - 0.8 ** 2)


def test_a_bid_cannot_fill_after_the_market_closes():
    assert ec.p_fill(0.36, 0.36, 0.44, ttl_h=5, hours_to_close=1) == pytest.approx(0.2)
    assert ec.p_fill(0.36, 0.36, 0.44, ttl_h=5, hours_to_close=0) == 0.0


def test_a_bid_below_the_touch_is_given_no_fill_until_it_is_measured():
    assert ec.p_fill(0.30, 0.36, 0.44, ttl_h=1, hours_to_close=10) == 0.0


def test_a_bid_that_crosses_is_not_a_maker_order():
    assert ec.p_fill(0.44, 0.36, 0.44, ttl_h=1, hours_to_close=10) is None
    assert ec.maker_quote(0.45, 10, 0.36, 0.44, 1, 10) is None


def test_adverse_selection_is_half_the_spread():
    assert ec.adverse(A["best_bid"], A["best_ask"]) == pytest.approx(0.5 * A["spread"])
    q = ec.maker_quote(0.40, 50, A["best_bid"], A["best_ask"], ttl_h=1, hours_to_close=6)
    assert q["effective_cost_per_share"] == pytest.approx(0.40 + 0.04)
    assert q["expected_shares"] == pytest.approx(10.0)
    assert q["version"] == ec.PRIOR_VERSION


# --------------------------------------------------------------------------
# Rule 11: learned values are bounded and versioned, the prior is the fallback
# --------------------------------------------------------------------------

def test_learned_parameters_are_clipped_to_their_bounds_and_versioned():
    rows = [{"param": "p_fill_touch_1h", "value": {"v": 0.99}, "version": "v7"},
            {"param": "adverse_spread_fraction", "value": {"v": -0.3}, "version": "v7"},
            {"param": "p_fill_touch_1h", "value": {"v": 0.5}, "version": "v6"}]
    params = ec.load(rest=lambda *a, **k: rows)
    assert params["p_fill_touch_1h"] == ec.P_FILL_TOUCH_1H_BOUNDS[1], "the newest row wins, clipped"
    assert params["adverse_spread_fraction"] == ec.ADVERSE_BOUNDS[0]
    assert params["version"] == "adverse_spread_fraction:v7,p_fill_touch_1h:v7"


def test_no_parameter_table_means_the_priors():
    def boom(*a, **k):
        raise RuntimeError("relation strategy_params does not exist")
    assert ec.load(rest=boom) == ec.priors()
    assert ec.load(rest=lambda *a, **k: []) == ec.priors()
