"""The learning loop was open at the far end.

Measured 2026-09-19:

    4,761 signals
    2,217 of them on a band the venue has since settled
    1,963 rows in fact_signal_outcome
        0 of those rows with settled_yes

bank_signals() wrote the fill and the P&L and never the band's actual outcome,
so nothing could measure which strategy, forecast version or calibration made
a CORRECT CALL - only which made money, which is a different and much noisier
question. A correct call that was never filled looked identical to no signal.

And it banked the wrong rows. It keyed off a CLOSED TRADE, so it banked a
signal whose trade closed and skipped one whose band settled: only 353 rows
were in both sets, and 1,864 settled signals had never been banked at all.
"""

import re
from pathlib import Path

import pytest

import databank as db

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260919190000_signals_learn_from_settlement.sql"


# ---------------------------------------------------------------------------
# the call, which is not the outcome
# ---------------------------------------------------------------------------
def test_a_yes_call_is_right_when_the_band_wins():
    assert db.signal_correct("ENTER", "YES", True) is True
    assert db.signal_correct("ENTER", "YES", False) is False


def test_a_no_call_is_right_when_the_band_loses():
    """The reason settled_yes and signal_correct are two columns. Scoring a NO
    signal by the band's result alone reads every one of them backwards."""
    assert db.signal_correct("ENTER", "NO", False) is True
    assert db.signal_correct("ENTER", "NO", True) is False


def test_an_exit_is_not_scored_by_the_band_s_result():
    """"Get out of this now" is a claim about the price, not about which bucket
    the day landed in. A position exited at a profit before a band that went on
    to win was a good exit or a bad one depending on where it was sold."""
    assert db.signal_correct("EXIT", "YES", True) is None


def test_an_unsettled_band_scores_nothing():
    assert db.signal_correct("ENTER", "YES", None) is None


# ---------------------------------------------------------------------------
# what gets banked
# ---------------------------------------------------------------------------
def _signal(signal_id=1, band="b1", side="YES", action="ENTER", snapshot=None):
    s = {"signal_id": signal_id, "strategy_id": "s1", "band_id": band,
         "city_key": "london", "side": side, "action": action, "reason": "r",
         "fired_at": "2026-09-17T12:00:00+00:00", "severity": "high",
         "price_at_fire": 0.30, "prob_at_fire": 0.42, "edge_at_fire": 9.0,
         "status": "fired"}
    if snapshot is not None:
        s["payload"] = {"decision_snapshot": {band: snapshot}}
    return s


def _outcome(band="b1", settled=True):
    return {"band_id": band, "settled_yes": settled, "for_date": "2026-09-17",
            "captured_at": "2026-09-18T06:00:00+00:00"}


def _trade(band="b1", closed=True):
    t = {"trade_id": "t1", "band_id": band, "strategy_id": "s1",
         "opened_at": "2026-09-17T12:05:00+00:00", "avg_fill_price": 0.33,
         "shares": 20}
    if closed:
        t.update({"closed_at": "2026-09-18T00:00:00+00:00",
                  "gross_pnl": 5.0, "net_pnl": 4.5})
    return t


def _bank(monkeypatch, signals, outcomes, trades, banked=()):
    def fake_rest_all(path, params=None, **kw):
        if path == "fact_signal_outcome":
            return [{"signal_id": i} for i in banked]
        if path == "signals":
            return signals
        if path == "fact_band_outcome":
            return outcomes
        if path == "paper_trades":
            return trades
        return []
    monkeypatch.setattr(db, "rest_all", fake_rest_all)
    return db.bank_signals(7, force=False)


def test_a_settled_signal_is_banked_even_though_it_never_filled(monkeypatch):
    """THE CENTRAL FIX. A call the desk never got filled on is still a call,
    and whether it was right is the thing every strategy decision rests on."""
    rows = _bank(monkeypatch, [_signal()], [_outcome(settled=True)], [])
    assert len(rows) == 1
    r = rows[0]
    assert r["filled"] is False
    assert r["settled_yes"] is True
    assert r["signal_correct"] is True
    assert r["net_pnl"] is None, "an unfilled call made no money and must not claim any"


def test_a_signal_on_an_unsettled_band_is_not_banked(monkeypatch):
    assert _bank(monkeypatch, [_signal()], [], [_trade()]) == []


def test_pnl_is_only_recorded_once_the_trade_actually_closed(monkeypatch):
    """An open position has an unrealised mark, not a result. Freezing one as
    net_pnl would put a number that is still moving into an immutable row."""
    rows = _bank(monkeypatch, [_signal()], [_outcome()], [_trade(closed=False)])
    assert rows[0]["filled"] is True
    assert rows[0]["net_pnl"] is None and rows[0]["gross_pnl"] is None


