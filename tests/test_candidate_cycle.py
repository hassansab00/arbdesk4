"""A refit cannot grade its training days, hide a regression, or skip rollback checks."""
import copy
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from wxpredict import candidate_cycle as cycle, cycle_registry as registry, model_artifacts


def scores():
    out = []
    for day in range(14):
        for cp in cycle.CHECKPOINTS:
            for city in range(20):
                out.append({"city": str(city), "date": (dt.date(2026, 10, 14) + dt.timedelta(days=day)).isoformat(),
                            "checkpoint": cp, "candidate_hit": True, "incumbent_hit": False, "market_hit": True,
                            "candidate_nll": .4, "incumbent_nll": .6, "market_nll": .3, "jump": .05})
    return out


def assess(rows, expected=1400):
    return cycle.assess(rows, expected, "2026-10-14", "2026-10-27")


def test_the_window_is_out_of_sample_for_both_and_new_data_must_exist():
    current = {"fit_last": "2026-10-11"}
    assert cycle.plan(dt.date(2026, 10, 15), current)["decision"] == "wait"
    p = cycle.plan(dt.date(2026, 10, 29), current)
    assert p["decision"] == "ready" and p["fit_last"] == "2026-10-12"
    assert p["evaluation_first"] == "2026-10-14" and p["evaluation_last"] == "2026-10-27"
    assert cycle.plan(dt.date(2026, 10, 29), current, paused=True)["reason"] == "paused by rollback"
    assert cycle.plan(dt.date(2026, 10, 29), {"fit_last": "2026-10-12"})["decision"] == "wait"


def test_improvement_requires_coverage_every_checkpoint_and_a_bounded_step():
    rows = scores()
    good = assess(rows)
    assert good["decision"] == "accept shadow" and good["nll_gain_ci90_by_date"][0] > 0
    assert assess(rows, 2000)["decision"] == "reject"
    thin = [r for r in rows if not (r["checkpoint"] == "morning" and r["date"] == "2026-10-14")]
    assert "insufficient dates/cities per checkpoint" in assess(thin)["reasons"]
    bad = copy.deepcopy(rows)
    for r in bad:
        if r["checkpoint"] == "morning":
            r.update(candidate_nll=.7, candidate_hit=False, incumbent_hit=True)
    assert assess(bad)["regressed_checkpoints"] == ["morning"]
    bad = copy.deepcopy(rows); bad[0]["jump"] = cycle.MAX_BUCKET_CHANGE + .001
    assert "prediction change exceeds its bound" in assess(bad)["reasons"]
    bad = copy.deepcopy(rows)
    for r in bad: r["candidate_nll"] = r["incumbent_nll"]
    assert "no positive date-clustered log-loss gain" in assess(bad)["reasons"]
    with pytest.raises(ValueError, match="duplicate"):
        assess(rows + [rows[0]])


def test_only_accepted_hash_checked_lineage_can_become_shadow_or_rollback(tmp_path, monkeypatch):
    report = {"night": "2026-10-29", "decision": "accept shadow", "changes_serving": False}
    sha = hashlib.sha256(b"model").hexdigest()
    a = {"version": "wxrd-auto-20261029-" + sha[:12], "model_sha256": sha, "asset_url": "unused-in-this-test"}
    registry.record(report, a, tmp_path)
    path, meta = registry.selected(tmp_path)
    assert meta == a
    with pytest.raises(ValueError, match="already recorded"):
        registry.record(report, a, tmp_path)
    b = {**a, "version": "wxrd-auto-20261030-" + sha[:12]}
    registry.record({**report, "night": "2026-10-30"}, b, tmp_path)
    monkeypatch.setattr(model_artifacts, "ensure", lambda meta, root=None: None)
    registry.rollback(a["version"], tmp_path)
    assert registry.registry(tmp_path)["paused"] is True
    assert registry.selected(tmp_path)[1] == a
    with pytest.raises(ValueError, match="paused"):
        registry.record({**report, "night": "2026-10-31"}, b, tmp_path)
    pathlib.Path(path).write_text("changed")
    with pytest.raises(ValueError, match="metadata hash"):
        registry.selected(tmp_path)
    registry.rollback("baseline", tmp_path)
    assert registry.selected(tmp_path) is None and registry.registry(tmp_path)["paused"]


