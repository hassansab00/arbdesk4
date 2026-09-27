"""Plan v2 P5.9 part 2: clusters, correlated exposure and drawdown scaling
(scripts/city_clusters.py, risk_rails.drawdown_scale, the engine's room)."""
import datetime as dt
import random

import city_clusters as cc
import decision_engine as de
import risk_rails

from test_decision_engine import BOOK, LEDGER, VIEW, _paid

AS_OF = dt.date(2026, 9, 20)
RAILS = dict(risk_rails.DEFAULTS)


def _record(n_days, cities, factor=None, seed=1):
    """(record rows, labels): forecast - observed = shared factor (for the
    cities in `factor`) plus each city's own noise."""
    rng = random.Random(seed)
    rows, labels = [], {}
    for k in range(n_days):
        d = str(AS_OF - dt.timedelta(days=n_days - k))
        common = rng.gauss(0, 1)
        for c in cities:
            err = (common if factor and c in factor else 0.0) + rng.gauss(0, 1 if not factor or c not in factor else 0.2)
            labels[(c, d)] = 20.0
            rows.append([c, "1", d, f"{20.0 + err:.4f}"])
            rows.append([c, "2", d, "99"])           # lead 2 is not read
    return rows, labels


# ---------------------------------------------------------------------------
# the fit
# ---------------------------------------------------------------------------

def test_shrinkage_keeps_a_real_factor_and_shrinks_noise():
    cities = ["a", "b", "c", "d", "e"]
    rows, labels = _record(300, cities, factor={"a", "b"})
    R, shrink, _rbar = cc.ledoit_wolf(cc.matrix(cc.detrended(cc.residuals(rows, labels)), AS_OF)[2])
    assert 0.0 <= shrink <= 1.0
    for i in range(5):
        assert abs(R[i][i] - 1.0) < 1e-12
        for j in range(5):
            assert abs(R[i][j] - R[j][i]) < 1e-12
    assert R[0][1] > 0.8                               # a and b share the weather
    assert max(abs(R[i][j]) for i in range(2, 5) for j in range(5) if i != j) < 0.2


def test_the_bias_removal_reads_only_the_past():
    series = {"a": {f"2026-01-{d:02d}": float(d % 7) for d in range(1, 29)}}
    before = cc.detrended(series, window=5)
    series["a"]["2026-01-28"] = 1000.0
    after = cc.detrended(series, window=5)
    assert {d: v for d, v in before["a"].items() if d < "2026-01-28"} == \
           {d: v for d, v in after["a"].items() if d < "2026-01-28"}
    assert "2026-01-05" not in before["a"] and "2026-01-06" in before["a"]


def test_the_fit_never_reads_its_own_day_or_later():
    cities = ["a", "b", "c"]
    rows, labels = _record(200, cities, factor={"a", "b"})
    base = cc.fit(rows, labels, AS_OF)
    later = list(rows) + [[c, "1", str(AS_OF + dt.timedelta(days=k)), "50"] for c in cities for k in range(40)]
    labels2 = {**labels, **{(c, str(AS_OF + dt.timedelta(days=k))): 0.0 for c in cities for k in range(40)}}
    assert cc.fit(later, labels2, AS_OF)["rho"] == base["rho"]


def test_rule_11_prior_step_bounds_and_minimum_sample():
    rows, labels = _record(200, ["a", "b", "c"], factor={"a", "b"})
    rows += [["thin", "1", str(AS_OF - dt.timedelta(days=k)), "20"] for k in range(1, 50)]
    labels.update({("thin", str(AS_OF - dt.timedelta(days=k))): 20.0 for k in range(1, 50)})
    t1 = cc.fit(rows, labels, AS_OF)
    assert "thin" not in t1["measured_cities"]
    # the first fit starts from the prior and moves at most MAX_STEP
    assert t1["rho"]["a|b"] == round(cc.RHO_PRIOR + cc.MAX_STEP, 4)
    assert t1["rho"]["a|c"] == round(cc.RHO_PRIOR - cc.MAX_STEP, 4)
    assert t1["rho"]["a|thin"] == cc.RHO_PRIOR                   # unmeasured: the prior
    # the next moves from the previous version, never outside [0, 1]
    t2 = cc.fit(rows, labels, AS_OF, previous=t1)
    assert abs(t2["rho"]["a|b"] - t1["rho"]["a|b"]) <= cc.MAX_STEP + 1e-9
    assert all(cc.RHO_BOUNDS[0] <= v <= cc.RHO_BOUNDS[1] for v in t2["rho"].values())
    assert t2["previous"] == t1["version"] and t2["version"] != t1["version"]
    assert t1["version"].startswith("city-clusters:2026-09-20:")


def test_a_refit_is_weekly():
    assert cc.due(None, AS_OF)
    assert not cc.due({"as_of": "2026-09-14"}, AS_OF)
    assert cc.due({"as_of": "2026-09-13"}, AS_OF)


