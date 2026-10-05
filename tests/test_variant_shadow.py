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
            "engine_version": "git:abc", "running_max_c": floor, "centre_c": 21.8, "sigma_c": 0.9,
            "station": "EGLL"}


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

    w["corrected"] = {}             # (city, date) -> derived_corrected_forecast row; none by default
    w["settings"] = []              # settings rows for station_correction_pricing
    w["sd_asked"] = []

    def get(url, headers=None, params=None, timeout=None):
        assert headers["apikey"] == "k" and 1.0 <= timeout <= 8.0
        table = url.rsplit("/", 1)[1]
        if table in ("derived_corrected_forecast", "settings"):
            w["sd_asked"].append(dict(params, _table=table, _timeout=timeout))
            data = w["corrected"] if table == "derived_corrected_forecast" else w["settings"]
            if isinstance(data, Exception):
                return Answer(data)
            if table == "settings":
                return Answer(data)
            cities = params["city_key"][len("in.("):-1].split(",")
            dates = params["for_date"][len("in.("):-1].split(",")
            return Answer([r for (c, d), r in data.items() if c in cities and d in dates])
        assert url == "https://x.supabase.co/rest/v1/band_probabilities"
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
    assert row["station"] == "EGLL", "the served call's station, recorded beside it"
    assert row["probs"] == vs.ladder(21.2, 1.4, "C", BANDS, 20.4, 0.03, 0.06)
    # the floor (20.4 C, in b2) rules out b1, except the measurement layer's
    # q_down share of the floor bucket's mass (P3.1)
    assert vs.ladder(21.2, 1.4, "C", BANDS, 20.4, 0, 0)["b1"] == pe.clamp_prob(0.0)
    atom = pe.normal_cdf(pe.unit_edge_c("C", 21), 21.2, 1.4)   # the settlement split, 20.5 C
    assert row["probs"]["b1"] == round(0.03 * atom, 6)
    assert abs(sum(row["probs"].values()) - 1) < 1e-4 and row["top_band_id"] in row["probs"]
    assert counts == {"version": "da_floor:v1", "due": 1, "written": 1, "skipped": {},
                      "seconds": counts["seconds"],
                      "sd_corr": {"version": "sd_corr:v1", "due": 1, "written": 0,
                                  "skipped": {"no_live_corrected_row": 1}}}
    assert all(row[k] is None for k in vs.CORRECTED_INPUTS), "a da_floor row names no corrected inputs"
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
    assert counts["sd_corr"]["skipped"] == {"no_live_corrected_row": 1, "no_market_or_timezone": 1}


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
    # sd_corr's own losses count too; its registered exclusions do not
    sd_excluded = {"no_live_corrected_row": 3, "corrected_row_older_than_max_age": 1, "live_row_without_width": 1}
    assert vs.lost({"skipped": {}, "sd_corr": {"skipped": sd_excluded}}) is None
    assert vs.lost({"skipped": {}, "sd_corr": {"error": "RuntimeError: 401"}}) == "sd_corr:v1: RuntimeError: 401"
    assert vs.lost({"skipped": {}, "sd_corr": {"skipped": {"out_of_time": 2}}}) == \
        "rows not captured: {'sd_corr:out_of_time': 2}"


# --------------------------------------------------------------------------
# sd_corr:v1, the engine's station-corrected path on the day itself
# (docs/SD_CORR_PREREG.md)
# --------------------------------------------------------------------------
SD_PREREG = ROOT / "docs" / "SD_CORR_PREREG.md"


def _corrected(city="london", hours_ago=3.0, centre=21.6, width=0.9, lead=0):
    at = (dt.datetime.now(UTC) - dt.timedelta(hours=hours_ago)).isoformat()
    return {"city_key": city, "for_date": "2026-10-05", "lead_days": lead, "combined_c": centre,
            "width_c": width, "version": "station-correction:2026-10-05:aaa0000001",
            "width_version": "station-width:2026-10-05:bbb0000001", "computed_at": at, "n_sources": 7}


