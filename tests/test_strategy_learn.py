"""The nightly learning loop (plan v2 P5.8), part 1: belief maps into
strategy_params, walk-forward, one row per version, and nothing used until the
flag says so."""
import datetime as dt
import sys
import types

import pytest

import belief
import city_clusters
import strategy_learn as sl

B1, B2, B3 = "b1", "b2", "b3"


def _cp(i, day, checkpoint="noon", probs=None):
    return {"checkpoint_id": f"c{i}", "city_key": "nyc", "target_date": day, "checkpoint": checkpoint,
            "probs": probs or {B1: 0.6, B2: 0.3, B3: 0.1}}


def _out(i, winner=B1):
    return {"checkpoint_id": f"c{i}", "winner_band_id": winner, "ladder_has_winner": True}


class _DB:
    """Fakes common.rest / rest_all / upsert / log_run for one run."""
    def __init__(self, checkpoints, outcomes, stored=None):
        self.checkpoints, self.outcomes, self.stored = checkpoints, outcomes, list(stored or [])
        self.s10 = []
        self.logged, self.upserts = [], []

    def module(self):
        m = types.ModuleType("common")
        def rest(path, params=None, **k):
            if path != "strategy_params":
                return []
            param = dict(params or []).get("param", "eq.belief")[3:]
            mine = [r for r in self.stored if r["param"] == param]
            return [{"value": r["value"], "version": r["version"]} for r in mine[-1:]]
        m.rest = rest

        def rest_all(path, params=None, *, order, page_size=500):
            return {"prediction_checkpoints": self.checkpoints, "fact_checkpoint_outcome": self.outcomes,
                    "s10_shadow_checkpoints": self.s10}[path]
        m.rest_all = rest_all

        def upsert(table, rows, on_conflict, chunk=500):
            assert table == "strategy_params" and on_conflict == "param,scope,version"
            self.upserts.append(rows)
            new = [r for r in rows if r["version"] not in {s["version"] for s in self.stored}]
            self.stored += new
            return len(new)
        m.upsert = upsert
        m.log_run = lambda job, status, rows, detail: self.logged.append((job, status, rows, detail))
        return m


def _run(monkeypatch, db, as_of="2026-09-26", dry=False, clusters=([], {}, [])):
    monkeypatch.setitem(sys.modules, "common", db.module())
    monkeypatch.setattr(sl, "cluster_inputs", lambda rest_all: clusters)
    return sl.main(["--as-of", as_of] + (["--dry-run"] if dry else []))


def test_a_night_writes_one_versioned_row_with_its_prior_and_bounds(monkeypatch):
    db = _DB([_cp(1, "2026-09-24"), _cp(2, "2026-09-25")], [_out(1), _out(2, B2)])
    d = _run(monkeypatch, db)
    assert len(db.stored) == 1
    row = db.stored[0]
    assert row["param"] == "belief" and row["scope"] == "all" and row["as_of"] == "2026-09-26"
    assert row["version"].startswith("belief:2026-09-26:") and row["n"] == 6
    assert row["prior"] == {"centre": "p_model", "k0": belief.K0, "n_min": belief.N_MIN,
                            "max_step": belief.MAX_STEP}
    assert row["bounds"] == {"p": [belief.P_LO, belief.P_HI]}
    assert db.logged[0][:3] == ("P5.8_strategy_learn", "ok", 1)
    assert d["belief"]["written"] and set(d["not_fitted"]) >= {"lambda", "p_fill_touch_1h"}
    assert "cluster_correlation" not in d["not_fitted"]


def test_it_learns_only_from_days_before_it_runs(monkeypatch):
    """Walk-forward: a checkpoint for the day it runs, or later, is not evidence."""
    db = _DB([_cp(1, "2026-09-25"), _cp(2, "2026-09-26"), _cp(3, "2026-09-27")],
             [_out(1), _out(2), _out(3)])
    _run(monkeypatch, db)
    assert db.stored[0]["n"] == 3