def test_partial_unregistered_metadata_can_resume_but_cannot_be_replaced(tmp_path):
    meta = {"version": "wxrd-auto-20261029-abc", "model_sha256": "a" * 64}
    path = tmp_path / "data/models/wxrd/candidates" / (meta["version"] + ".json")
    registry.write(str(path), meta)
    assert registry.selected(tmp_path) is None
    registry.record({"night": "2026-10-29", "decision": "accept shadow"}, meta, tmp_path)
    assert registry.selected(tmp_path)[1] == meta
    assert not registry.registry(tmp_path).get("changes_serving", False)


def test_candidate_weight_integrity_and_source_are_checked_before_import(tmp_path):
    raw = b"known model bytes"; blob = gzip.compress(raw)
    sha = hashlib.sha256(raw).hexdigest()
    assert model_artifacts.validate_blob(blob, sha) == raw
    with pytest.raises(ValueError, match="hash mismatch"):
        model_artifacts.validate_blob(blob, "0" * 64)
    with pytest.raises(ValueError, match="this repository"):
        model_artifacts.ensure({"asset_url": "https://elsewhere.invalid/model.gz", "model_sha256": sha}, tmp_path)
    url = "https://github.com/hassansab00/arbdesk4/releases/download/test/m.txt.gz"
    path = tmp_path / ".cache/wxpredict-models" / (sha + ".txt.gz")
    path.parent.mkdir(parents=True); path.write_bytes(blob)
    assert model_artifacts.ensure({"asset_url": url, "model_sha256": sha}, tmp_path) == str(path)
    path.write_bytes(gzip.compress(b"different"))
    with pytest.raises(ValueError, match="hash mismatch"):
        model_artifacts.ensure({"asset_url": url, "model_sha256": sha}, tmp_path)


def test_the_real_fit_and_arrival_inference_pipeline_runs_without_promoting_ties(tmp_path, monkeypatch):
    pytest.importorskip("lightgbm")
    # Legacy research modules prepend their own directory while importing.
    # Keep that script-local behaviour from changing later tests' imports.
    monkeypatch.setattr(sys, "path", list(sys.path))
    import pandas as pd
    from wxpredict import features, build_table, remaining, wxrd_live, arrival_dataset
    def row(day, city="synthetic", checkpoint="morning"):
        r = {k: 0 for k in features.COLUMNS}
        r.update(event_id="event-" + day + city, source="venue", city_key=city, station="SYN",
                 date=day, unit="C", n_bands=2, bands_lo=";20", bands_hi="20;", winner=1,
                 label_unit=20, day_offset=0, local_hour=9, decision_utc=1800000000,
                 decision_local=day + "T09:01:00+00:00", rmax_unit=18, rmax_c=18, t_now_c=18,
                 fc_daily_source="synthetic", mkt_complete=1, mkt_prices="0.4;0.6", mkt_ages_min="1;1",
                 live_mode="arrival", checkpoint=checkpoint)
        return r
    training = pd.DataFrame([row("2026-10-10", "c" + str(i)) for i in range(40)])[features.COLUMNS]
    table = tmp_path / "wxpredict_table.csv.gz"; training.to_csv(table, index=False, compression="gzip")
    monkeypatch.setattr(build_table, "build", lambda **k: {"sha256": "synthetic-table", "sources": []})
    eval_rows = [row(r["date"], r["city"], r["checkpoint"]) for r in scores() if r['checkpoint'] in cycle.REMAINING_CHECKPOINTS]
    def build_evaluation(*args,**kwargs):
        assert kwargs=={'checkpoints':cycle.REMAINING_CHECKPOINTS}
        assert not fitted, 'Contract/label checks must precede expensive model fitting'
        return eval_rows, {'first_calls':len(eval_rows)}
    monkeypatch.setattr(arrival_dataset, "build", build_evaluation)
    monkeypatch.setattr(cycle, "MIN_TRAIN_ROWS", 20)
    monkeypatch.setattr(cycle, "ROUNDS", 2)
    real_fit = remaining.fit
    fitted = []
    def fit(*a):
        model = real_fit(*a); fitted.append(model); return model
    monkeypatch.setattr(remaining, "fit", fit)
    monkeypatch.setattr(wxrd_live, "load_model", lambda *a: fitted[0])
    p = cycle.plan(dt.date(2026, 10, 29), {"fit_last": "2026-10-11"})
    result, meta, weight = cycle.fit_and_evaluate(p, "unused", {"fit_last": "2026-10-11", "model_sha256": "baseline"}, str(tmp_path))
    assert result["decision"] == "reject" and "no positive date-clustered log-loss gain" in result["reasons"]
    assert result["maximum_bucket_change"] == pytest.approx(0,abs=1e-12) and result["all"]["n"] == len(eval_rows)
    assert meta["fit_last"] < p["evaluation_first"]
    assert len(weight)==len(meta['models'])==5
    for path,entry in zip(weight,meta['models']):
        assert model_artifacts.validate_blob(pathlib.Path(path).read_bytes(),entry['sha256'])