def test_sd_corr_one_row_beside_da_floor_with_the_live_corrected_inputs(io):
    io["corrected"][("london", "2026-10-05")] = live = _corrected()
    counts = _record(io, [_served("london", "noon")])
    (_t, da_rows, _c), (table, sd_rows, conflict) = io["written"]
    assert ([r["variant"] for r in da_rows], [r["variant"] for r in sd_rows]) == (["da_floor"], ["sd_corr"]), \
        "one insert per variant, da_floor's first"
    assert (table, conflict) == (vs.TABLE, vs.ON_CONFLICT)
    by = {"da_floor": da_rows[0], "sd_corr": sd_rows[0]}
    assert set(by["da_floor"]) == set(by["sd_corr"]), "each names the other's inputs, as null"
    assert all(by["da_floor"][k] is None for k in vs.CORRECTED_INPUTS)
    sd = by["sd_corr"]
    assert sd["variant_version"] == "sd_corr:v1" and sd["engine_version"] == "git:abc" and sd["station"] == "EGLL"
    assert (sd["corrected_centre_c"], sd["corrected_width_c"], sd["floor_c"]) == (21.6, 0.9, 20.4)
    assert (sd["corrected_version"], sd["corrected_width_version"], sd["corrected_computed_at"],
            sd["corrected_lead_days"], sd["corrected_n_sources"]) == (
        live["version"], live["width_version"], live["computed_at"], 0, 7)
    assert all(sd[k] is None for k in vs.DAY_AHEAD_INPUTS)
    assert (sd["q_down"], sd["q_up"]) == (0.03, 0.06), "the same q as the served call"
    assert sd["probs"] == vs.ladder(21.6, 0.9, "C", BANDS, 20.4, 0.03, 0.06)
    assert counts["sd_corr"] == {"version": "sd_corr:v1", "due": 1, "written": 1, "skipped": {}}
    # two reads: the corrected rows and their max age, each one bounded attempt
    assert [a["_table"] for a in io["sd_asked"]] == ["derived_corrected_forecast", "settings"]
    first = io["sd_asked"][0]
    assert (first["select"], first["city_key"], first["for_date"]) == (
        vs.CORRECTED_SELECT, "in.(london)", "in.(2026-10-05)")
    assert io["sd_asked"][1]["key"] == "eq.station_correction_pricing"


def test_sd_corr_reads_the_row_at_the_engine_s_max_age_whatever_the_switch_says(io):
    io["settings"] = [{"value": {"enabled": False, "max_age_hours": 6}}]
    io["corrected"][("london", "2026-10-05")] = _corrected(hours_ago=7)
    counts = _record(io, [_served("london", "noon")])
    assert counts["sd_corr"]["skipped"] == {"corrected_row_older_than_max_age": 1}
    io["settings"] = [{"value": {"enabled": False, "max_age_hours": 8}}]
    counts = _record(io, [_served("london", "morning")])
    assert counts["sd_corr"]["written"] == 1, "the pricing switch off does not stop the test"
    # absent: the engine's default, 36 h
    io["settings"] = []
    io["corrected"][("london", "2026-10-05")] = _corrected(hours_ago=35.9)
    assert _record(io, [_served("london", "prepeak_2h")])["sd_corr"]["written"] == 1
    io["corrected"][("london", "2026-10-05")] = _corrected(hours_ago=36.1)
    assert _record(io, [_served("london", "prepeak_1h")])["sd_corr"]["skipped"] == {
        "corrected_row_older_than_max_age": 1}


def test_sd_corr_skips_are_counted_by_reason(io):
    io["corrected"][("london", "2026-10-05")] = dict(_corrected(), width_c=None, width_version=None)
    io["corrected"][("paris", "2026-10-05")] = _corrected(city="paris", hours_ago=-1)   # computed after now
    counts = _record(io, [_served("london", "noon"), _served("paris", "noon"),
                          dict(_served("london", "noon"), city_key="rome")])
    assert counts["sd_corr"]["skipped"] == {"live_row_without_width": 1, "no_live_corrected_row": 1,
                                            "no_market_or_timezone": 1}
    assert counts["sd_corr"]["due"] == 3 and vs.lost(counts) is None, "registered exclusions, not losses"


def test_the_live_row_rule():
    now = dt.datetime(2026, 10, 5, 12, tzinfo=UTC)
    row = {"combined_c": 20.0, "width_c": 1.0, "width_version": "w", "computed_at": "2026-10-04T05:00:00+00:00"}
    assert vs.corrected_live(row, now, 36) == (row, None)                       # 31 h old
    assert vs.corrected_live(row, now, 30) == (None, "corrected_row_older_than_max_age")
    assert vs.corrected_live(None, now) == (None, "no_live_corrected_row")
    assert vs.corrected_live(dict(row, computed_at="2026-10-05T12:00:01+00:00"), now) == (
        None, "no_live_corrected_row")
    assert vs.corrected_live(dict(row, width_c=0), now) == (None, "live_row_without_width")
    assert vs.corrected_live(dict(row, width_version=None), now) == (None, "live_row_without_width")


