"""Plan v2 P5.0 item 2: exits are evaluated before the worker fills.

Every exit the desk ever queued (5 of 5, 17-19 Sep, measured live 24 Sep)
expired unfilled with a 5-minute life, because pipeline_intraday evaluated
exits AFTER its fill step: the order waited four hours for the next worker.
"""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _steps():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "pipeline_intraday.yml").read_text(encoding="utf-8"))
    return [str(s.get("run", "")) for job in doc["jobs"].values() for s in job["steps"]]


def _index(steps, script):
    hits = [i for i, run in enumerate(steps) if f"scripts/{script}" in run]
    assert len(hits) == 1, f"{script} should run exactly once in pipeline_intraday, found {len(hits)}"
    return hits[0]


def test_exits_are_queued_before_the_worker_fills():
    steps = _steps()
    assert _index(steps, "paper_exits.py") < _index(steps, "paper_worker.py"), \
        "an exit queued after the fill waits for the next run and expires"


def test_exits_do_not_sit_between_the_proposals_and_the_fill():
    """A new BUY lives five minutes from its proposal; nothing slow may run
    between paper_plans and paper_worker."""
    steps = _steps()
    assert _index(steps, "paper_exits.py") < _index(steps, "signal_engine.py")
    assert _index(steps, "paper_worker.py") == _index(steps, "paper_plans.py") + 1


def test_an_exit_order_lives_thirty_minutes():
    sql = (ROOT / "supabase" / "migrations" / "20260924060000_exits_live_thirty_minutes.sql").read_text(encoding="utf-8")
    assert "now()+interval '30 minutes'" in sql
    assert "interval '5 minutes'" not in sql
