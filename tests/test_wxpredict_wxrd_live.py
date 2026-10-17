"""WXPredict on the day, in shadow (tools/wxpredict/wxrd_live.py): wxrd-v2
beside every same-day call the tick writes, at the engine's own checkpoints,
as variant 'wxrd' of variant_shadow_checkpoints. The pure parts here; the
model and the database are the tick's (a rehearsal on 12 Oct's 20:36Z tick
wrote 8 of 8 rows)."""
import datetime as dt
import json
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import wxrd_live as wl  # noqa: E402
from wxpredict import build_table, common  # noqa: E402

META = json.loads(pathlib.Path(wl.MODEL_JSON).read_text())


def served(city="madrid", checkpoint="noon", day="2026-10-14", top="b2", decided_at="2026-10-14T10:36:29+00:00"):
    return {"city_key": city, "target_date": day, "checkpoint": checkpoint,
            "decided_at": decided_at, "engine_version": "git:abc", "model_path": "forecast",
            "station": "LEMD", "centre_c": 24.1, "sigma_c": 1.2, "top_band_id": top}


def test_the_version_names_the_frozen_model():
    assert wl.variant_version(META) == "wxrd:v2:" + META["model_sha256"][:12]
    assert wl.variant_version(META).startswith(wl.VARIANT + ":"), "the table's check: variant_version like variant || ':%'"


def test_only_the_days_checkpoints_without_a_row_are_due():
    rows = [served(), served(checkpoint="d1_eve"), served(city="paris"), served(checkpoint="postpeak_1h")]
    held = {("paris", "2026-10-14", "noon")}
    todo, deferred = wl.due(rows, held)
    assert [(r["city_key"], r["checkpoint"]) for r in todo] == [("madrid", "noon"), ("madrid", "postpeak_1h")]
    assert deferred == 0


def test_a_failed_tick_leaves_its_checkpoints_to_the_next_oldest_first():
    """Codex on #395: a tick whose step failed must not lose its checkpoints.
    The run looks RETRY_HOURS back, so they are due again at the next tick;
    oldest first, at most MAX_DUE, the rest counted and left for later."""
    rows = [served(city=f"c{k:02d}", decided_at=f"2026-10-14T{10 + k % 3:02d}:36:00+00:00") for k in range(5)]
    todo, deferred = wl.due(rows, set(), limit=3)
    assert [r["decided_at"][11:13] for r in todo] == ["10", "10", "11"] and deferred == 2
    assert wl.RETRY_HOURS * 60 > wl.SERVED_WITHIN_MIN
    assert wl.RETRY_HOURS + wl.RECORD_HOURS <= 48, "the record a retry reads is inside the prune's 2 days"
    src = (ROOT / "tools" / "wxpredict" / "wxrd_live.py").read_text()
    assert 'since = (now - dt.timedelta(hours=RETRY_HOURS))' in src
    assert '("captured_at", f"gte.{(oldest(todo) - dt.timedelta(hours=RECORD_HOURS))' in src
    assert wl.oldest(todo) == dt.datetime(2026, 10, 14, 10, 36, tzinfo=dt.timezone.utc)


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


def test_a_failed_install_is_never_cached():
    """Codex on #395: the cache saves .venv-wx after the job, so a half-built
    environment there would be restored and reused every hour. It is built in
    .venv-wx-build and moved into place only after its imports work."""
    steps = _steps()
    cache = next(s for s in steps if s.get("id") == "wxvenv")
    assert cache["with"]["path"] == ".venv-wx"
    run = next(s for s in steps if s.get("name") == "WXPredict on the day, in shadow (wxrd-v2)")["run"]
    lines = [ln.strip() for ln in run.splitlines() if ln.strip()]
    build = [ln for ln in lines if ".venv-wx-build" in ln]
    assert build[0] == "rm -rf .venv-wx-build" and build[-1] == "mv .venv-wx-build .venv-wx"
    imports = next(i for i, ln in enumerate(lines) if ln.startswith(".venv-wx-build/bin/python -c"))
    assert all(m in lines[imports] for m in ("lightgbm", "numpy", "pandas", "scipy", "requests"))
    assert "pip install" in lines[imports - 1] and imports < lines.index(build[-1]), \
        "installed, then the imports checked, then moved into place"
    assert not any(ln.startswith(("python -m venv .venv-wx ", ".venv-wx/bin/pip")) for ln in lines), \
        "nothing is ever installed at the cached path itself"


