"""P1.1's candidate in shadow: the day-ahead ladder with the floor (da_floor:v1).

docs/P11_DA_FLOOR_PREREG.md fixes the forward test before any row exists;
scripts/variant_shadow.py records the candidate beside each same-day call the
tick writes. These tests hold the live capture to the research definition
(tools/p11_intraday_ablation.py) on the recorded P1.1 rows, and hold the
capture to the pre-registration: same-day checkpoints only, the last pricing
before the city's own midnight, the engine's q, first captures, every skip
counted, never raising, never past its deadline.
"""
import datetime as dt
import glob
import importlib
import gzip
import json
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

import probability_engine as pe  # noqa: E402
import variant_shadow as vs      # noqa: E402
import p11_intraday_ablation as p11  # noqa: E402

UTC = dt.timezone.utc
PREREG = ROOT / "docs" / "P11_DA_FLOOR_PREREG.md"
MIGRATION = ROOT / "supabase" / "migrations" / "20261004180000_engine_variants_in_shadow.sql"


# --------------------------------------------------------------------------
# the capture is the research definition
# --------------------------------------------------------------------------
def _p11_rows():
    rows = []
    for p in sorted(glob.glob(str(ROOT / "data" / "eval" / "p11" / "rows_*.json.gz"))):
        with gzip.open(p, "rt") as f:
            rows += json.load(f)
    return rows


def test_the_live_ladder_is_p11s_da_floor_on_every_recorded_row():
    """On the 27 Sep - 3 Oct rows P1.1 scored, the capture's ladder equals the
    research tool's da_floor to the served rounding (6 decimals), with the q
    the engine read at each decision."""
    compared = 0
    for r in _p11_rows():
        if r.get("da_centre") is None or r.get("da_sigma") is None:
            continue
        research = p11.variants(r)["da_floor"]
        q = p11.q_at(r)
        if q is None:
            q = ((float(r["q_down"]), float(r["q_up"])) if r.get("q_down") is not None
                 and r.get("q_up") is not None else (pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP))
        live = vs.ladder(r["da_centre"], r["da_sigma"], r["unit"], r["bands"], r["floor"], *q)
        assert set(live) == set(research)
        assert max(abs(live[b] - research[b]) for b in live) <= 5e-7, (r["city"], r["date"], r["cp"])
        compared += 1
    assert compared >= 1500, compared


def test_the_midnight_is_the_city_s_own():
    assert vs.day_starts_at("2026-10-04", "America/Chicago") == dt.datetime(2026, 10, 4, 5, tzinfo=UTC)
    assert vs.day_starts_at("2026-10-04", "Asia/Shanghai") == dt.datetime(2026, 10, 3, 16, tzinfo=UTC)
    # New Zealand is on daylight time from 27 Sep
    assert vs.day_starts_at("2026-10-04", "Pacific/Auckland") == dt.datetime(2026, 10, 3, 11, tzinfo=UTC)
    assert vs.day_starts_at("2026-10-04", "Asia/Kolkata") == dt.datetime(2026, 10, 3, 18, 30, tzinfo=UTC)


# --------------------------------------------------------------------------
# record(): what the tick calls
# --------------------------------------------------------------------------
BANDS = [
    {"band_id": "b1", "band_lo": None, "band_hi": 20, "open_low": True, "open_high": False},
    {"band_id": "b2", "band_lo": 20, "band_hi": 21, "open_low": False, "open_high": False},
    {"band_id": "b3", "band_lo": 21, "band_hi": 22, "open_low": False, "open_high": False},
    {"band_id": "b4", "band_lo": 22, "band_hi": None, "open_low": False, "open_high": True},
]
MARKETS = {("london", "2026-10-05"): {"market_id": "m1", "unit": "C"},
           ("paris", "2026-10-05"): {"market_id": "m2", "unit": "C"}}
TZ = {"london": "Europe/London", "paris": "Europe/Paris"}


def _served(city, checkpoint, floor=20.4):
    return {"city_key": city, "target_date": "2026-10-05", "checkpoint": checkpoint,
            "engine_version": "git:abc", "running_max_c": floor, "centre_c": 21.8, "sigma_c": 0.9}


