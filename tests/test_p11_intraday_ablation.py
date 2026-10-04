"""P1.1's component diagnosis is reproducible and its replay is the engine.

tools/p11_intraday_ablation.py recomputes each same-day checkpoint ladder
from recorded inputs (data/eval/p11/rows_*.json.gz) with the engine's own
integrator. These tests hold three things: the served ladders come back
exactly (so the variants are the engine with one component switched), q is
the value the engine read at that hour, and the committed report is what the
committed inputs produce."""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import p11_intraday_ablation as abl   # noqa: E402

ROWS = sorted(glob.glob(os.path.join(ROOT, "data", "eval", "p11", "rows_*.json.gz")))


def test_the_replay_reproduces_every_served_ladder():
    rows = abl.load(ROWS)
    check = abl.replay_check(rows)
    assert check["rows"] == 1649
    assert check["max_abs_diff"] < 1e-4, check
    assert check["rows_off_by_1e-3"] == 0


def test_q_is_the_value_read_at_that_hour():
    r = {"city": "nyc", "decided_at": "2026-10-01T04:36:30+00:00"}
    before = abl.q_at(r)
    after = abl.q_at(dict(r, decided_at="2026-10-01T05:36:30+00:00"))
    snaps = abl.snapshots()
    want_before = tuple(float(x) for x in snaps["2026-10-01"]["nyc"])
    want_after = tuple(float(x) for x in snaps["2026-10-02"]["nyc"])
    assert before == want_before and after == want_after


def test_the_committed_report_is_what_the_inputs_give():
    fresh = abl.run(abl.load(ROWS))
    committed = json.load(open(os.path.join(ROOT, "data", "eval", "p11", "report.json")))
    assert json.loads(json.dumps(fresh, sort_keys=True)) == committed


def test_the_document_quotes_the_report():
    doc = open(os.path.join(ROOT, "docs", "P11_INTRADAY_DIAGNOSIS_2026-10-04.md")).read()
    rep = json.load(open(os.path.join(ROOT, "data", "eval", "p11", "report.json")))
    m = rep["by_checkpoint"]["morning"]
    assert f"{m['variants']['served']['log_loss']['mean']:.3f}" in doc
    assert f"**{m['variants']['da_floor']['log_loss']['mean']:.3f}**" in doc
    d = m["differences_log_loss"]["served_minus_da_floor (switching centre and width, floor held)"]
    assert f"{d['mean']:+.3f} [{d['ci90'][0]:+.3f}, {d['ci90'][1]:+.3f}]" in doc
