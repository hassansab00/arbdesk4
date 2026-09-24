"""The fixed risk rails (plan v2 P5.9, part 1)."""
import json
import pathlib
import re

import pytest

import holdings_solver as hs
import risk_rails as rr

ROOT = pathlib.Path(__file__).resolve().parents[1]
EDGE = [{"id": "b0", "p": 0.5, "yes_price": 0.40, "no_price": None},
        {"id": "b1", "p": 0.3, "yes_price": 0.20, "no_price": None},
        {"id": "b2", "p": 0.2, "yes_price": 0.45, "no_price": None}]


def test_the_python_defaults_are_the_migrations_seed():
    """One set of numbers: the database enforces what the migration seeded, the
    solver plans inside DEFAULTS when the row cannot be read. They must agree."""
    sql = (ROOT / "supabase/migrations/20260924110000_fixed_risk_rails.sql").read_text()
    seed = sql[sql.index("('risk_rails', jsonb_build_object("):]
    seed = seed[:seed.index("'decided_by'")]
    pairs = dict(re.findall(r"'(\w+)',\s*([0-9.]+)", seed))
    assert {k: float(v) for k, v in pairs.items()} == {k: float(v) for k, v in rr.DEFAULTS.items()}


def test_the_rails_are_the_plans_defaults():
    assert rr.DEFAULTS == {"daily_loss_frac": 0.05, "city_day_frac": 0.03, "cluster_day_frac": 0.08,
                           "max_price": 0.97, "close_buffer_min": 15}


def test_load_reads_the_live_rails_and_the_kill_switch():
    rows = [{"key": "risk_rails", "value": {"city_day_frac": 0.02, "max_price": 0.95}},
            {"key": "trading_halt", "value": {"halted": True, "reason": "venue outage"}}]
    rails, halted, why = rr.load(rest=lambda *a, **k: rows)
    assert rails["city_day_frac"] == 0.02 and rails["max_price"] == 0.95 and rails["daily_loss_frac"] == 0.05
    assert halted and why == "venue outage"

    def boom(*a, **k):
        raise RuntimeError("settings unreadable")
    assert rr.load(rest=boom) == (rr.DEFAULTS, False, None)


def test_the_ladder_budget_is_what_is_left_of_three_percent():
    assert rr.ladder_budget(rr.DEFAULTS, 1000, 0) == pytest.approx(0.03)
    assert rr.ladder_budget(rr.DEFAULTS, 1000, 20) == pytest.approx(0.01)
    assert rr.ladder_budget(rr.DEFAULTS, 1000, 45) == 0.0, "already over: nothing more"


def test_the_daily_loss_rail():
    assert not rr.daily_loss_hit(rr.DEFAULTS, 960, -40)          # 40 of 1,000 = 4%
    assert rr.daily_loss_hit(rr.DEFAULTS, 950, -50)              # 50 of 1,000 = 5%
    assert not rr.daily_loss_hit(rr.DEFAULTS, 1100, 100)


def test_the_solver_spends_no_more_than_the_city_day_rail():
    free = hs.solve_book(EDGE, allow=("YES",))
    assert 1 - free["cash"] > 0.4, "unrailed, Kelly spends ~47% here"
    railed = hs.solve_book(EDGE, allow=("YES",), max_spend=rr.ladder_budget(rr.DEFAULTS, 1000, 0))
    assert 1 - railed["cash"] == pytest.approx(0.03, abs=1e-6)
    assert "city_day" in railed["binding"]
    # Inside the budget it is the constrained optimum, not Kelly scaled down:
    # a small budget goes first to the best edge per dollar (b1, p/c 1.44
    # against b0's 1.21). A grid over every split of the 3% agrees.
    import math
    from allocator import effective_cost
    c0, c1 = effective_cost(0.40), effective_cost(0.20)
    best = max(((0.5 * math.log(1 - s + a / c0) + 0.3 * math.log(1 - s + (s - a) / c1)
                 + 0.2 * math.log(1 - s), a) for s in [0.03] for a in [i * 0.0003 for i in range(101)]))
    got = sum(p * math.log(w) for p, w in zip([0.5, 0.3, 0.2], railed["wealth_by_outcome"].values()))
    assert got >= best[0] - 1e-7, "the grid found a better split of the city-day budget"


def test_a_rail_that_does_not_bind_changes_nothing():
    free = hs.solve_book(EDGE, allow=("YES",))
    loose = hs.solve_book(EDGE, allow=("YES",), max_spend=0.9)
    assert loose["weights"] == pytest.approx(free["weights"], abs=1e-4)
    assert "city_day" not in loose["binding"]


def test_nothing_is_bought_above_the_price_rail():
    ladder = [{"id": "b0", "p": 0.99, "yes_price": 0.98, "no_price": None},
              {"id": "b1", "p": 0.01, "yes_price": 0.005, "no_price": None}]
    book = hs.solve_book(ladder, allow=("YES",), max_price=rr.DEFAULTS["max_price"])
    assert "b0:YES" not in book["weights"], "a 98c YES is past the 0.97 rail"
