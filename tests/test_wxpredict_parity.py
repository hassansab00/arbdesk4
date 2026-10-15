"""WXPredict WP3b step 3, the parity comparison (tools/wxpredict/parity.py): a
live row against the table's for the same decision, column by column, each
difference either a named source difference or unexplained; the market judged
by step 2.1's tolerance on narrow books and reported apart on wide or one-sided
ones; arrival against rule reported on its own."""
import csv
import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import features  # noqa: E402
from wxpredict import parity  # noqa: E402

DAY = "2026-10-14"


def table_row(event="e1", t=1792000860, city="madrid", hour="12", offset="0"):
    r = {c: "1.5" for c in features.COLUMNS}
    r.update(event_id=event, decision_utc=str(t), city_key=city, date=DAY, local_hour=hour, day_offset=offset,
             source="venue", station="LEMD", unit="C", bands_lo=";22;23", bands_hi="22;23;", n_bands="3",
             decision_local="2026-10-14T12:00:00", fc_daily_source="lead1", mkt_prices="0.2;0.5;0.3",
             mkt_ages_min="10.0;20.0;30.0", precip_3h_in="0.0", winner="1")
    return r


def live_row(tr, mode="rule", spreads="0.01;0.02;0.01", sides="ba;ba;ba", **change):
    r = dict(tr, live_mode=mode, mkt_spreads=spreads, mkt_sides=sides, mkt_book_age_min="20", books_seen="3")
    r["decision_utc"] = tr["decision_utc"] + ".0"          # written another way, the same instant
    r["winner"] = ""                                       # the live row is made before the outcome
    r.update(change)
    return r


def test_the_same_row_is_parity():
    t = table_row()
    out = parity.run([t], [live_row(t), live_row(t, mode="arrival")], DAY, DAY)
    assert out["rows"]["matched"] == 1 and out["unexplained"] == 0
    assert out["verdict"].startswith("parity holds")
    assert "winner" not in out["rule_vs_table"], "labels are not compared"


def test_live_uuids_are_mapped_only_by_verified_venue_identity():
    t = table_row("123")
    m = {"market_id": "uuid", "event_slug": "slug", "city_key": t["city_key"], "resolution_date": DAY}
    e = {"event_id": "123", "slug": "slug", "city_key": t["city_key"], "date": DAY}
    ids = parity.event_crosswalk([m], [e])
    out = parity.run([t], [live_row(t, event_id="uuid")], DAY, DAY, event_ids=ids)
    assert out["rows"]["matched"] == 1 and out["unexplained"] == 0
    assert out["event_id_crosswalk"] == {"uuid": "123"}
    # City/date alone cannot identify a twin listing.
    assert parity.event_crosswalk([dict(m, event_slug=None)], [e]) == {}
    ids = parity.event_crosswalk([dict(m, event_slug=None)], [e],
                                [{"market_id": "uuid", "token_yes": "yes"}],
                                [{"event_id": "123", "token_yes": "yes"}])
    assert ids == {"uuid": "123"}
    assert parity.event_crosswalk([dict(m, city_key="other")], [e]) == {}


def test_duplicate_decisions_or_ambiguous_identifiers_are_refused():
    import pytest
    t = table_row()
    with pytest.raises(ValueError, match="duplicate decision"):
        parity.run([t, t], [live_row(t)], DAY, DAY)
    with pytest.raises(ValueError, match="duplicate decision"):
        parity.run([t], [live_row(t), live_row(t)], DAY, DAY)
    with pytest.raises(ValueError, match="ambiguous venue slug"):
        parity.event_crosswalk([], [{"slug": "s", "event_id": "1"}, {"slug": "s", "event_id": "2"}])


def test_a_number_is_judged_within_1e_6():
    t = table_row()
    out = parity.run([t], [live_row(t, t_now_c="1.5000004")], DAY, DAY)
    assert out["unexplained"] == 0
    out = parity.run([t], [live_row(t, t_now_c="1.50002")], DAY, DAY)
    assert out["unexplained"] == 1
    ex = out["rule_vs_table"]["t_now_c"]["examples"][0]
    assert (ex["reference"], ex["other"], ex["city"]) == ("1.5", "1.50002", "madrid")
    assert out["verdict"] == "parity fails: 1 unexplained differences, 0 rows only in the table, 0 only live"


def test_one_side_empty_is_a_difference():
    t = table_row()
    assert parity.run([t], [live_row(t, rmax_c="")], DAY, DAY)["unexplained"] == 1


