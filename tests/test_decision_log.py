"""The decision log (plan v2 P5.11): one row per (run, strategy, city-day)."""
import datetime as dt
import uuid

import pytest

import decision_log as dl
import signal_engine as se
from strategies.base import Signal

NOW = dt.datetime(2026, 9, 25, 8, 36, tzinfo=dt.timezone.utc)
RUN = str(uuid.UUID(int=7))
BANDS = {"b1": ("nyc", "2026-09-25"), "b2": ("nyc", "2026-09-25"), "b3": ("dal", "2026-09-25")}


def _sig(sid, band, action="ENTER", shares=10.0, price=0.40):
    return Signal(strategy_id=sid, band_id=band, side="YES", action=action, reason="t",
                  price_at_fire=price, prob_at_fire=0.5, edge_at_fire=0.1, suggested_shares=shares,
                  confidence=0.7, regime_label="SHARP", severity="info", dedupe_key=f"{sid}:{band}:{action}")


@pytest.mark.parametrize("outs,held,want", [
    ([("EXIT", "written"), ("ENTER", "written")], 0, ("SELL", "exit")),
    ([("ENTER", "written")], 0, ("BUY", "enter")),
    ([("ENTER", "deduped")], 0, ("HOLD", "already_decided")),
    ([("ENTER", "held_no_cost_version")], 0, ("WAIT", "no_cost_version")),
    ([("ENTER", "blocked")], 0, ("NONE", "conflict")),
    ([], 12.0, ("HOLD", "holding")),
    ([], 0, ("NONE", "no_signal")),
])
def test_the_verdict_takes_the_strongest_thing_that_happened(outs, held, want):
    outcomes = [(_sig("s1", "b1", action=a), o) for a, o in outs]
    assert dl.verdict(outcomes, held) == want


def test_a_signal_sized_to_nothing_is_not_a_buy():
    assert dl.verdict([(_sig("s1", "b1", shares=0.0), "written")], 0) == ("NONE", "sized_to_zero")


def test_every_strategy_gets_a_row_for_every_city_day_even_when_it_does_nothing():
    rows, unmapped = dl.build(RUN, NOW, ["s1", "s2"], set(BANDS.values()),
                              [(_sig("s1", "b1"), "written")], BANDS, {}, "sha1")
    assert unmapped == 0
    got = {(r["strategy_id"], r["city_key"]): (r["action"], r["reason_code"]) for r in rows}
    assert got == {("s1", "nyc"): ("BUY", "enter"), ("s1", "dal"): ("NONE", "no_signal"),
                   ("s2", "nyc"): ("NONE", "no_signal"), ("s2", "dal"): ("NONE", "no_signal")}
    buy = next(r for r in rows if r["action"] == "BUY")
    assert buy["target_usd"] == pytest.approx(4.0) and buy["held_usd"] == 0
    assert buy["n_signals"] == 1 and buy["params_version"] == "sha1"
    assert buy["run_id"] == RUN and buy["decided_at"] == NOW.isoformat()
    assert all(set(r) == set(buy) for r in rows)                 # one shape: one PostgREST insert


def test_target_is_what_is_held_plus_what_the_buy_adds():
    rows, _ = dl.build(RUN, NOW, ["s1"], {("nyc", "2026-09-25")},
                       [(_sig("s1", "b1"), "written"), (_sig("s1", "b2", shares=5, price=0.2), "written")],
                       BANDS, {("s1", "nyc", "2026-09-25"): 3.0}, "v")
    assert rows[0]["target_usd"] == pytest.approx(3.0 + 4.0 + 1.0) and rows[0]["held_usd"] == 3.0


def test_a_holding_off_the_board_still_gets_its_row():
    rows, _ = dl.build(RUN, NOW, ["s1"], set(), [], BANDS, {("s1", "nyc", "2026-09-25"): 5.0}, "v")
    assert [(r["action"], r["reason_code"]) for r in rows] == [("HOLD", "holding")]


def test_a_signal_on_a_band_nobody_can_place_is_counted_not_dropped_silently():
    rows, unmapped = dl.build(RUN, NOW, ["s1"], set(), [(_sig("s1", "zz"), "written")], BANDS, {}, "v")
    assert rows == [] and unmapped == 1


def test_held_is_read_per_strategy_ledger():
    positions = [{"account_id": "a1", "band_id": "b1", "cost_basis": "2.5"},
                 {"account_id": "a1", "band_id": "b2", "cost_basis": "1.5"},
                 {"account_id": "a9", "band_id": "b1", "cost_basis": "99"}]      # not a ledger
    assert dl.held_by_city_day(positions, {"a1": "s1"}, BANDS) == {("s1", "nyc", "2026-09-25"): 4.0}


def test_the_engine_writes_its_decisions_and_reports_a_failure(monkeypatch):
    class V:
        band_id, city_key, resolution_date = "b1", "nyc", "2026-09-25"

    class C:
        strategy_id = "s1"

    written = {}
    monkeypatch.setattr(se, "insert", lambda table, rows: written.setdefault(table, rows))
    counts = {}
    assert se.write_decisions(RUN, NOW, [C], [V], [(_sig("s1", "b1"), "written")], [], {}, counts)
    assert [r["action"] for r in written["decisions"]] == ["BUY"] and counts["decisions"] == 1

    def boom(table, rows):
        raise RuntimeError("relation decisions does not exist")
    monkeypatch.setattr(se, "insert", boom)
    counts = {}
    assert se.write_decisions(RUN, NOW, [C], [V], [], [], {}, counts) is False
    assert "does not exist" in counts["decisions_error"]