def test_a_failed_corrected_read_loses_sd_corr_s_rows_and_never_da_floor_s(io):
    io["corrected"] = RuntimeError("401 PGRST303")
    counts = _record(io, [_served("london", "noon")])
    (table, rows, conflict), = io["written"]
    assert [r["variant"] for r in rows] == ["da_floor"]
    assert counts["sd_corr"]["error"] == "RuntimeError: 401 PGRST303"
    assert vs.lost(counts) == "sd_corr:v1: RuntimeError: 401 PGRST303", "the tick turns to attention"
    # the max age is read, never guessed: a failed settings read loses the rows too
    io["corrected"] = {("london", "2026-10-05"): _corrected()}
    io["settings"] = RuntimeError("read timed out")
    counts = _record(io, [_served("london", "morning")])
    assert counts["sd_corr"]["error"] == "RuntimeError: read timed out"


def test_a_refused_write_of_one_variant_never_costs_the_other_its_rows(io, monkeypatch):
    """da_floor's test was running before sd_corr's: an sd_corr row the table
    refuses must not take da_floor's rows with it (nor the other way)."""
    io["corrected"][("london", "2026-10-05")] = _corrected()
    common = importlib.import_module("common")
    inner = common._post

    def refusing(variant):
        def post(url, headers=None, params=None, data=None, timeout=None):
            if any(r["variant"] == variant for r in json.loads(data)):
                raise RuntimeError("new row violates check constraint")
            return inner(url, headers=headers, params=params, data=data, timeout=timeout)
        return post

    monkeypatch.setattr(common, "_post", refusing("sd_corr"))
    counts = _record(io, [_served("london", "noon")])
    assert [[r["variant"] for r in rows] for _t, rows, _c in io["written"]] == [["da_floor"]]
    assert counts["written"] == 1 and "error" not in counts
    assert counts["sd_corr"]["written"] == 0
    assert counts["sd_corr"]["error"] == "RuntimeError: new row violates check constraint"
    assert vs.lost(counts) == "sd_corr:v1: RuntimeError: new row violates check constraint"

    io["written"].clear()
    monkeypatch.setattr(common, "_post", refusing("da_floor"))
    counts = _record(io, [_served("london", "morning")])
    assert [[r["variant"] for r in rows] for _t, rows, _c in io["written"]] == [["sd_corr"]]
    assert counts["error"] == "RuntimeError: new row violates check constraint"
    assert counts["written"] == 0 and counts["sd_corr"]["written"] == 1


def test_the_sd_corr_write_is_not_started_with_under_a_second_left(io, monkeypatch):
    io["corrected"][("london", "2026-10-05")] = _corrected()
    inner = vs.write

    def slow(rows, timeout_s):
        n = inner(rows, timeout_s)
        time.sleep(1.7)
        return n
    monkeypatch.setattr(vs, "write", slow)
    counts = _record(io, [_served("london", "noon")], deadline=time.monotonic() + 2.5)
    assert [[r["variant"] for r in rows] for _t, rows, _c in io["written"]] == [["da_floor"]]
    assert counts["written"] == 1 and counts["sd_corr"]["skipped"] == {"out_of_time": 1}
    assert vs.lost(counts) == "rows not captured: {'sd_corr:out_of_time': 1}"


def test_sd_corr_reads_stop_short_of_the_deadline(io, monkeypatch):
    def lookup(*a, **k):
        time.sleep(0.3)
        return io["calls"]["b1"]
    monkeypatch.setattr(vs, "day_ahead_call", lookup)
    io["corrected"][("london", "2026-10-05")] = _corrected()
    counts = _record(io, [_served("london", "noon")], deadline=time.monotonic() + 2.1)
    assert io["sd_asked"] == [] and counts["sd_corr"]["skipped"] == {"out_of_time": 1}
    assert vs.lost(counts) == "rows not captured: {'sd_corr:out_of_time': 1}"
    _record(io, [_served("london", "morning")], deadline=time.monotonic() + 4.0)
    assert io["sd_asked"] and all(1.0 <= a["_timeout"] <= 3.0 for a in io["sd_asked"])


def test_a_dry_run_writes_neither_variant(io):
    io["corrected"][("london", "2026-10-05")] = _corrected()
    counts = _record(io, [_served("london", "noon")], dry_run=True)
    assert io["written"] == [] and counts["would_write"] == 1 and counts["sd_corr"]["would_write"] == 1


