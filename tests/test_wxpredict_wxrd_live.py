"""WXPredict on the day, in shadow (tools/wxpredict/wxrd_live.py): wxrd-v2
beside every same-day call the tick writes, at the engine's own checkpoints,
as variant 'wxrd' of variant_shadow_checkpoints. The pure parts here; the
model and the database are the tick's (a rehearsal on 12 Oct's 20:36Z tick
wrote 8 of 8 rows)."""
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import wxrd_live as wl  # noqa: E402
from wxpredict import build_table, common  # noqa: E402

META = json.loads(pathlib.Path(wl.MODEL_JSON).read_text())


def served(city="madrid", checkpoint="noon", day="2026-10-14", top="b2"):
    return {"city_key": city, "target_date": day, "checkpoint": checkpoint,
            "decided_at": "2026-10-14T10:36:29+00:00", "engine_version": "git:abc", "model_path": "forecast",
            "station": "LEMD", "centre_c": 24.1, "sigma_c": 1.2, "top_band_id": top}


def test_the_version_names_the_frozen_model():
    assert wl.variant_version(META) == "wxrd:v2:" + META["model_sha256"][:12]
    assert wl.variant_version(META).startswith(wl.VARIANT + ":"), "the table's check: variant_version like variant || ':%'"


def test_only_the_days_checkpoints_without_a_row_are_due():
    rows = [served(), served(checkpoint="d1_eve"), served(city="paris"), served(checkpoint="postpeak_1h")]
    held = {("paris", "2026-10-14", "noon")}
    assert [(r["city_key"], r["checkpoint"]) for r in wl.due(rows, held)] == [("madrid", "noon"),
                                                                              ("madrid", "postpeak_1h")]


def test_a_row_is_the_tables_shape_and_sums_to_one():
    p = [0.1234567, 0.6, 0.2765433]
    row = wl.variant_row(served(), "C", ["b1", "b2", "b3"], p, "wxrd:v2:62b5de3eace3", False)
    assert row["probs"] == {"b1": 0.123457, "b2": 0.6, "b3": 0.276543}
    assert abs(sum(row["probs"].values()) - 1) <= 1e-4, "the table's check: probs sum to 1 +- 1e-4"
    assert (row["top_band_id"], row["top_prob"]) == ("b2", 0.6)
    assert row["variant"] == "wxrd" and row["decided_at"] == served()["decided_at"]
    assert row["floor_c"] is None and row["q_down"] is None and row["q_up"] is None, "all null or all set"
    assert row["served_calibrated"] is False and row["engine_version"] == "git:abc"


def test_a_tie_goes_to_the_lower_bucket_id():
    row = wl.variant_row(served(), "C", ["b2", "b1"], [0.5, 0.5], "wxrd:v2:x", False)
    assert row["top_band_id"] == "b1"


def test_the_served_ladder_is_the_one_holding_its_top_bucket():
    twin = {"city": "madrid", "date": "2026-10-14", "band_ids": ["a1", "a2"]}
    main = {"city": "madrid", "date": "2026-10-14", "band_ids": ["b1", "b2"]}
    assert wl.event_for([twin, main], served(top="b2")) is main
    assert wl.event_for([twin], served(top="b2")) is None


def test_the_record_is_read_for_the_due_cities_and_their_stations():
    cities = {"madrid": {"icao": "LEMD"}, "nyc": {"icao": "KLGA"}}
    assert wl.key_filter(cities, {"nyc", "madrid"}) == \
        "(key.like.madrid|*,key.like.LEMD|*,key.like.nyc|*,key.like.LGA|*)"


def test_the_model_file_is_the_one_its_record_names():
    import gzip
    import hashlib
    raw = gzip.decompress((pathlib.Path(wl.MODEL_JSON).parent / META["model_file"]).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == META["model_sha256"]
    assert META["report_lag_s"] == 480


def test_the_virtualenv_has_the_versions_the_model_was_fitted_with():
    pins = dict(line.split("==") for line in (ROOT / "requirements.wxpredict.txt").read_text().splitlines()
                if "==" in line and not line.startswith("#"))
    assert pins["lightgbm"] == META["lightgbm"]
    assert {"numpy", "pandas", "scipy", "requests"} <= set(pins)


def _steps():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "tick.yml").read_text())["jobs"]["tick"]["steps"]


def test_the_tick_runs_it_after_the_live_record_and_never_fails_on_it():
    steps = _steps()
    names = [s.get("name") for s in steps]
    i = names.index("WXPredict on the day, in shadow (wxrd-v2)")
    assert names.index("The live record, collected (WXPredict WP3)") < i < names.index("Trade prints, collected")
    step = steps[i]
    assert "tools/wxpredict/wxrd_live.py" in step["run"] and "requirements.wxpredict.txt" in step["run"]
    assert step["continue-on-error"] is True and int(step["timeout-minutes"]) <= 3
    assert "dry_run != 'true'" in step["if"]


def test_every_repo_file_it_reads_is_checked_out():
    step = next(s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout"))
    paths = step["with"]["sparse-checkout"].split()

    def covered(p):
        rel = pathlib.Path(p).resolve().relative_to(ROOT).as_posix()
        return any(rel == q or rel.startswith(q.rstrip("/") + "/") for q in paths)
    need = [wl.MODEL_JSON, pathlib.Path(wl.MODEL_JSON).parent / META["model_file"], __import__("wxrd_live").__file__,
            common.STATION_DAILY, common.station_daily_nightly_dir(), ROOT / "requirements.wxpredict.txt",
            pathlib.Path(common.PR) / "best_match_daily.csv.gz", pathlib.Path(common.PR) / "models_daily.csv.gz",
            sorted(pathlib.Path(common.CITIES_GLOB).parent.glob("cities-*.csv.gz"))[-1],
            ROOT / "scripts" / "common.py", ROOT / "scripts" / "honest_record.py", ROOT / "scripts" / "cost_model.py"]
    missing = [str(p) for p in need if not covered(p)]
    assert not missing, missing
    assert build_table.load_station_daily and build_table.load_daily_forecasts