def test_every_repo_file_it_reads_is_checked_out():
    step = next(s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout"))
    paths = step["with"]["sparse-checkout"].split()

    def covered(p):
        rel = pathlib.Path(p).resolve().relative_to(ROOT).as_posix()
        return any(rel == q or rel.startswith(q.rstrip("/") + "/") for q in paths)
    need = [wl.MODEL_JSON, pathlib.Path(wl.MODEL_JSON).parent / META["model_file"], __import__("wxrd_live").__file__,
            common.STATION_DAILY, common.station_daily_nightly_dir(), ROOT / "requirements.wxpredict.txt", ROOT / "data/mirror/manifest.json",
            ROOT / "data/mirror/live_capture",
            pathlib.Path(common.PR) / "best_match_daily.csv.gz", pathlib.Path(common.PR) / "models_daily.csv.gz",
            sorted(pathlib.Path(common.CITIES_GLOB).parent.glob("cities-*.csv.gz"))[-1],
            ROOT / "scripts" / "common.py", ROOT / "scripts" / "honest_record.py", ROOT / "scripts" / "cost_model.py"]
    for model_json, _ in wl.VARIANTS.values():          # every variant's frozen model and its files
        need.append(pathlib.Path(model_json))
        if pathlib.Path(model_json).exists():
            meta = json.loads(pathlib.Path(model_json).read_text())
            files = [m["file"] for m in meta["models"]] if "models" in meta else [meta["model_file"]]
            need += [pathlib.Path(model_json).parent / f for f in files]
    missing = [str(p) for p in need if not covered(p)]
    assert not missing, missing
    assert build_table.load_station_daily and build_table.load_daily_forecasts


class _Ops:
    """The database helpers, faked: rest_all answers by table."""
    def __init__(self, tables):
        self.tables, self.upserts, self.logs = tables, [], []

    def rest_all(self, table, params, order=None, page_size=None):
        return list(self.tables.get(table, []))

    def upsert(self, table, rows, on_conflict):
        self.upserts.append((table, rows))
        return len(rows)

    def log_run(self, job, status, n, detail):
        self.logs.append((job, status, n, detail))


def _run_with(monkeypatch, seconds_after_start):
    import types
    served_rows = [served(city=c, decided_at="2026-10-14T08:36:29+00:00") for c in ("madrid", "paris", "rome")]
    siblings = [dict(r, variant="honest", served_calibrated=False) for r in served_rows]
    fake = _Ops({"prediction_checkpoints": served_rows, wl.TABLE: siblings})
    from wxpredict import capture_history
    monkeypatch.setattr(capture_history, "read", lambda *a, **k: [])
    monkeypatch.setattr(common, "ops", lambda: fake)
    monkeypatch.setattr(common, "cities", lambda: {c: {"icao": None} for c in ("madrid", "paris", "rome")})
    monkeypatch.setattr(wl.lr, "live_events", lambda *a, **k: [])
    monkeypatch.setattr(build_table, "load_station_daily", lambda: ({}, None))
    monkeypatch.setattr(build_table, "load_daily_forecasts", lambda: ({}, {}))
    clock = iter([0.0] + [float(seconds_after_start)] * 50)
    monkeypatch.setattr(wl, "time", types.SimpleNamespace(time=lambda: next(clock)))
    detail = wl.run(now=dt.datetime(2026, 10, 14, 12, 40, tzinfo=dt.timezone.utc))
    return detail, fake


def test_past_its_time_budget_the_rest_is_left_for_the_next_tick(monkeypatch):
    """A backlog must not time the step out hour after hour: past
    ROW_BUDGET_S no more rows are built, the rest is counted as deferred, and
    the run still logs. These three were decided 4 h before: retried."""
    detail, fake = _run_with(monkeypatch, wl.ROW_BUDGET_S + 1)
    assert (detail["due"], detail["deferred"], detail["retried"], detail["written"]) == (3, 3, 3, 0)
    assert fake.upserts == [] and fake.logs[0][:3] == (wl.JOB, "ok", 0)
    detail, fake = _run_with(monkeypatch, 1)
    assert detail["deferred"] == 0 and detail["skipped"] == {"no whole ladder": 3}, "inside the budget: each tried"


def test_the_five_seed_variant_names_its_version_and_its_models(tmp_path):
    """wxrd3 (13 Oct): five seeds, their ladders averaged. Its version's id is
    the sha256 of the models' sha256s in seed order, so a row's version names
    exactly the models that priced it; a model whose bytes differ is refused."""
    import gzip
    import hashlib
    v3 = {"version": "wxrd-v3", "models": [], "report_lag_s": 480}
    for seed in (1, 2):
        text = f"model {seed}".encode()
        (tmp_path / f"m{seed}.txt.gz").write_bytes(gzip.compress(text))
        v3["models"].append({"seed": seed, "file": f"m{seed}.txt.gz", "sha256": hashlib.sha256(text).hexdigest()})
    v3["model_sha256"] = hashlib.sha256("".join(m["sha256"] for m in v3["models"]).encode()).hexdigest()
    assert wl.variant_version(v3, "wxrd3") == "wxrd3:v3:" + v3["model_sha256"][:12]
    assert wl.variant_version(META) == "wxrd:v2:" + META["model_sha256"][:12], "v2's version is unchanged"
    bad = dict(v3, model_sha256="0" * 64)
    try:
        wl.load_model(bad, str(tmp_path / "x.json"))
        raise AssertionError("an id that is not its models' must be refused")
    except SystemExit as exc:
        assert "do not make its id" in str(exc)
    tampered = dict(v3, models=[dict(v3["models"][0], sha256="1" * 64), v3["models"][1]])
    tampered["model_sha256"] = hashlib.sha256("".join(m["sha256"] for m in tampered["models"]).encode()).hexdigest()
    try:
        wl.load_model(tampered, str(tmp_path / "x.json"))
        raise AssertionError("a model whose bytes differ must be refused")
    except SystemExit as exc:
        assert "refusing to predict" in str(exc)


def test_each_variant_runs_on_its_own_and_a_missing_model_is_skipped(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(wl, "VARIANTS", {"wxrd": (wl.MODEL_JSON, "P7.6_wxrd_shadow"),
                                         "wxrd3": (str(tmp_path / "absent.json"), "P7.6_wxrd3_shadow")})
    deadlines = []
    monkeypatch.setattr(wl, "run", lambda now, dry, variant, deadline: ran.append(variant) or
                        deadlines.append(deadline) or {"variant": variant})
    assert wl.main([]) == 0 and ran == ["wxrd"]

    def boom(now, dry, variant, deadline):
        ran.append(variant)
        deadlines.append(deadline)
        if variant == "wxrd":
            raise SystemExit("wxrd-v2's sha256 is ..., not ...: refusing to predict")
        raise RuntimeError("down")
    monkeypatch.setattr(wl, "VARIANTS", {"wxrd": (wl.MODEL_JSON, "a"), "wxrd3": (wl.MODEL_JSON, "b")})
    monkeypatch.setattr(wl, "run", boom)
    ran.clear()
    deadlines.clear()
    assert wl.main([]) == 1 and ran == ["wxrd", "wxrd3"], \
        "a refused model (SystemExit) or an error in one does not stop the next (Codex on #398); the step fails"
    assert deadlines[0] == deadlines[1], "one row budget for every variant together (Codex on #398)"


def test_a_variant_row_carries_its_variant():
    row = wl.variant_row(served(), "C", ["b1", "b2"], [0.4, 0.6], "wxrd3:v3:abc", False, "wxrd3")
    assert row["variant"] == "wxrd3" and row["variant_version"].startswith(row["variant"] + ":")


def test_duplicate_engine_versions_choose_one_first_call():
    early = dict(served(), checkpoint_id="a")
    later = dict(served(decided_at="2026-10-14T11:36:29+00:00"), checkpoint_id="b")
    assert wl.due([later, early, early], set()) == ([early], 0)


@pytest.mark.parametrize("variant", ["wxrd", "wxrd3"])
def test_failed_input_capture_leaves_the_first_call_due_until_saved(monkeypatch, variant):
    """A transient input-save failure cannot permanently mark a call held."""
    import types
    from wxpredict import capture_history, decision_inputs

    first = dict(served(), checkpoint_id=123)
    sibling = dict(first, variant="honest", served_calibrated=False)
    captures, predictions = [], []

    class Ops(_Ops):
        def rpc(self, name, params):
            assert name == "live_capture_put"
            captures.append(params)
            if len(captures) == 1:
                raise RuntimeError("temporary capture outage")
            return {"rows": len(params["p_rows"]), "new": len(params["p_rows"])}

        def upsert(self, table, rows, on_conflict):
            super().upsert(table, rows, on_conflict)
            self.tables[table].extend(rows)
            return len(rows)

    fake = Ops({"prediction_checkpoints": [first], wl.TABLE: [sibling]})
    event = {"city": "madrid", "date": first["target_date"],
             "unit": "C", "band_ids": ["b1", "b2"]}
    vector = {"event_id": "contract", "decision_utc": int(decision_inputs.unix(first["decided_at"])),
              "local_hour": 12, "city_key": "madrid", "date": first["target_date"]}
    monkeypatch.setattr(common, "ops", lambda: fake)
    monkeypatch.setattr(common, "cities", lambda: {"madrid": {"icao": None, "timezone":"UTC"}})
    monkeypatch.setattr(capture_history, "read", lambda *a, **k: [])
    monkeypatch.setattr(wl.lr, "live_events", lambda *a: [event])
    monkeypatch.setattr(wl.lr, "row_at", lambda *a, **k: dict(vector))
    monkeypatch.setattr(build_table, "load_station_daily", lambda: ({}, None))
    monkeypatch.setattr(build_table, "load_daily_forecasts", lambda: ({}, {}))
    monkeypatch.setattr(decision_inputs, "history_identity", lambda: ("a" * 40, 0))
    monkeypatch.setattr(wl, "load_model", lambda *a: predictions.append("loaded") or object())
    monkeypatch.setitem(sys.modules, "sdyes_forward", types.SimpleNamespace(
        model_probs=lambda rows, model: {("contract", vector["decision_utc"]): [.4, .6]}))

    failed = wl.run(now=dt.datetime(2026, 10, 14, 10, 37, tzinfo=dt.timezone.utc), variant=variant)
    assert failed["written"] == 0 and "temporary capture outage" in failed["decision_input_capture_error"]
    assert fake.upserts == [] and predictions == [] and fake.logs[-1][1] == "partial"

    retried = wl.run(now=dt.datetime(2026, 10, 14, 10, 38, tzinfo=dt.timezone.utc), variant=variant)
    assert retried["due"] == retried["written"] == 1
    assert captures[0]["p_rows"] == captures[1]["p_rows"]
    assert captures[1]["p_rows"][0]["payload"]["checkpoint_id"] == first["checkpoint_id"]
    assert len(predictions) == len(fake.upserts) == 1 and fake.logs[-1][1] == "ok"

    held = wl.run(now=dt.datetime(2026, 10, 14, 10, 39, tzinfo=dt.timezone.utc), variant=variant)
    assert held["due"] == held["written"] == 0 and len(captures) == 2