@pytest.fixture
def io(monkeypatch):
    w = {"asked": [], "written": [], "calls": {
        "b1": {"band_id": "b1", "centre_c": 21.2, "sigma_c": 1.4,
               "computed_at": "2026-10-04T20:40:00+00:00", "lead_days": 1}}}

    class Answer:
        def __init__(self, rows):
            self.rows = rows

        def raise_for_status(self):
            if isinstance(self.rows, Exception):
                raise self.rows

        def json(self):
            return self.rows

    def get(url, headers=None, params=None, timeout=None):
        assert url == "https://x.supabase.co/rest/v1/band_probabilities"
        assert headers["apikey"] == "k" and 1.0 <= timeout <= 8.0
        w["asked"].append(dict(params, _timeout=timeout))
        ids = params["band_id"][len("in.("):-1].split(",")
        if isinstance(w["calls"], Exception):
            return Answer(w["calls"])
        return Answer([w["calls"][ids[0]]] if ids[0] in w["calls"] else [])

    def post(url, headers=None, params=None, data=None, timeout=None):
        assert url.startswith("https://x.supabase.co/rest/v1/")
        assert headers["Prefer"] == "resolution=ignore-duplicates,return=minimal"
        assert 1.0 <= timeout <= 8.0, "one attempt, never longer than the time left"
        if isinstance(w.get("post"), Exception):
            return Answer(w["post"])
        w["written"].append((url.rsplit("/", 1)[1], json.loads(data), params["on_conflict"]))
        return Answer(None)
    # the `common` variant_shadow will import now: other tests reload it
    live = importlib.import_module("common")
    monkeypatch.setattr(live, "_get", get)
    monkeypatch.setattr(live, "_cfg", lambda: {"url": "https://x.supabase.co", "key": "k"})
    monkeypatch.setattr(live, "_post", post)
    monkeypatch.setattr(pe, "_measurement_layer_for",
                        lambda city: (0.03, 0.06) if city == "london" else None)
    return w


def _record(io, out, results=None, **kw):
    bands = {"m1": BANDS, "m2": [dict(b, band_id="p" + b["band_id"][1:]) for b in BANDS]}
    return vs.record(out, results or {}, MARKETS, bands, TZ, {"london": "C", "paris": "C"}, **kw)


def test_one_row_per_same_day_call_with_the_inputs_it_was_built_from(io):
    out = [_served("london", "noon"), _served("london", "d1_eve")]
    results = {("london", "2026-10-05"): ([], None, ["priced_from:x", "measurement_layer:q_down0.0300_q_up0.0600:city"])}
    counts = _record(io, out, results)
    (table, rows, conflict), = io["written"]
    assert (table, conflict) == ("variant_shadow_checkpoints", "city_key,target_date,checkpoint,variant")
    (row,) = rows
    assert (row["city_key"], row["checkpoint"], row["variant"], row["variant_version"]) == (
        "london", "noon", "da_floor", "da_floor:v1")
    assert (row["day_ahead_centre_c"], row["day_ahead_sigma_c"], row["floor_c"]) == (21.2, 1.4, 20.4)
    assert (row["q_down"], row["q_up"]) == (0.03, 0.06), "the q the engine read for the city"
    assert row["served_calibrated"] is False and row["engine_version"] == "git:abc"
    assert row["probs"] == vs.ladder(21.2, 1.4, "C", BANDS, 20.4, 0.03, 0.06)
    # the floor (20.4 C, in b2) rules out b1, except the measurement layer's
    # q_down share of the floor bucket's mass (P3.1)
    assert vs.ladder(21.2, 1.4, "C", BANDS, 20.4, 0, 0)["b1"] == pe.clamp_prob(0.0)
    atom = pe.normal_cdf(pe.unit_edge_c("C", 21), 21.2, 1.4)   # the settlement split, 20.5 C
    assert row["probs"]["b1"] == round(0.03 * atom, 6)
    assert abs(sum(row["probs"].values()) - 1) < 1e-4 and row["top_band_id"] in row["probs"]
    assert counts == {"version": "da_floor:v1", "due": 1, "written": 1, "skipped": {},
                      "seconds": counts["seconds"]}
    # the last pricing before London's own midnight (BST: 23:00Z the day before)
    (asked,) = io["asked"]
    assert asked["computed_at"] == "lt.2026-10-04T23:00:00+00:00"
    assert asked["order"] == "computed_at.desc,prob_id.desc" and asked["limit"] == "1"
    assert asked["band_id"] == "in.(b1,b2,b3,b4)"
    assert asked["_timeout"] == 8.0, "one attempt, never longer than the time left"


def test_the_pooled_q_where_the_city_has_none_and_no_q_without_a_floor(io):
    io["calls"]["p1"] = dict(io["calls"]["b1"], band_id="p1")
    _record(io, [_served("paris", "morning"), _served("london", "morning", floor=None)])
    rows = {r["city_key"]: r for r in io["written"][0][1]}
    assert (rows["paris"]["q_down"], rows["paris"]["q_up"]) == (pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP)
    assert rows["london"]["floor_c"] is None and rows["london"]["q_down"] is None
    assert rows["london"]["probs"] == vs.ladder(21.2, 1.4, "C", BANDS, None, 0, 0)


def test_a_calibrated_served_ladder_is_marked(io):
    results = {("london", "2026-10-05"): ([], None, ["calibrated:temperature(T=1.100,ladders=40)"])}
    _record(io, [_served("london", "noon")], results)
    assert io["written"][0][1][0]["served_calibrated"] is True


def test_every_skip_is_counted_by_reason(io):
    out = [_served("paris", "noon"),                       # no pricing before midnight
           dict(_served("london", "noon"), city_key="rome")]   # no market, no timezone
    counts = _record(io, out)
    assert io["written"] == []
    assert counts["skipped"] == {"no_day_ahead_call": 1, "no_market_or_timezone": 1}
    assert counts["due"] == 2 and counts["written"] == 0