def test_the_known_source_differences_are_named_not_matched():
    t = table_row()
    out = parity.run([t], [live_row(t, precip_3h_in="")], DAY, DAY)
    assert out["rule_vs_table"]["precip_3h_in"]["known"] == {"trace_precip": 1} and out["unexplained"] == 0

    lk = table_row(city="lucknow")
    out = parity.run([lk], [live_row(lk, fch_now_c="2.0")], DAY, DAY)
    assert out["rule_vs_table"]["fch_now_c"]["known"] == {"lucknow_half_hour": 1}
    out = parity.run([t], [live_row(t, fch_now_c="2.0")], DAY, DAY)
    assert out["unexplained"] == 1, "only Lucknow's hours are the known difference"

    early = table_row(hour="1")
    out = parity.run([early], [live_row(early, fc_bm_tmax_c="2.0", fc_daily_source="lead2")], DAY, DAY)
    assert out["rule_vs_table"]["fc_bm_tmax_c"]["known"] == {"lead1_before_0200": 1}
    assert out["unexplained"] == 0
    late = table_row(hour="5")
    assert parity.run([late], [live_row(late, fc_bm_tmax_c="2.0")], DAY, DAY)["unexplained"] == 1

    out = parity.run([t], [live_row(t, mkt_ages_min="1.0;2.0;3.0", mkt_moves_1h="4")], DAY, DAY)
    assert out["unexplained"] == 0
    assert out["rule_vs_table"]["mkt_moves_1h"]["known"] == {"price_source_age": 1}


def test_the_market_by_step_2_1s_tolerance_and_wide_books_apart():
    t = table_row()
    assert parity.run([t], [live_row(t, mkt_top_p="1.505", mkt_prices="0.205;0.5;0.3")], DAY, DAY)["unexplained"] == 0
    out = parity.run([t], [live_row(t, mkt_top_p="1.53")], DAY, DAY)
    assert out["unexplained"] == 1
    out = parity.run([t], [live_row(t, mkt_prices="0.2;0.5")], DAY, DAY)
    assert out["unexplained"] == 1, "a different number of buckets"
    for spreads, sides in (("0.01;0.2;0.01", "ba;ba;ba"), ("0.01;;0.01", "ba;;ba"),
                           ("0.01;0.05;0.01", "ba;a;ba"), ("0.01;0.05;0.01", "b;ba;ba"), ("0.01;0.02;0.01", "")):
        out = parity.run([t], [live_row(t, spreads=spreads, sides=sides, mkt_top_p="1.9")], DAY, DAY)
        assert out["unexplained"] == 0, (spreads, sides)
        assert out["rows"]["market_wide_or_one_sided"] == 1
        assert out["market_wide_or_one_sided"]["mkt_top_p"]["unexplained"] == 1
        assert out["rule_vs_table"]["t_now_c"]["compared"] == 1, "the weather is still compared"


def test_arrival_is_compared_with_rule_apart():
    t = table_row()
    out = parity.run([t], [live_row(t), live_row(t, mode="arrival", t_now_c="9.0")], DAY, DAY)
    assert out["unexplained"] == 0
    assert out["arrival_vs_rule"]["t_now_c"]["unexplained"] == 1


def test_rows_on_one_side_and_outside_the_window_are_counted():
    a, b, c = table_row("e1"), table_row("e2"), table_row("e3")
    other_day = dict(table_row("e4"), date="2026-10-20")
    out = parity.run([a, b, other_day], [live_row(a), live_row(c)], DAY, DAY)
    assert out["rows"] == {"table": 2, "live_rule": 2, "live_arrival": 0, "matched": 1, "only_table": 1,
                           "only_live": 1, "market_narrow": 1, "market_wide_or_one_sided": 0}
    assert out["unexplained"] == 0 and out["unmatched_rows"] == 2
    assert out["verdict"] == "parity fails: 0 unexplained differences, 1 rows only in the table, 1 only live", \
        "a row on one side only fails parity: a truncated output must not pass (Codex on #391)"
    assert parity.run([a], [], DAY, DAY)["verdict"] == "no matched rows"


def test_the_command_reads_gzip_and_writes_the_result(tmp_path):
    t = table_row()
    paths = {}
    for name, rows in (("table.csv.gz", [t]), ("live.csv.gz", [live_row(t)])):
        p = tmp_path / name
        with gzip.open(p, "wt", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        paths[name] = str(p)
    out = tmp_path / "parity" / "p.json"
    parity.main(["--table", paths["table.csv.gz"], "--live", paths["live.csv.gz"], "--from", DAY, "--to", DAY,
                 "--out", str(out)])
    assert json.loads(out.read_text())["unexplained"] == 0