def test_clusters_join_only_pairs_above_the_threshold():
    rho = {"a|b": 0.9, "b|c": 0.6, "c|d": 0.2, "d|e": 0.49}
    got = cc.clusters(["a", "b", "c", "d", "e"], rho)
    assert got["a"] == got["b"] == got["c"] == "a"
    assert got["d"] == "d" and got["e"] == "e"


# ---------------------------------------------------------------------------
# the room, and the plan's test: the cluster cap binds on correlated fixtures
# ---------------------------------------------------------------------------

CORRELATED = {"version": "t", "rho": {"c|x": 0.9, "c|y": 0.0}, "cluster": {"c": "c", "x": "c", "y": "y"}}


def test_the_room_shrinks_as_correlation_rises():
    same_day = {"x": 60.0}
    rooms = []
    for r in (0.0, 0.3, 0.6, 0.9):
        table = {"rho": {"c|x": r}, "cluster": {"c": "c", "x": "x"}}
        rooms.append(cc.room(RAILS, table, "c", 1000.0, 0.0, same_day)[0])
    assert rooms == sorted(rooms, reverse=True) and rooms[0] > rooms[-1]
    assert abs(rooms[-1] - (80.0 - 0.9 * 60.0) / 1000.0) < 1e-12


def test_the_cluster_rail_counts_the_whole_cluster():
    frac, by = cc.room(RAILS, CORRELATED, "c", 1000.0, 10.0, {"x": 60.0, "y": 500.0})
    assert by == "cluster_day" and abs(frac - (80.0 - 70.0) / 1000.0) < 1e-12


def test_an_unmeasured_pair_is_not_an_uncorrelated_one():
    frac, by = cc.room(RAILS, None, "c", 1000.0, 0.0, {"q": 200.0})
    assert by == "correlated" and abs(frac - (80.0 - cc.RHO_PRIOR * 200.0) / 1000.0) < 1e-12


def test_the_cluster_cap_binds_in_the_engine_on_correlated_fixtures():
    alone = de.decide(VIEW, book=BOOK, ledger=LEDGER, params={"clusters": CORRELATED})
    assert "city_day" in alone["binding"]
    d = de.decide(VIEW, book=BOOK, ledger=dict(LEDGER, same_day={"x": 60.0}), params={"clusters": CORRELATED})
    assert d["action"] == "BUY" and "cluster_day" in d["binding"]
    assert _paid(d["orders"]) <= 20.0 + 0.05 < _paid(alone["orders"])
    assert d["versions"]["clusters"] == "t"
    # an uncorrelated neighbour takes nothing off
    free = de.decide(VIEW, book=BOOK, ledger=dict(LEDGER, same_day={"y": 60.0}), params={"clusters": CORRELATED})
    assert abs(_paid(free["orders"]) - _paid(alone["orders"])) < 0.05


def test_a_full_cluster_blocks_the_order_whatever_the_solver_wants():
    d = de.decide(VIEW, book=BOOK, ledger=dict(LEDGER, same_day={"x": 80.0}), params={"clusters": CORRELATED})
    assert d["action"] == "NONE" and d["reason_code"] == "cluster_day_full" and not d["orders"]


# ---------------------------------------------------------------------------
# drawdown
# ---------------------------------------------------------------------------

def test_drawdown_scaling_is_the_plans_rule():
    f = risk_rails.drawdown_scale
    assert f(1000.0, None) == 1.0 and f(1100.0, 1000.0) == 1.0
    assert abs(f(900.0, 1000.0) - 0.5) < 1e-12
    assert f(850.0, 1000.0) == risk_rails.DRAWDOWN_MIN_SCALE == f(100.0, 1000.0)


def test_a_ledger_in_drawdown_bets_smaller():
    view = dict(VIEW, only=["b:YES"])
    book = dict(BOOK, b={"ask": 0.25, "bid": 0.23, "depth_usd": 500.0})
    rails = dict(RAILS, city_day_frac=1.0, cluster_day_frac=1.0)     # let lambda set the size
    top = de.decide(view, book=book, ledger=LEDGER, rails=rails, params={"against_market_gate_on": False})
    down = de.decide(view, book=book, ledger=dict(LEDGER, high_water_usd=1000.0 / 0.95), rails=rails,
                     params={"against_market_gate_on": False})
    assert top["action"] == down["action"] == "BUY"
    assert abs(down["drawdown_scale"] - 0.75) < 1e-9 and "drawdown" in down["binding"]
    assert abs(_paid(down["orders"]) - 0.75 * _paid(top["orders"])) < 0.05


def test_the_loader_is_the_prior_while_learning_is_off():
    def rest(table, params):
        if table == "settings":
            return [{"value": {"enabled": False}}]
        raise AssertionError("strategy_params must not be read")
    assert cc.load(rest) is None
    assert cc.rho_of(None, "a", "b") == cc.RHO_PRIOR and cc.rho_of(None, "a", "a") == 1.0
    assert cc.version_of(None) == cc.PRIOR_VERSION
