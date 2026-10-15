"""The live audit cannot grade training dates or hide a checkpoint regression."""
import copy
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from wxpredict import forward_audit as fa  # noqa: E402

VERSION = "wxrd3:v3:hash"


def fixture():
    p = {"checkpoint_id": "id", "city_key": "london", "target_date": "2026-10-13", "checkpoint": "noon",
         "decided_at": "2026-10-13T12:36:00Z", "top_band_id": "b", "probs": {"a": .4, "b": .6}}
    v = {k: p[k] for k in ("city_key", "target_date", "checkpoint", "decided_at")}
    v.update(variant_version=VERSION, top_band_id="a", probs={"a": .7, "b": .3})
    o = {"checkpoint_id": "id", "winner_band_id": "a"}
    return p, o, v


def test_only_the_same_first_decision_and_settled_winner_are_scored():
    p, o, v = fixture()
    later = dict(p, checkpoint_id="later", decided_at="2026-10-13T12:37:00Z", top_band_id="a")
    r = fa.score([later, p], [o], [v], "2026-10-11", VERSION)
    assert (r["all"]["n"], r["all"]["candidate_hits"], r["all"]["serving_hits"]) == (1, 1, 0)
    assert r["all"]["candidate_nll"] < r["all"]["serving_nll"]
    assert r["gate"] == "insufficient forward dates"
    assert r["changes_serving"] is False
    v["decided_at"] = later["decided_at"]
    r = fa.score([p, later], [o], [v], "2026-10-11", VERSION)
    assert r["all"]["n"] == 0 and r["excluded"] == {"not_the_first_decision": 1}


def test_training_dates_unknown_versions_and_unsettled_calls_are_not_scores():
    p, o, v = fixture()
    assert fa.score([p], [o], [v], "2026-10-13", VERSION)["excluded"] == {"in_sample": 1}
    assert fa.score([p], [o], [dict(v, variant_version="other")], "2026-10-11", VERSION)["excluded"] == {"other_version": 1}
    assert fa.score([p], [], [v], "2026-10-11", VERSION)["excluded"] == {"not_settled": 1}


def test_morning_regression_is_named_even_if_later_calls_improve():
    p, o, v = fixture()
    morning = dict(p, checkpoint_id="morning", checkpoint="morning", decided_at="2026-10-13T09:36:00Z",
                   top_band_id="a", probs={"a": .8, "b": .2})
    vm = dict(v, checkpoint="morning", decided_at=morning["decided_at"], top_band_id="b", probs={"a": .2, "b": .8})
    r = fa.score([p, morning], [o, dict(o, checkpoint_id="morning")], [v, vm], "2026-10-11", VERSION)
    assert r["regressed_checkpoints"] == ["morning"]
    assert r["all"]["candidate_hits"] == r["all"]["serving_hits"]


def test_duplicate_calls_and_invalid_probability_or_bucket_sets_are_refused():
    p, o, v = fixture()
    with pytest.raises(ValueError, match="duplicate candidate"):
        fa.score([p], [o], [v, v], "2026-10-11", VERSION)
    for probs in ({"a": 1, "b": 1}, {"a": float("nan"), "b": .4}, {"different": 1}):
        bad = copy.deepcopy(v)
        bad["probs"] = probs
        with pytest.raises(ValueError):
            fa.score([p], [o], [bad], "2026-10-11", VERSION)


def test_review_gate_needs_fourteen_dates_in_every_early_checkpoint():
    import datetime as dt
    checkpoints, outcomes, variants = [], [], []
    for day in range(14):
        date = (dt.date(2026, 10, 12) + dt.timedelta(days=day)).isoformat()
        for cp in fa.CHECKPOINTS[:-1]:
            p, o, v = fixture()
            identity = date + cp
            p.update(checkpoint_id=identity, target_date=date, checkpoint=cp,
                     decided_at=date + "T12:36:00Z")
            o["checkpoint_id"] = identity
            v.update(target_date=date, checkpoint=cp, decided_at=p["decided_at"])
            checkpoints.append(p)
            outcomes.append(o)
            variants.append(v)
    got = fa.score(checkpoints, outcomes, variants, "2026-10-11", VERSION)
    assert got["gate"] == "ready for review" and got["changes_serving"] is False
    assert got["before_peak"]["n"] == 56
    assert fa.score(checkpoints, outcomes, variants[:-1], "2026-10-11", VERSION)["gate"] == "insufficient forward dates"
    morning = next(v for v in variants if v["checkpoint"] == "morning")
    morning.update(top_band_id="b", probs={"a": .000001, "b": .999999})
    assert fa.score(checkpoints, outcomes, variants, "2026-10-11", VERSION)["gate"] == "checkpoint regression"
