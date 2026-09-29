"""The day-feature repair (plan v2 P1.6, 29 Sep): tools/repair_day_features.py.

prune_observations cut every city part-way through its local day, and the
next refresh_feature_cache overwrote that day's cached features from the
readings the cut left (fixed going forward by
20260929010000_a_cut_day_keeps_its_cached_values.sql). The tool recomputes
those days from the repository's raw readings exactly as v_city_day_features
does, decides which cached rows to rewrite, and writes the audit and the
guarded SQL that rewrote them (data/repairs/2026-09-29-day-features).
"""

import datetime as dt
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import repair_day_features as t  # noqa: E402

REPAIR = ROOT / "data" / "repairs" / "2026-09-29-day-features"
UTC = dt.timezone.utc


def test_rounding_is_postgres_half_away_from_zero():
    assert t.pg_round(Decimal("2.345"), 2) == Decimal("2.35")
    assert t.pg_round(Decimal("-2.345"), 2) == Decimal("-2.35")
    assert t.pg_round(Decimal("0.00005"), 4) == Decimal("0.0001")
    assert t.pg_round(None, 2) is None


def _reading(hour, temp, dew=None, **kw):
    at = dt.datetime(2026, 7, 1, hour, tzinfo=UTC)
    return {"local_date": at.date(), "local_hour": hour, "valid_at": at, "temp_c": Decimal(str(temp)),
            "dewpoint_c": None if dew is None else Decimal(str(dew)), "humidity": kw.get("humidity"),
            "wind_speed": kw.get("wind_speed"), "wind_dir_deg": kw.get("wind_dir_deg"),
            "precip": kw.get("precip"), "cloud_cover": kw.get("cloud_cover"),
            "pressure_hpa": kw.get("pressure_hpa")}


def test_a_day_is_computed_as_the_view_computes_it():
    rows = [_reading(h, 10 + h, dew=5) for h in range(24)]
    f, tied = t.features(rows)
    assert (f["max_c"], f["min_c"], f["n_obs"], f["diurnal_range_c"]) == (33, 10, 24, 23)
    # the morning reading is the one nearest 08:00 local with a dewpoint
    assert f["morning_temp_c"] == 18 and f["dewpoint_depression_c"] == 13 and f["morning_to_max_c"] == 15
    assert not tied


def test_a_tied_morning_takes_the_earliest_reading_and_says_so():
    rows = [_reading(7, 20, dew=10), _reading(9, 22, dew=11), _reading(14, 30)]
    f, tied = t.features(rows)
    assert tied and f["morning_temp_c"] == 20


def test_the_wind_vector_needs_three_daytime_readings():
    two = [_reading(h, 20, wind_speed=Decimal(5), wind_dir_deg=Decimal(90)) for h in (10, 11)]
    assert t.features(two)[0]["wind_u_mean"] is None
    three = [_reading(h, 20, wind_speed=Decimal(5), wind_dir_deg=Decimal(90)) for h in (10, 11, 12)]
    f = t.features(three)[0]
    assert f["wind_u_mean"] == Decimal("-1.0000") and f["wind_v_mean"] == Decimal("0.0000")


def _line(city, day, n, mx, prev="~"):
    vals = {c: "~" for c in t.DUMP_COLS}
    vals.update(city_key=city, obs_date=day, n_obs=str(n), max_c=mx, min_c="10", diurnal_range_c="5",
                prev_max_c=prev, computed_at="2026-09-20T00:00:00.000000")
    return "|".join(vals[c] for c in t.DUMP_COLS)


def _recomputed(city, day, n, mx, tied=False):
    r = {f: None for f in t.BASE}
    r.update(city_key=city, obs_date=day, n_obs=n, max_c=mx, min_c="10", diurnal_range_c="5",
             morning_tied=tied)
    return r