def test_the_live_sd_corr_ladder_is_the_replay_s_on_every_recorded_row():
    """On P1.1's rows, the row the capture would have read at each decision
    (the newest computation before it, as the table held it) gives the same
    verdict as tools/sd_corrected_replay.live_row, and the same ladder as its
    sd_corr to the served rounding."""
    import sd_corrected_replay as sdr
    frozen = sdr.corrected_rows(sdr.load_frozen(ROOT / "data" / "eval" / "sd_corr" / "corrected_rows.json.gz"))
    same = {"no_row_computed_before": "no_live_corrected_row",
            "row_older_than_max_age": "corrected_row_older_than_max_age"}
    compared = excluded = 0
    for r in _p11_rows():
        if r.get("da_centre") is None or r.get("da_sigma") is None:
            continue
        at = sdr._ts(r["decided_at"])
        held = [x for x in frozen.get((r["city"], r["date"]), []) if x["computed_at"] <= at]
        table_row = None
        if held:
            b = max(held, key=lambda x: x["computed_at"])
            table_row = {"combined_c": b["centre"], "width_c": b["width"], "width_version": b["width_version"],
                         "version": b["version"], "computed_at": b["computed_at"].isoformat(),
                         "lead_days": b["lead"], "n_sources": b["n_sources"]}
        got, why = vs.corrected_live(table_row, at, vs.SD_MAX_AGE_H)
        live, rwhy = sdr.live_row(frozen.get((r["city"], r["date"]), []), r["decided_at"])
        if live is None:
            assert got is None and why == same[rwhy], (r["city"], r["date"], r["cp"])
            excluded += 1
            continue
        if live["width"] is None:
            assert why == "live_row_without_width"
            excluded += 1
            continue
        research = sdr.candidate_variants(r, live)["sd_corr"]
        q = p11.q_at(r)
        if q is None:
            q = ((float(r["q_down"]), float(r["q_up"])) if r.get("q_down") is not None
                 and r.get("q_up") is not None else (pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP))
        lad = vs.ladder(got["combined_c"], got["width_c"], r["unit"], r["bands"], r["floor"], *q)
        assert set(lad) == set(research)
        assert max(abs(lad[b] - research[b]) for b in lad) <= 5e-7, (r["city"], r["date"], r["cp"])
        compared += 1
    assert compared == 1343 and excluded == 301, (compared, excluded)


def test_the_sd_corr_pre_registration_names_what_the_code_does():
    doc = SD_PREREG.read_text()
    head = doc[:doc.index("## Result")]
    assert "**Written 5 Oct 2026, before any forward row of this candidate existed.**" in head
    assert "`sd_corr:v1`" in head and vs.SD_VERSION == "sd_corr:v1" and vs.SD_VARIANT == "sd_corr"
    assert "`settings.station_correction_pricing.max_age_hours` (36 when absent)" in head
    assert vs.SD_MAX_AGE_H == 36.0
    assert "`combined_c`" in head and "`width_c`" in head
    assert "whatever the pricing switches say" in head
    assert "`tools/sd_corrected_replay.py`" in head


def test_the_table_holds_each_variant_to_its_own_inputs():
    """tests/database/sd-corr-shadow.cjs runs it; these pin the clauses a review
    would look for."""
    sql = (ROOT / "supabase" / "migrations" / "20261005090000_the_same_day_corrected_candidate.sql").read_text()
    assert "a check\n-- that evaluates to null passes" in sql
    assert "coalesce(case variant" in sql and "else true end, false));" in sql
    assert "and corrected_computed_at is not null and corrected_computed_at <= decided_at" in sql
    for col in vs.CORRECTED_INPUTS:
        assert f"add column if not exists {col} " in sql, col
    for col in ("day_ahead_centre_c", "day_ahead_sigma_c", "day_ahead_priced_at"):
        assert f"alter column {col} drop not null;" in sql
    # the contract: the variant's own centre and width, still the owner's view
    assert "coalesce(v.day_ahead_centre_c, v.corrected_centre_c)," in sql
    assert "coalesce(v.day_ahead_sigma_c, v.corrected_width_c)," in sql
    assert "create or replace view public.v_prediction_contract with (security_invoker = false) as" in sql
    assert "'engine_variant', 'sd_corr:v1', 'same day', 'shadow', 'docs/SD_CORR_PREREG.md'" in sql
    db = json.loads((ROOT / "tests" / "database" / "package.json").read_text())["scripts"]["test"]
    assert "node sd-corr-shadow.cjs" in db
