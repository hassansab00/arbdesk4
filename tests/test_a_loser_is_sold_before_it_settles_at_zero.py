"""A daily maximum only goes up, so a loss can be certain hours before resolution.

Exits fired on ONE rule: the position's P&L against the desk's stop-loss and
take-profit fractions. Nothing about the clock, the city's peak, or whether the
day had already decided the answer - on a market whose whole subject is the
maximum of a single day.

THE ASYMMETRY IS THE INSTRUMENT'S, NOT A PREFERENCE:

  * once the running max climbs past a closed bucket, a YES on it can never win
    again. That is certain long before the venue resolves, and waiting pays
    $0.00 while the book usually still bids a cent or two.
  * on an open-high bucket ("95F or more") the mirror locks in: the moment the
    max reaches band_lo, YES has won for good and the NO has lost.
  * anything else needs the day to be over, because a max sitting inside a
    bucket at noon can leave it by four.

WINNERS ARE LEFT ALONE. A certain winner settles at $1.00 and selling it pays
the venue a fee on the way out, so holding is strictly better. This rule only
ever recovers residual value from a loser.
"""
import paper_exits as px

CLOSED = {"band_lo": 70, "band_hi": 75, "open_low": False, "open_high": False}
OPEN_HIGH = {"band_lo": 95, "band_hi": None, "open_low": False, "open_high": True}
OPEN_LOW = {"band_lo": None, "band_hi": 60, "open_low": True, "open_high": False}


def test_a_max_past_the_bucket_has_already_beaten_a_yes():
    # The day is NOT decided and it does not need to be.
    assert px.certainly_lost(CLOSED, 80, "YES", "C", day_decided=False)


def test_a_max_still_inside_the_bucket_is_not_a_loss_yet():
    # It could climb out this afternoon; that is why the day must be decided.
    assert not px.certainly_lost(CLOSED, 72, "YES", "C", day_decided=False)


def test_the_same_position_wins_once_the_day_is_decided():
    assert not px.certainly_lost(CLOSED, 72, "YES", "C", day_decided=True)


def test_a_max_that_never_reached_the_bucket_loses_when_the_day_ends():
    assert px.certainly_lost(CLOSED, 60, "YES", "C", day_decided=True)
    assert not px.certainly_lost(CLOSED, 60, "YES", "C", day_decided=False)


def test_an_open_high_bucket_locks_in_and_beats_the_no():
    # "95 or more" reached at 96: YES can never be undone, so NO has lost now.
    assert px.certainly_lost(OPEN_HIGH, 96, "NO", "C", day_decided=False)
    assert not px.certainly_lost(OPEN_HIGH, 96, "YES", "C", day_decided=False)


def test_an_open_high_bucket_not_yet_reached_is_undecided():
    assert not px.certainly_lost(OPEN_HIGH, 90, "NO", "C", day_decided=False)


def test_an_open_low_bucket_is_beaten_by_a_max_above_it():
    # "under 60" with the max already at 65 can never come back.
    assert px.certainly_lost(OPEN_LOW, 65, "YES", "C", day_decided=False)


def test_a_no_only_loses_once_the_day_is_decided_inside_the_bucket():
    assert not px.certainly_lost(CLOSED, 72, "NO", "C", day_decided=False)
    assert px.certainly_lost(CLOSED, 72, "NO", "C", day_decided=True)


def test_the_band_is_read_in_its_own_unit():
    # live_weather keeps Celsius; a US ladder is labelled Fahrenheit. 26.67C is
    # 80F, which is past a 70-75F bucket. Read as Celsius it is below it, and
    # the position would be held to a settlement it cannot win.
    assert px.to_band_unit(26.67, "F") == 80.006
    assert px.certainly_lost(CLOSED, 26.67, "YES", "F", day_decided=False)
    assert not px.certainly_lost(CLOSED, 26.67, "YES", "C", day_decided=False)


def test_no_reading_is_not_a_verdict():
    # A city with no running max yet must never retire a position.
    assert not px.certainly_lost(CLOSED, None, "YES", "C", day_decided=True)


def test_an_unbounded_bucket_missing_its_edge_is_not_a_verdict():
    assert px.band_contains({"open_high": True, "band_lo": None, "band_hi": None}, 80) is None
    assert not px.certainly_lost({"open_high": True, "band_lo": None, "band_hi": None},
                                 80, "YES", "C", day_decided=True)