def test_a_filled_and_closed_signal_carries_the_result_and_the_slippage(monkeypatch):
    rows = _bank(monkeypatch, [_signal()], [_outcome()], [_trade()])
    assert rows[0]["net_pnl"] == 4.5
    assert rows[0]["slippage_c"] == pytest.approx(3.0), (
        "fired at 0.30, filled at 0.33 - three cents is where a correct call "
        "turns into a loss")


def test_the_versions_that_produced_the_call_travel_with_it(monkeypatch):
    """So "which forecast version made a correct call" has an answer."""
    snap = {"forecast_version": "11111111-1111-1111-1111-111111111111",
            "calibration_version": "22222222-2222-2222-2222-222222222222",
            "cost_version": "33333333-3333-3333-3333-333333333333"}
    rows = _bank(monkeypatch, [_signal(snapshot=snap)], [_outcome()], [])
    assert rows[0]["forecast_version"] == snap["forecast_version"]
    assert rows[0]["calibration_version"] == snap["calibration_version"]


def test_a_signal_fired_before_the_snapshot_existed_still_banks(monkeypatch):
    rows = _bank(monkeypatch, [_signal()], [_outcome()], [])
    assert rows[0]["forecast_version"] is None


def test_the_decision_and_the_settlement_are_different_moments(monkeypatch):
    """fired_at is when the desk decided, settled_at is when the venue's
    evidence confirmed the outcome, and captured_at is when the row was
    frozen. Measuring how long a call took to be proved right needs the first
    two, and neither of them is the third."""
    rows = _bank(monkeypatch, [_signal()], [_outcome()], [])
    assert rows[0]["fired_at"] == "2026-09-17T12:00:00+00:00"
    assert rows[0]["settled_at"] == "2026-09-18T06:00:00+00:00"
    assert rows[0]["outcome_source"] == "fact_band_outcome"


def test_a_no_signal_on_a_losing_band_banks_as_correct(monkeypatch):
    rows = _bank(monkeypatch, [_signal(side="NO")], [_outcome(settled=False)], [])
    assert rows[0]["settled_yes"] is False
    assert rows[0]["signal_correct"] is True


def test_rows_already_frozen_are_not_offered_again(monkeypatch):
    assert _bank(monkeypatch, [_signal(1)], [_outcome()], [], banked=[1]) == []


# ---------------------------------------------------------------------------
# and the 1,963 rows that can never be updated
# ---------------------------------------------------------------------------
def _sql():
    return "\n".join(l for l in MIGRATION.read_text().splitlines()
                     if not l.strip().startswith("--"))


def test_the_frozen_rows_are_not_rewritten():
    """fact_signal_outcome carries proprietary_fact_immutable: every UPDATE is
    refused, which is what makes those rows admissible. A migration that
    rewrote 1,963 of them to add a column would be exactly the edit that guard
    exists to prevent."""
    sql = _sql()
    assert "update public.fact_signal_outcome" not in sql.lower()
    assert "delete from public.fact_signal_outcome" not in sql.lower()


def test_the_outcome_is_served_for_them_anyway():
    """The band's result is still known - in fact_band_outcome, keyed by the
    same band. Joining it closes the loop for the whole history without
    touching one byte of frozen evidence."""
    sql = _sql()
    view = sql[sql.index("create or replace view v_signal_outcome"):]
    assert "left join fact_band_outcome b on b.band_id = f.band_id" in view
    assert "coalesce(f.settled_yes, b.settled_yes)" in view
    assert "outcome_source" in view, (
        "a reader has to be able to tell a frozen outcome from a joined one")


def test_the_view_scores_a_no_call_the_right_way_round():
    view = _sql()
    body = view[view.index("create or replace view v_signal_outcome"):]
    assert re.search(r"when f\.side = 'NO'\s+then not", body), (
        "a NO signal on a band that lost is a CORRECT call")


def test_being_right_and_making_money_are_reported_separately():
    """A strategy can be right more often than not and still lose, if what it
    is right about is already in the price. One number cannot say both."""
    sql = _sql()
    card = sql[sql.index("create or replace view v_signal_scorecard"):]
    assert "hit_rate_pct" in card and "realised_net_pnl" in card


def test_versions_get_their_own_scorecard():
    """Grouping the main scorecard by version would split every strategy's
    history into a null half and a non-null half the day versions start
    arriving, and hide the overall hit rate behind the change."""
    assert "create or replace view v_signal_scorecard_by_version" in _sql()