def test_failed_artifact_publication_cannot_change_the_registry(tmp_path, monkeypatch):
    from wxpredict import common, arrival_dataset
    monkeypatch.setattr(common, "ROOT", str(tmp_path))
    monkeypatch.setattr(cycle, "incumbent", lambda: ("unused", {"fit_last": "2026-10-11"}))
    ready = cycle.plan(dt.date(2026, 10, 29), {"fit_last": "2026-10-11"})
    monkeypatch.setattr(cycle, "plan", lambda *a: ready)
    def preflight(*args,**kwargs):
        assert kwargs=={'checkpoints':cycle.REMAINING_CHECKPOINTS}
        return {'ready':True}
    monkeypatch.setattr(arrival_dataset, "preflight", preflight)
    monkeypatch.setattr(cycle, "fit_and_evaluate", lambda *a: ({**ready, "decision": "accept shadow"},
        {"version": "synthetic", "model_sha256": "a"*64}, "unused"))
    def refused(*a):
        raise ValueError("public artifact read-back hash failed")
    monkeypatch.setattr(model_artifacts, "publish", refused)
    before = registry.registry(tmp_path)
    with pytest.raises(ValueError, match="read-back"):
        cycle.main([])
    assert registry.registry(tmp_path) == before and registry.selected(tmp_path) is None
    assert not (tmp_path / "data/eval/wxpredict/cycles").exists()


def test_remaining_candidate_scope_can_pass_without_unsupported_morning_rows():
    rows=[r for r in scores() if r['checkpoint'] in cycle.REMAINING_CHECKPOINTS]
    result=cycle.assess(rows,len(rows),'2026-10-14','2026-10-27',
        checkpoints=cycle.REMAINING_CHECKPOINTS,improvement_checkpoints=cycle.PREPEAK_CHECKPOINTS)
    assert result['decision']=='accept shadow' and not result['sparse_checkpoint_dates']
    assert 'morning' not in result['checkpoints']
    assert result['objective']['n'] < result['all']['n']
    assert cycle.plan(dt.date(2026,10,29),{'fit_last':'2026-10-11'})['policy']['evaluation_checkpoints']==list(cycle.REMAINING_CHECKPOINTS)
    thin=[r for r in rows if not(r['checkpoint']=='noon' and r['date']=='2026-10-14')]
    assert 'insufficient dates/cities per checkpoint' in cycle.assess(thin,len(rows),'2026-10-14','2026-10-27',
        checkpoints=cycle.REMAINING_CHECKPOINTS,improvement_checkpoints=cycle.PREPEAK_CHECKPOINTS)['reasons']


def test_postpeak_gain_alone_cannot_pass_the_primary_forecast_objective():
    rows=[dict(r) for r in scores() if r['checkpoint'] in cycle.REMAINING_CHECKPOINTS]
    for r in rows:
        if r['checkpoint']!='postpeak_1h': r.update(candidate_nll=r['incumbent_nll'],candidate_hit=r['incumbent_hit'])
    result=cycle.assess(rows,len(rows),'2026-10-14','2026-10-27',
        checkpoints=cycle.REMAINING_CHECKPOINTS,improvement_checkpoints=cycle.PREPEAK_CHECKPOINTS)
    assert result['decision']=='reject' and 'no positive date-clustered log-loss gain' in result['reasons']
    assert result['objective']['candidate_nll']==result['objective']['incumbent_nll']
    with pytest.raises(ValueError,match='scope'):
        cycle.assess(rows,len(rows),'2026-10-14','2026-10-27',checkpoints=())