def test_the_lookup_timeout_is_cut_to_the_time_left(io):
    _record(io, [_served("london", "noon")], deadline=time.monotonic() + 3.0)
    assert 1.0 <= io["asked"][0]["_timeout"] <= 3.0


def test_a_refused_read_is_counted_and_never_raised(io):
    io["calls"] = RuntimeError("401 PGRST303")
    counts = _record(io, [_served("london", "noon")])
    assert counts["skipped"] == {"error": 1} and io["written"] == []


def test_a_refused_write_is_reported_and_never_raised(io):
    io["post"] = RuntimeError("the insert was refused")
    counts = _record(io, [_served("london", "noon")])
    assert counts["error"].startswith("RuntimeError: the insert was refused")


def test_no_write_starts_with_under_a_second_left(io, monkeypatch):
    """Codex on #303: common.upsert waits up to 120 s and retries four times,
    which could run past the deadline. The write is one bounded request, and
    with under a second left it is not started; the rows are counted."""
    def lookup(*a, **k):
        time.sleep(0.3)
        return io["calls"]["b1"]
    monkeypatch.setattr(vs, "day_ahead_call", lookup)
    counts = _record(io, [_served("london", "noon")], deadline=time.monotonic() + 1.2)
    assert io["written"] == [] and counts["skipped"] == {"out_of_time": 1} and counts["written"] == 0


def test_the_tick_records_the_candidate_after_the_engine_decides():
    """The engine's decisions act on this tick; the candidate only observes."""
    src = (ROOT / "scripts" / "tick.py").read_text()
    assert src.index("engine_shadow.record(") < src.index("variant_shadow.record(")
    assert "deadline=t0 + budget_s - 2.0" in src[src.index("variant_shadow.record("):]


def test_no_lookup_starts_past_the_deadline(io):
    counts = _record(io, [_served("london", "noon")], deadline=time.monotonic() - 1)
    assert io["asked"] == [] and io["written"] == []
    assert counts["skipped"] == {"out_of_time": 1}


def test_a_lookup_that_overruns_the_deadline_is_left_behind(io, monkeypatch):
    def slow(*a, **k):
        time.sleep(1.0)
        return io["calls"]["b1"]
    monkeypatch.setattr(vs, "day_ahead_call", slow)
    t0 = time.monotonic()
    counts = _record(io, [_served("london", "noon")], deadline=time.monotonic() + 0.2)
    assert time.monotonic() - t0 < 0.9
    assert counts["skipped"] == {"out_of_time": 1} and io["written"] == []


def test_a_dry_run_writes_nothing(io):
    counts = _record(io, [_served("london", "noon")], dry_run=True)
    assert io["written"] == [] and counts["would_write"] == 1


def test_build_row_refuses_a_call_without_a_width():
    row, why = vs.build_row(_served("london", "noon"), {"centre_c": 21.0, "sigma_c": 0,
                            "computed_at": "2026-10-04T20:40:00+00:00"}, BANDS, "C", (0.02, 0.05), False)
    assert row is None and why == "day_ahead_call_without_centre"


# --------------------------------------------------------------------------
# the table and the pre-registration
# --------------------------------------------------------------------------
def test_the_table_is_append_only_and_the_service_role_s():
    sql = MIGRATION.read_text()
    assert "before update or delete on public.variant_shadow_checkpoints" in sql
    assert "before truncate on public.variant_shadow_checkpoints" in sql
    assert "revoke all on public.variant_shadow_checkpoints from public, anon, authenticated, service_role;" in sql
    assert "grant select, insert on public.variant_shadow_checkpoints to service_role;" in sql
    assert "unique (city_key, target_date, checkpoint, variant)" in sql
    assert "('morning', 'noon', 'prepeak_2h', 'prepeak_1h', 'postpeak_1h')" in sql
    assert tuple(vs.CHECKPOINTS) == ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")


def test_every_row_reaches_the_repository():
    import mirror_to_repo
    assert mirror_to_repo.TABLES["variant_shadow_checkpoints"] == {
        "kind": "append", "time": "decided_at", "pk": ["shadow_id"]}


def test_the_pre_registration_names_what_the_code_does():
    doc = PREREG.read_text()
    head = doc[:doc.index("## Result")]
    assert "**Written 4 Oct 2026 at about 12:00Z, before any forward row of this candidate existed.**" in head
    assert "`da_floor:v1`" in head and vs.VERSION == "da_floor:v1"
    assert "seed 11, 1,000" in head and "at least 20 dates" in head
    assert "Hassan" in head, "a verdict proposes; it does not serve"
    assert "`variant_shadow.ladder`" in head


def test_lost_names_failures_and_the_clock_not_the_registered_exclusions():
    assert vs.lost(None) is None and vs.lost({"skipped": {}}) is None
    assert vs.lost({"skipped": {"no_day_ahead_call": 4, "no_bands": 1}}) is None
    assert vs.lost({"error": "RuntimeError: refused"}) == "RuntimeError: refused"
    assert vs.lost({"skipped": {"out_of_time": 2, "no_bands": 1}}) == "rows not captured: {'out_of_time': 2}"
