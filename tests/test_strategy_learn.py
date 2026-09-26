"""The nightly learning loop (plan v2 P5.8), part 1: belief maps into
strategy_params, walk-forward, one row per version, and nothing used until the
flag says so."""
import datetime as dt
import sys
import types

import pytest

import belief
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
        self.logged, self.upserts = [], []

    def module(self):
        m = types.ModuleType("common")
        m.rest = lambda path, params=None, **k: (
            [{"value": r["value"], "version": r["version"]} for r in self.stored[-1:]]
            if path == "strategy_params" else [])

        def rest_all(path, params=None, *, order, page_size=500):
            return self.checkpoints if path == "prediction_checkpoints" else self.outcomes
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


def _run(monkeypatch, db, as_of="2026-09-26", dry=False):
    monkeypatch.setitem(sys.modules, "common", db.module())
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
