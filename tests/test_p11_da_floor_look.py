"""da_floor:v1's looks (tools/p11_da_floor_look.py): the committed result is
what the rule gives on the committed aggregates, and the rule reads as
docs/P11_DA_FLOOR_PREREG.md fixes it."""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import p11_da_floor_look as L  # noqa: E402

FIRST = os.path.join(ROOT, "data", "eval", "p11", "da_floor_first_look_2026-10-25")


def test_the_aggregates_are_the_query_result():
    raw = open(os.path.join(FIRST, "aggregates.txt"), "rb").read()
    assert hashlib.md5(raw).hexdigest() == "b4d0c726a8f0d957d07a6e00fdfd322f"
    assert "b4d0c726a8f0d957d07a6e00fdfd322f" in open(os.path.join(FIRST, "query.sql")).read()


def test_the_committed_first_look_is_what_the_rule_gives():
    rec = json.load(open(os.path.join(FIRST, "result.json")))
    again = L.look(L.read(os.path.join(FIRST, "aggregates.txt")))
    assert {k: rec[k] for k in again} == again
    assert rec["rule"]["verdict"] == "insufficient evidence"
    assert rec["pooled"]["dates"] == 21 and rec["pooled"]["rows"] == 4666


def test_the_result_is_appended_to_the_prereg():
    text = open(os.path.join(ROOT, "docs", "P11_DA_FLOOR_PREREG.md")).read()
    head, _, tail = text.partition("(appended below after the first look; nothing above changes)")
    assert tail and "insufficient evidence" in tail and "insufficient evidence" in head


def _block(lo, hi, top_s=0.5, top_d=0.5, dates=21):
    return {"dates": dates, "log_loss_gain_served_minus_da_floor": {"ci90": [lo, hi]},
            "top1_served": {"rate": top_s, "wilson95": [top_s - 0.01, top_s + 0.01]},
            "top1_da_floor": {"rate": top_d}}


def test_the_rule():
    ok = {c: _block(0.01, 0.2) for c in L.CHECKPOINTS}
    assert L.verdict(_block(0.01, 0.1), ok)["verdict"] == "accepted"
    assert L.verdict(_block(0.01, 0.1, dates=19), ok)["verdict"] == "insufficient evidence"
    assert L.verdict(_block(-0.2, -0.01), ok)["verdict"] == "rejected"
    one_below = dict(ok, postpeak_1h=_block(-0.2, -0.01))
    assert L.verdict(_block(0.01, 0.1), one_below)["verdict"] == "insufficient evidence"
    assert L.verdict(_block(0.01, 0.1, top_s=0.5, top_d=0.48), ok)["verdict"] == "insufficient evidence"
    assert L.verdict(_block(-0.05, 0.06), ok)["verdict"] == "insufficient evidence"


def test_the_pairs_keep_each_dates_sum_and_count():
    rows = [{"date": "d1", "n": 3, "s": 0.9}, {"date": "d1", "n": 1, "s": -0.1}, {"date": "d2", "n": 2, "s": 0.4}]
    p = L.pairs(rows, "n", "s")
    assert len(p) == 6
    assert abs(sum(v for d, v in p if d == "d1") - 0.8) < 1e-12 and abs(sum(v for d, v in p if d == "d2") - 0.4) < 1e-12