def test_only_a_day_cached_from_fewer_readings_is_rewritten():
    lines = [_line("nyc", "2026-07-28", 2, "23.89"),      # the cut left 2 readings
             _line("nyc", "2026-07-29", 24, "27.22"),     # whole, and the same
             _line("dc", "2026-06-23", 24, "30"),         # the repository has fewer
             _line("tokyo", "2026-07-29", 24, "30")]      # same count, a different value
    rec = {"rows": [_recomputed("nyc", "2026-07-28", 24, "26.67"),
                    _recomputed("nyc", "2026-07-29", 24, "27.22"),
                    _recomputed("dc", "2026-06-23", 22, "29"),
                    _recomputed("tokyo", "2026-07-29", 24, "31")]}
    p = t.plan(lines, rec)
    assert [r["city_key"] for r in p["repair"]] == ["nyc"]
    assert p["repair"][0]["after"]["max_c"] == "26.67" and p["repair"][0]["before"]["max_c"] == "23.89"
    assert [r["city_key"] for r in p["equal"]] == ["nyc"]
    assert [r["city_key"] for r in p["cache_has_more"]] == ["dc"]
    assert [r["city_key"] for r in p["same_count_differs"]] == ["tokyo"]


def test_the_dump_and_the_recompute_must_cover_the_same_days():
    try:
        t.plan([_line("nyc", "2026-07-28", 2, "23.89")], {"rows": []})
    except SystemExit as e:
        assert "different city-days" in str(e)
    else:
        raise AssertionError("a plan was made from a dump the recompute does not cover")


def test_each_block_refuses_values_or_rows_that_are_not_the_audits():
    rows = t.plan([_line("nyc", "2026-07-28", 2, "23.89"), _line("nyc", "2026-07-30", 2, "23.33")],
                  {"rows": [_recomputed("nyc", "2026-07-28", 24, "26.67"),
                            _recomputed("nyc", "2026-07-30", 24, "24.44")]})["repair"]
    (sql,) = t.repair_chunks(rows, "audit.jsonl")
    blob = sql[sql.index("$blob$") + 6:sql.rindex("$blob$")]
    assert re.search(r"md5\(v_blob\) <> '([0-9a-f]+)'", sql).group(1) == hashlib.md5(blob.encode()).hexdigest()
    before = "\n".join(r["before_line"] for r in rows)
    assert f"is distinct from '{hashlib.md5(before.encode()).hexdigest()}'" in sql
    assert "if v_n <> 2 then raise" in sql
    assert blob.split("\n")[0].startswith("nyc|2026-07-28|24|26.67|")


def test_the_committed_repair_is_the_plan_of_its_committed_inputs(tmp_path):
    """Re-running the plan on the committed dump and recompute gives the same
    audit rows and the same SQL blocks that were applied."""
    out = tmp_path / "sql"
    audit = tmp_path / "audit.jsonl"
    argv = ["--cache", str(REPAIR / "cache_before_a-k.txt"), str(REPAIR / "cache_before_l-z.txt"),
            "--recomputed", str(REPAIR / "recomputed.json"), "--cuts", str(REPAIR / "cuts.json"),
            "--audit", str(audit), "--sql-dir", str(out), "--repaired-since", "2026-09-29T07:40:00Z"]
    assert t.main_plan(argv) == 0
    committed = [json.loads(l) for l in (REPAIR / "audit.jsonl").read_text().splitlines()]
    again = [json.loads(l) for l in audit.read_text().splitlines()]
    assert committed[0]["counts"] == {"repair": 561, "equal": 437, "equal_but_morning_tie": 0,
                                      "cache_has_more": 3, "same_count_differs": 62}
    assert committed[1:len(again)] == again[1:]
    # the audit's own path differs in the header comment only
    for f in sorted(out.iterdir()):
        mine = f.read_text().replace(str(audit), "data/repairs/2026-09-29-day-features/audit.jsonl")
        assert mine == (REPAIR / "sql" / f.name).read_text(), f.name


def test_the_repair_only_ever_raised_a_count_or_a_maximum():
    rows = [json.loads(l) for l in (REPAIR / "audit.jsonl").read_text().splitlines()[1:]]
    repaired = [r for r in rows if r["kind"] == "repair"]
    assert len(repaired) == 561
    assert all(int(r["after"]["n_obs"]) > int(r["before"]["n_obs"]) for r in repaired)
    assert all(Decimal(r["after"]["max_c"]) >= Decimal(r["before"]["max_c"]) for r in repaired)
    assert sum(1 for r in rows if r["kind"] == "day_over_day_left_null") == 14