def test_a_night_with_nothing_new_writes_nothing(monkeypatch):
    db = _DB([_cp(1, "2026-09-24")], [_out(1)])
    _run(monkeypatch, db, as_of="2026-09-26")
    d = _run(monkeypatch, db, as_of="2026-09-27")        # same settled buckets
    assert len(db.stored) == 1 and d["belief"]["unchanged"] and db.logged[-1][2] == 0


def test_the_step_is_taken_from_the_last_version_written(monkeypatch):
    """25% per night from what the loop wrote last (belief.MAX_STEP)."""
    first = [_cp(i, "2026-09-24") for i in range(40)]
    db = _DB(first, [_out(i, B3) for i in range(40)])        # the 0.1 bucket always wins
    _run(monkeypatch, db, as_of="2026-09-25")
    more = first + [_cp(100 + i, "2026-09-25") for i in range(40)]
    db.checkpoints, db.outcomes = more, db.outcomes + [_out(100 + i, B1) for i in range(40)]
    d = _run(monkeypatch, db, as_of="2026-09-26")
    assert len(db.stored) == 2
    assert d["belief"]["previous"] == db.stored[0]["version"] and d["belief"]["held_back"] > 0


def test_a_dry_run_writes_and_logs_nothing(monkeypatch):
    db = _DB([_cp(1, "2026-09-24")], [_out(1)])
    d = _run(monkeypatch, db, dry=True)
    assert db.stored == [] and db.logged == [] and d["dry_run"]


def test_the_table_is_write_once_and_the_flag_starts_off():
    import pathlib
    mig = (pathlib.Path(__file__).resolve().parents[1] / "supabase" / "migrations"
           / "20260926110000_strategy_params.sql").read_text()
    assert "unique (param, scope, version)" in mig
    assert "strategy_params_immutable before update or delete" in mig
    assert "'enabled', false" in mig and "on conflict (key) do nothing" in mig


def test_the_daily_chain_runs_it_after_databank():
    import pathlib
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"
          / "pipeline_daily.yml").read_text()
    assert wf.index("scripts/databank.py") < wf.index("scripts/strategy_learn.py")


def _cluster_inputs(n_days=120, end="2026-09-26"):
    """Two cities whose forecasts miss together, one that does not."""
    import random
    rng, rows, labels = random.Random(3), [], {}
    last = dt.date.fromisoformat(end)
    for k in range(n_days):
        d = str(last - dt.timedelta(days=n_days - k))
        common = rng.gauss(0, 1)
        for c, e in (("a", common + rng.gauss(0, 0.2)), ("b", common + rng.gauss(0, 0.2)), ("c", rng.gauss(0, 1))):
            labels[(c, d)] = 20.0
            rows.append([c, "1", d, f"{20.0 + e:.4f}"])
    return rows, labels, ["a", "b", "c", "unmeasured"]


def test_the_cluster_fit_is_weekly_and_versioned(monkeypatch):
    db = _DB([_cp(1, "2026-09-24")], [_out(1)])
    d = _run(monkeypatch, db, clusters=_cluster_inputs())
    rows = [r for r in db.stored if r["param"] == "city_clusters"]
    assert len(rows) == 1 and d["city_clusters"]["written"]
    row = rows[0]
    assert row["version"].startswith("city-clusters:2026-09-26:") and row["as_of"] == "2026-09-26"
    assert row["bounds"] == {"rho": [0.0, 1.0]} and row["prior"]["rho"] == city_clusters.RHO_PRIOR
    v = row["value"]
    assert v["measured_cities"] == ["a", "b", "c"] and v["rho"]["a|unmeasured"] == city_clusters.RHO_PRIOR
    assert v["rho"]["a|b"] == round(city_clusters.RHO_PRIOR + city_clusters.MAX_STEP, 4)
    # six days later: not due, nothing written; seven: due, and it steps from the last version
    d6 = _run(monkeypatch, db, as_of="2026-10-02", clusters=_cluster_inputs(end="2026-10-02"))
    assert not d6["city_clusters"]["due"] and len([r for r in db.stored if r["param"] == "city_clusters"]) == 1
    d7 = _run(monkeypatch, db, as_of="2026-10-03", clusters=_cluster_inputs(end="2026-10-03"))
    assert d7["city_clusters"]["previous"] == row["version"]
    second = [r for r in db.stored if r["param"] == "city_clusters"][-1]["value"]
    assert abs(second["rho"]["a|b"] - v["rho"]["a|b"]) <= city_clusters.MAX_STEP + 1e-9


