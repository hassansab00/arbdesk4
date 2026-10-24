"""Current research uses first-call context, preserves provenance and gates coverage."""
import copy
import csv
import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from wxpredict import arrival_dataset as arrivals, current_dataset as dataset
from wxpredict import current_forecasts, decision_inputs as inputs, features


def case(current=True):
    call = dict(checkpoint_id=12, city_key="city", target_date="2026-10-16", checkpoint="noon",
                decided_at="2026-10-16T12:36:00Z", top_band_id="high",
                market={"low": {"bid": .2, "ask": .4}, "high": {"bid": .4, "ask": .6}})
    event = dict(city="city", date="2026-10-16", station="SYN", unit="C", event_id="event",
                 band_ids=["low", "high"], bands=[(None, 20.), (20., None)])
    row = dict.fromkeys(features.COLUMNS, 0)
    row.update(city_key="city", date="2026-10-16", source="venue", station="SYN", unit="C", n_bands=2,
               bands_lo=";20", bands_hi="20;", decision_utc=int(inputs.unix(call["decided_at"])),
               local_hour=12, rmax_unit=18, rmax_c=18, fc_daily_source="fixture")
    context = dict(zip(current_forecasts.FIELDS, [24, 24, 21.123456789, 21., 7, 22., 1.]))
    payload = inputs.pack(call, event, row, "a" * 40, 0, current=context if current else None)["payload"]
    attached = arrivals.attach(call, payload, event, {"winner": 1, "label_unit": 22.}, include_current=True)
    return call, event, payload, attached


def test_context_is_opt_in_and_missing_inputs_are_not_replayed():
    call, event, payload, row = case()
    legacy = arrivals.attach(call, payload, event, {"winner": 1, "label_unit": 22.})
    assert all(name not in legacy for name in current_forecasts.FIELDS)
    assert dataset.complete(row)
    assert row["recorded_checkpoint_id"] == "12"
    assert row["recorded_row_sha256"] == payload["row_sha256"]
    assert row["recorded_current_sha256"] == payload["current_forecasts_sha256"]
    assert {k: row[k] for k in legacy} == legacy
    _, _, _, missing = case(False)
    assert missing["current_context_available"] == 0 and not dataset.complete(missing)
    assert all(missing[name] is None for name in current_forecasts.FIELDS)
    bad = copy.deepcopy(payload)
    bad["current_forecasts"]["current_max_c"] = 99.
    with pytest.raises(ValueError, match="input hash mismatch"):
        arrivals.attach(call, bad, event, {}, include_current=True)
    bad["current_forecasts"] = None
    with pytest.raises(ValueError, match="unexpected"):
        inputs.current_context(bad)


def test_coverage_keeps_all_first_calls_in_the_denominator():
    _, _, _, row = case()
    rows = [{**row, "city_key": str(city), "checkpoint": cp}
            for cp in dataset.CHECKPOINTS for city in range(20)]
    denominator=len(rows)/.8
    report = dataset.coverage(rows, {"first_calls": denominator}, "2026-10-16", "2026-10-16")
    assert report["coverage_ready"] and report["current_coverage"] == .8
    assert not report["changes_serving"]
    assert not dataset.coverage(rows, {"first_calls": denominator+1}, "2026-10-16", "2026-10-16")["coverage_ready"]
    assert not dataset.coverage(rows[:-1], {"first_calls": denominator}, "2026-10-16", "2026-10-16")["coverage_ready"]
    assert report['checkpoints']==list(dataset.CHECKPOINTS) and 'morning' not in dataset.CHECKPOINTS
    assert dataset.complete({**row, "current_hours": 25, "expected_hours": 25})
    assert not dataset.complete({**row, "current_hours": 24, "expected_hours": 25})
    assert not dataset.complete({**row, "current_max_c": float("nan")})
    with pytest.raises(ValueError, match="duplicate"):
        dataset.coverage(rows + [rows[0]], {"first_calls": denominator}, "2026-10-16", "2026-10-16")
    with pytest.raises(ValueError, match="denominator"):
        dataset.coverage(rows, {"first_calls": len(rows)-1}, "2026-10-16", "2026-10-16")
    with pytest.raises(ValueError, match="reversed"):
        dataset.coverage([], {"first_calls": 0}, "2026-10-17", "2026-10-16")


def test_export_is_stable_and_keeps_full_precision_and_provenance(tmp_path, monkeypatch):
    _, _, _, row = case()
    table = tmp_path / "labels.csv.gz"
    table.write_bytes(gzip.compress(b"label fixture"))
    def build(*args, **kw):
        assert kw == {"include_current": True,"checkpoints":dataset.CHECKPOINTS}
        return [row], {"first_calls": 2, "paired_rows": 1}
    monkeypatch.setattr(arrivals, "build", build)
    out = tmp_path / "dataset.csv.gz"
    report = dataset.main(["--from", "2026-10-16", "--to", "2026-10-16", "--through",
                           "2026-10-18T00:00:00Z", "--labels-table", str(table), "--out", str(out)])
    assert report["complete_current_rows"] == 1 and not report["coverage_ready"]
    assert report["dataset_sha256"] == dataset.write(str(out), [row])
    assert report["labels_table_sha256"] == hashlib.sha256(table.read_bytes()).hexdigest()
    assert json.loads(Path(str(out) + ".json").read_text())["uses_reconstructed_features"] is False
    with gzip.open(out, "rt") as fh:
        saved = list(csv.DictReader(fh))[0]
    assert saved["current_max_c"] == str(row["current_max_c"])
    assert saved["recorded_current_sha256"] == row["recorded_current_sha256"]


def test_feature_adapter_excludes_provenance_outcomes_and_future_columns(monkeypatch):
    pytest.importorskip("lightgbm")
    pd = pytest.importorskip("pandas")
    monkeypatch.setattr(sys, "path", list(sys.path))
    from wxpredict import current_recipe
    _, _, _, row = case()
    df = pd.DataFrame([row, {**row, "unit": "F"}])
    original = current_recipe.features(df)
    changed = df.copy()
    for label in inputs.LABELS:
        changed[label] = -999
    changed["recorded_checkpoint_id"] = "a different checkpoint"
    changed["recorded_row_sha256"] = "a different hash"
    changed["checkpoint"] = "a different checkpoint name"
    changed["future_outcome"] = 999
    pd.testing.assert_frame_equal(original, current_recipe.features(changed))
    assert original.current_gap_models_mean_c.tolist() == pytest.approx([4., 3.6])
    assert original.current_hour_coverage.tolist() == [1., 1.]
    assert not any(c.startswith("recorded_") or c in inputs.LABELS for c in original.columns)