def test_the_venue_reading_decides_not_the_raw_one():
    """Plan v2 P5.0 item 6. A 23 C bucket is [23, 24). A running max of 23.6 C
    is read by the venue as 24, so a YES on 23 C has already lost - the raw
    23.6 would have held it to a settlement it cannot win."""
    c23 = {"band_lo": 23, "band_hi": 24, "open_low": False, "open_high": False}
    assert px.certainly_lost(c23, 23.6, "YES", "C", day_decided=False)
    assert not px.certainly_lost(c23, 23.4, "YES", "C", day_decided=False)
    # and the day decided at 23.4 is read as 23: inside, so the NO lost
    assert px.certainly_lost(c23, 23.4, "NO", "C", day_decided=True)


def test_a_lost_position_is_held_to_settlement_not_sold(monkeypatch):
    """Hassan, 24 Sep: once the day has peaked and the book has settled on one
    bucket, nobody buys the dead one. The cycle used to try, and
    queue_automatic_paper_exit refused it unless the stop-loss was hit, which
    raised and ended the whole cycle. Now it is counted and left for settlement."""
    import paper_worker

    account = {"account_id": "a1", "policy_version": 1,
               "policy": {"stop_loss_fraction": 0.5, "take_profit_fraction": 0.5}}
    position = {"account_id": "a1", "band_id": "b1", "side": "YES", "shares": 10, "cost_basis": 3}

    def rest_all(table, params, order=None):
        return {"paper_accounts": [account], "paper_positions": [position]}[table]

    def rest(table, params):
        return {
            "paper_orders": [],
            "v_canonical_bands": [{"token_yes": "ty", "token_no": "tn", "band_lo": 23, "band_hi": 24,
                                   "open_low": False, "open_high": False, "market_id": "m1"}],
            "v_canonical_markets": [{"city_key": "london", "resolution_date": "2026-09-24", "unit": "C"}],
            "live_weather": [{"running_max_c": 25.2, "day_decided": True, "local_date": "2026-09-24"}],
        }[table]

    calls = {"book": 0, "rpc": 0}
    monkeypatch.setattr(px, "rest_all", rest_all)
    monkeypatch.setattr(px, "rest", rest)
    monkeypatch.setattr(px, "rpc", lambda *a, **k: calls.__setitem__("rpc", calls["rpc"] + 1))
    logged = {}
    monkeypatch.setattr(px, "log_run", lambda job, status, n, detail: logged.update(detail))
    monkeypatch.setattr(paper_worker, "capture_book",
                        lambda order: calls.__setitem__("book", calls["book"] + 1))

    out = px.cycle()
    assert calls == {"book": 0, "rpc": 0}, "a lost position is neither priced nor sold"
    assert out["exits_queued"] == 0
    assert out["skipped"] == {"lost_hold_to_settlement": 1}
    assert logged["lost_held_to_settlement"] == 1


# --- 29 Sep: a Decimal on a Fahrenheit band ended every cycle ------------------
#
# cycle() passed live_weather.running_max_c through paper_execution.number(),
# the money helper, which returns a Decimal; to_band_unit then did
# Decimal * 9.0 and raised TypeError. All 13 intraday runs from 27 Sep 20:36Z
# to 29 Sep 20:36Z failed in this step (the five tracebacks read end on that
# line), and since the whole cycle died, no position after the first
# Fahrenheit one was ever considered. (GitHub Actions: pipeline_intraday runs
# 110-122 and their job logs, read 29 Sep; ingest_log's newest paper_exits row
# is 25 Sep 12:38Z.) The tests above only passed floats and only drove cycle()
# on a Celsius market, so all of them passed.

from decimal import Decimal

import venue

F_84_85 = {"band_lo": 84, "band_hi": 86, "open_low": False, "open_high": False}


def test_a_fahrenheit_band_reads_a_decimal_like_a_float():
    for reading in (26.67, Decimal("26.67")):
        assert abs(px.to_band_unit(reading, "F") - 80.006) < 1e-9
        assert px.certainly_lost(CLOSED, reading, "YES", "F", day_decided=False)
    assert px.to_band_unit(Decimal("23.6"), "C") == 23.6
    assert venue.venue_round(Decimal("26.67"), "F") == 80
    assert venue.venue_round(Decimal("23.6"), "C") == 24