def test_no_measured_city_writes_no_cluster_row(monkeypatch):
    db = _DB([_cp(1, "2026-09-24")], [_out(1)])
    d = _run(monkeypatch, db, clusters=_cluster_inputs(n_days=40))    # under MIN_DATES
    assert not [r for r in db.stored if r["param"] == "city_clusters"]
    assert d["city_clusters"]["measured_cities"] == 0 and not d["city_clusters"]["written"]


# ---------------------------------------------------------------------------
# the market weight (P5.3 amended)
# ---------------------------------------------------------------------------
# As the tick stores it: a dead bucket is ask-only, and its last trade is ignored.
BOOK = {B1: {"ask": 0.62, "bid": 0.58, "last": 0.6}, B2: {"ask": 0.32, "bid": 0.28, "last": 0.3},
        B3: {"ask": 0.001, "bid": None, "last": 0.999}}


def _mcp(i, day, checkpoint="noon", market=BOOK):
    return dict(_cp(i, day, checkpoint), market=market)


def test_the_market_weight_is_fitted_from_the_book_beside_each_call(monkeypatch):
    days = [str(dt.date(2026, 9, 1) + dt.timedelta(days=k)) for k in range(5)]
    cps = [_mcp(k, d) for k, d in enumerate(days)] + [_mcp(99, days[0], market={B1: {"ask": 0.6}})]
    db = _DB(cps, [_out(k) for k in range(5)] + [_out(99)])
    db.s10 = [{"city_key": "nyc", "target_date": days[0], "checkpoint": "noon",
               "probs": {B1: 0.9, B2: 0.05, B3: 0.05}},
              {"city_key": "nyc", "target_date": days[1], "checkpoint": "noon", "probs": {B1: 1.0}}]
    d = _run(monkeypatch, db, as_of="2026-09-26")
    row = [r for r in db.stored if r["param"] == "market_weight"][0]
    v = row["value"]
    # an unquoted bucket (checkpoint 99) and an S10 ladder over other buckets are left out
    assert d["market_weight"]["rows"] == 6 and row["n"] == 6
    assert set(v["weights"]) == {"engine:midday", "s10:midday"}
    # 5 and 1 settled days: under MIN_DAYS, so both stay at the prior
    assert v["weights"] == {"engine:midday": 0.0, "s10:midday": 0.0}
    assert v["evidence"]["engine:midday"]["days"] == 5 and "held" in v["evidence"]["engine:midday"]
    assert row["bounds"] == {"w": [0.0, 1.0]} and row["prior"]["w"] == 0.0
    # the same evidence the next night writes nothing
    d2 = _run(monkeypatch, db, as_of="2026-09-27")
    assert d2["market_weight"]["unchanged"] and len([r for r in db.stored if r["param"] == "market_weight"]) == 1


def test_the_market_weight_reads_no_day_it_is_fitted_for(monkeypatch):
    cps = [_mcp(1, "2026-09-25"), _mcp(2, "2026-09-26")]
    db = _DB(cps, [_out(1), _out(2)])
    _run(monkeypatch, db, as_of="2026-09-26")
    row = [r for r in db.stored if r["param"] == "market_weight"][0]
    assert row["n"] == 1 and row["value"]["evidence"]["engine:midday"]["days"] == 1
