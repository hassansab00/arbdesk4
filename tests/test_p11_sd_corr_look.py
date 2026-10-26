"""sd_corr:v1's looks (tools/p11_sd_corr_look.py): the committed first look is
what the rule gives on the committed mirror, and the rule reads as
docs/SD_CORR_PREREG.md fixes it."""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import p11_sd_corr_look as L  # noqa: E402

FIRST = os.path.join(ROOT, "data", "eval", "p11", "sd_corr_first_look_2026-10-26")


def test_the_first_look_names_the_mirror_files_it_read():
    rec = json.load(open(os.path.join(FIRST, "result.json")))
    assert rec["sources"], "every file read is named"
    for path, sha in rec["sources"].items():
        assert path.startswith("data/mirror/") and len(sha) == 64
    assert rec["rule"]["verdict"] == "insufficient evidence"
    assert rec["pooled"]["dates"] == 21 and rec["pooled"]["rows"] == 4656


def test_the_first_look_reproduces_from_the_files_it_names():
    """The mirror appends later nights as new files; the look is rebuilt from
    exactly the files it names, each still holding the bytes it read."""
    rec = json.load(open(os.path.join(FIRST, "result.json")))
    for path, sha in rec["sources"].items():
        with open(os.path.join(ROOT, path), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == sha, f"{path} changed since the look"
    rows, left, _ = L.rows(only=set(rec["sources"]))
    assert dict(left) == rec["left_out"]
    again = L.look(rows)
    assert {k: rec[k] for k in again} == again


def _block(lo, hi, top_s=0.5, top_c=0.5, dates=21):
    return {"dates": dates, "log_loss_gain_served_minus_sd_corr": {"ci90": [lo, hi]},
            "top1_served": {"rate": top_s, "wilson95": [top_s - 0.01, top_s + 0.01]},
            "top1_sd_corr": {"rate": top_c}}


def test_the_rule():
    ok = {c: _block(0.01, 0.2) for c in L.CHECKPOINTS}
    assert L.verdict(_block(0.01, 0.1), ok)["verdict"] == "accepted"
    assert L.verdict(_block(0.01, 0.1, dates=19), ok)["verdict"] == "insufficient evidence"
    assert L.verdict(_block(-0.2, -0.01), ok)["verdict"] == "rejected"
    assert L.verdict(_block(0.01, 0.1), dict(ok, noon=_block(-0.2, -0.01)))["verdict"] == "insufficient evidence"
    # rule 3 is per checkpoint: one checkpoint's top-1 below the served Wilson lower end holds it
    one_low = dict(ok, postpeak_1h=_block(0.01, 0.2, top_s=0.78, top_c=0.70))
    assert L.verdict(_block(0.01, 0.1), one_low)["verdict"] == "insufficient evidence"


def test_ties_go_to_the_smallest_bucket_id():
    ll, top, br = L.score({"b": 0.4, "a": 0.4, "c": 0.2}, "a")
    assert top == 1
    assert L.score({"b": 0.4, "a": 0.4, "c": 0.2}, "b")[1] == 0
    assert abs(br - (0.36 + 0.16 + 0.04)) < 1e-12