def _run_cycle(monkeypatch, positions, *, unit, running_max_c, bands_raise=()):
    """cycle() against a fake desk: one account, the given positions, every band
    on one market in `unit`, and the city's live row on that market's day."""
    import paper_worker

    account = {"account_id": "a1", "policy_version": 1,
               "policy": {"stop_loss_fraction": 0.5, "take_profit_fraction": 0.5}}

    def rest_all(table, params, order=None):
        return {"paper_accounts": [account], "paper_positions": positions}[table]

    def rest(table, params):
        if table == "v_canonical_bands" and params["band_id"][3:] in bands_raise:
            raise RuntimeError("the band read failed")
        return {
            "paper_orders": [],
            "v_canonical_bands": [{"token_yes": "ty", "token_no": "tn", **F_84_85, "market_id": "m1"}],
            "v_canonical_markets": [{"city_key": "miami", "resolution_date": "2026-09-29", "unit": unit}],
            "live_weather": [{"running_max_c": running_max_c, "day_decided": False, "local_date": "2026-09-29"}],
        }[table]

    calls = {"book": 0, "rpc": 0}
    logged = {}
    monkeypatch.setattr(px, "rest_all", rest_all)
    monkeypatch.setattr(px, "rest", rest)
    monkeypatch.setattr(px, "rpc", lambda *a, **k: calls.__setitem__("rpc", calls["rpc"] + 1))
    monkeypatch.setattr(px, "log_run",
                        lambda job, status, n, detail: logged.update(detail, status=status, job=job))
    def capture_book(order):
        calls["book"] += 1
        return {"bids": [], "snapshot_id": "s1"}      # a real book with nobody bidding

    monkeypatch.setattr(paper_worker, "capture_book", capture_book)
    return px.cycle(), logged, calls


def _pos(band_id):
    return {"account_id": "a1", "band_id": band_id, "side": "YES", "shares": 10, "cost_basis": 3}


def test_the_cycle_reads_a_fahrenheit_market(monkeypatch):
    # 28.9 C is 84.02 F, read by the venue as 84: still inside 84-85 F, day open
    out, logged, calls = _run_cycle(monkeypatch, [_pos("b1")], unit="F", running_max_c=28.9)
    assert out["errors"] == {} and logged["status"] == "ok"
    assert calls["book"] == 1, "an open position on a Fahrenheit market is priced for an exit"
    assert out["skipped"] == {"no_bids": 1}
    # 30.0 C is 86 F: past 84-85 F, so the YES has lost and is held to settlement
    out, logged, calls = _run_cycle(monkeypatch, [_pos("b1")], unit="F", running_max_c=30.0)
    assert out["skipped"] == {"lost_hold_to_settlement": 1} and out["errors"] == {}
    assert calls == {"book": 0, "rpc": 0}


def test_one_position_that_raises_does_not_cost_the_others_their_turn(monkeypatch):
    out, logged, calls = _run_cycle(monkeypatch, [_pos("b1"), _pos("b2"), _pos("b3")],
                                    unit="F", running_max_c=30.0, bands_raise=("b2",))
    assert out["skipped"] == {"lost_hold_to_settlement": 2}, "b1 and b3 were still considered"
    assert out["errors"] == {"RuntimeError": 1}
    assert logged["status"] == "error", "a position that raised makes the run an error, not ok"
    assert logged["job"] == "paper_exits"
    assert logged["first_errors"] == [{"account_id": "a1", "band_id": "b2", "side": "YES",
                                       "error": "RuntimeError: the band read failed"}]


def test_every_position_reached_is_counted_once(monkeypatch):
    """30 Sep 00:36Z: 16 positions open, 11 in the log - the ones held inside
    the policy's thresholds were counted nowhere. The log now says how many
    positions it reached, and each is queued, skipped for a reason or an error."""
    free = {**_pos("b1"), "cost_basis": 0}
    out, logged, _calls = _run_cycle(monkeypatch, [free, _pos("b2"), _pos("b3")],
                                     unit="F", running_max_c=28.9, bands_raise=("b2",))
    assert out["skipped"] == {"zero_cost_basis": 1, "no_bids": 1} and out["errors"] == {"RuntimeError": 1}
    assert out["positions"] == logged["positions"] == 3
    assert out["positions"] == out["exits_queued"] + sum(out["skipped"].values()) + sum(out["errors"].values())


def test_the_step_goes_red_only_after_every_position_had_its_turn(monkeypatch):
    monkeypatch.setattr(px, "cycle", lambda: {"exits_queued": 0, "skipped": {}, "errors": {"TypeError": 1}})
    assert px.main() == 1
    monkeypatch.setattr(px, "cycle", lambda: {"exits_queued": 0, "skipped": {}, "errors": {}})
    assert px.main() == 0
