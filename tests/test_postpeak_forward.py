"""The post-peak rule in shadow (docs/POSTPEAK_PREREG.md, tools/postpeak_forward.py):
the rule is the one the study scored, the looks are counts fixed beforehand, a
look reads whole dates and nothing before it is due, and the document says what
the committed design holds."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
DESIGN = json.load(open(os.path.join(ROOT, "data", "eval", "postpeak", "design_2026-11-15.json")))
STUDY = json.load(open(os.path.join(ROOT, "data", "eval", "strategy_study_2026-11-15", "result.json")))
DOC = open(os.path.join(ROOT, "docs", "POSTPEAK_PREREG.md")).read()


def _pc(x):
    return f"{100 * x:+.1f}%"


def _row(v):
    return f"{100 * v['win_rate'] + 1e-9:.1f}% | {v['mean_cost']:.3f} | {_pc(v['return_per_dollar'])} | {_pc(v['ci90'][0])} to {_pc(v['ci90'][1])}"


def test_the_design_is_the_rule_the_study_scored():
    assert DESIGN["result"] == STUDY["postpeak"]["both"]
    assert (DESIGN["min_p"], DESIGN["max_ask"]) == (0.9, 0.97)
    assert DESIGN["ungraded_trades"] == 0 and DESIGN["by_status"]["trade"] == DESIGN["result"]["n"]


def test_the_document_says_what_the_design_holds():
    for key in ("result", "without_the_best_date", "asks_of_0.50_or_more", "control_below_min_p"):
        assert _row(DESIGN[key]) in DOC, key
    s = DESIGN["by_status"]
    assert f"{s['below_min_p']} had a top pick below 0.90, {s['ask_at_or_over_max']} were offered" in DOC
    assert f"{s['no_ask']} had no ask, and {s['trade']} traded" in DOC
    assert f"{DESIGN['calendar_days']} calendar days, {DESIGN['first_calls']:,} first post-peak" in DOC
    assert f"needs {DESIGN['trades_for_80pc_power_at_this_return']} trades, rounded up to {DESIGN['last_look_trades']}" in DOC
    assert f"standard error of {100 * DESIGN['standard_error_by_date']:.2f} points" in DOC
    assert f"({DESIGN['trades_a_calendar_day']} trades a calendar day)" in DOC
    assert f"**It lost {len(DESIGN['losses'])} of {DESIGN['result']['n']}.**" in DOC
    assert f"said {DESIGN['mean_model_probability']:.3f} on average" in DOC
    dev, hold = STUDY["postpeak"]["development"], STUDY["postpeak"]["holdout"]
    assert f"{_pc(dev['return_per_dollar'])} on the {dev['n']} trades to 31 Oct" in DOC
    assert f"{_pc(hold['return_per_dollar'])} on the {hold['n']}" in DOC


def test_the_looks_are_the_counts_the_design_derived():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    assert P.LOOKS == {1: DESIGN["first_look_trades"], 2: DESIGN["last_look_trades"]}
    assert P.FIRST_FORWARD_DATE > P.STUDY_TO and P.FIRST_FORWARD_DATE == "2026-11-16"
    assert "**First look: 110 graded trades.**" in DOC and "**Last look: 220 graded trades.**" in DOC
    assert "`postpeak_forward.FIRST_FORWARD_DATE`" in DOC and "**16 Nov 2026**" in DOC


def test_the_rule():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    book = {"a": {"ask": 0.93, "bid": 0.9}, "b": {"ask": 0.05}}
    assert P.decide({"a": 0.95, "b": 0.05}, book) == ("trade", "a", 0.95, 0.93)
    assert P.decide({"a": 0.9, "b": 0.1}, book)[0] == "trade", "0.90 itself qualifies"
    assert P.decide({"a": 0.899, "b": 0.101}, book)[0] == "below_min_p"
    assert P.decide({"a": 0.95, "b": 0.05}, {"a": {"ask": 0.97}})[0] == "ask_at_or_over_max", "0.97 itself does not"
    assert P.decide({"a": 0.95, "b": 0.05}, {"a": {"ask": None, "bid": 0.9}})[0] == "no_ask"
    assert P.decide({"a": 0.95, "b": 0.05}, {"b": {"ask": 0.1}})[0] == "no_ask"
    assert P.decide({"a": 0.95, "b": 0.05}, None)[0] == "no_book"
    assert P.decide(None, book)[0] == "no_ladder"
    assert P.decide({"b": 0.5, "a": 0.5}, book)[1] == "a", "a tie goes to the smallest bucket id"


def test_a_trade_is_graded_only_on_a_settled_winner_that_is_on_the_ladder():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    ok = {"settled_at": "2026-11-17T10:00:00Z", "winner_band_id": "a", "ladder_has_winner": "True"}
    assert P.grade("a", ok) == "win" and P.grade("b", ok) == "loss"
    assert P.grade("a", None) == "not_settled" and P.grade("a", dict(ok, settled_at="")) == "not_settled"
    assert P.grade("a", dict(ok, winner_band_id="")) == "no_winner"
    assert P.grade("a", dict(ok, ladder_has_winner="False")) == "winner_not_on_ladder"


def _record(days, per_day, settled=lambda d, i: True, win=lambda d, i: True, p=0.95, ask=0.9):
    """A mirror of `per_day` post-peak calls on each of `days`, as the reader yields it."""
    calls, outcomes = [], []
    for d in days:
        for i in range(per_day):
            cid = f"{d}-{i:03d}"
            calls.append({"checkpoint_id": cid, "city_key": f"c{i:03d}", "target_date": d, "checkpoint": "postpeak_1h",
                          "decided_at": f"{d}T20:00:{i % 60:02d}+00:00", "engine_version": "git:x",
                          "forecast_model": "wxrd3", "model_path": "wxpredict",
                          "probs": json.dumps({"a": p, "b": round(1 - p, 6)}),
                          "market": json.dumps({"a": {"ask": ask, "bid": ask - 0.02}, "b": {"ask": 0.1, "bid": 0.05}})})
            if settled(d, i):
                outcomes.append({"checkpoint_id": cid, "settled_at": f"{d}T23:59:00+00:00",
                                 "winner_band_id": "a" if win(d, i) else "b", "ladder_has_winner": "True"})
    return lambda table: iter(calls if table == "prediction_checkpoints" else outcomes)


def _days(n, first=16):
    import datetime as dt
    return [(dt.date(2026, 11, first) + dt.timedelta(days=i)).isoformat() for i in range(n)]


def test_the_count_shows_no_win_and_no_return():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    out = P.count(_record(_days(3), 10))
    assert (out["first_calls"], out["trades"], out["trades_graded"], out["dates_with_a_trade"]) == (30, 30, 30, 3)
    assert out["looks"]["1"] == {"needs": 110, "due": False, "waiting": "30 graded trades of 110"}
    text = json.dumps(out)
    assert "return" not in text.replace("no win, no return", "") and "win_rate" not in text and "ci90" not in text


def test_a_look_is_refused_before_its_count_and_reads_whole_dates_after():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    with pytest.raises(SystemExit, match="look 1 is not due: 100 graded trades of 110"):
        P.look(1, _record(_days(10), 10))
    with pytest.raises(SystemExit, match="look 2 is not due"):
        P.look(2, _record(_days(12), 10))
    # 12 days of 10: the 110th graded trade is on day 11, so day 11 is read whole and day 12 is not
    out = P.look(1, _record(_days(12), 10))
    assert out["to"] == _days(12)[10] and out["result"]["n"] == 110 and out["result"]["dates"] == 11
    # more dates arriving later do not change what look 1 read
    assert P.look(1, _record(_days(30), 10)) == out


def test_a_look_waits_for_a_recent_unsettled_trade_and_reports_an_old_one():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    days = _days(13)
    late = lambda d, i: not (d == days[5] and i == 0)   # noqa: E731  one trade on day 6 never settles
    with pytest.raises(SystemExit, match="1 trades on or before .* not settled yet"):
        P.look(1, _record(days, 10, settled=late))
    out = P.look(1, _record(_days(20), 10, settled=late))      # day 6 is now over 7 days old
    # the 110th graded trade is now on day 12, read whole: 12 x 10 less the one that never settled
    assert out["to"] == days[11] and out["trades_not_graded"] == {"not_settled": 1} and out["result"]["n"] == 119
    assert out["with_ungraded_as_losses"]["n"] == 120
    assert out["with_ungraded_as_losses"]["return_per_dollar"] < out["result"]["return_per_dollar"]


def test_the_verdicts():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    up, flat, down = {"return_per_dollar": 0.05, "ci90": [0.01, 0.09]}, {"return_per_dollar": 0.02, "ci90": [-0.02, 0.06]}, \
        {"return_per_dollar": -0.03, "ci90": [-0.07, 0.01]}
    assert P.verdict(1, up, up) == P.verdict(1, flat, flat) == "continues to the last look", "no acceptance at look 1"
    assert P.verdict(1, down, down) == "failed: stopped"
    assert P.verdict(1, {"return_per_dollar": 0.0, "ci90": [-0.04, 0.04]}, flat) == "failed: stopped"
    assert P.verdict(2, up, flat) == "positive research evidence"
    assert P.verdict(2, up, down) == "insufficient evidence", "one date must not carry it"
    assert P.verdict(2, flat, flat) == "insufficient evidence"
    assert P.verdict(2, down, down) == "failed"
    assert P.verdict(2, {"return_per_dollar": -0.05, "ci90": [-0.09, -0.01]}, down) == "failed"


def test_the_control_is_the_same_call_below_the_threshold():
    pytest.importorskip("numpy")
    import postpeak_forward as P
    rows = P.calls("2026-11-16", None, _record(_days(2), 5, p=0.8))
    assert all(r["status"] == "below_min_p" for r in rows) and len(P.control(rows)) == 10
    assert P.count(_record(_days(2), 5, p=0.8))["trades"] == 0


def test_no_forward_date_was_read_before_the_file_was_written():
    """The first forward date is after the last date the study's mirror held."""
    assert STUDY["last_date"] < "2026-11-16" and DESIGN["to"] == STUDY["last_date"]
