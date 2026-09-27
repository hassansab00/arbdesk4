"""The width around the corrected centre, scored forward (plan v2.3 P3.9 part 3).

Each confirmed market: the engine's last day-ahead ladder as served, against
the same ladder re-priced with the stored width around the same centre. Only
the width may differ, so a ladder that does not reproduce from its own centre
and width is skipped, not scored.
"""
import datetime as dt
import math

import pytest

import station_width_score as ws
from probability_engine import clamp_prob, compute_band_probabilities

B = [{"band_id": f"00000000-0000-0000-0000-00000000000{i}", "band_lo": lo, "band_hi": hi,
      "open_low": ol, "open_high": oh}
     for i, (lo, hi, ol, oh) in enumerate([(None, 18, True, False), (18, 19, False, False), (19, 20, False, False),
                                           (20, 21, False, False), (21, None, False, True)], start=1)]
WIN = B[2]["band_id"]                       # 19-20 C
MARKET = {"market_id": "11111111-0000-0000-0000-000000000000", "city_key": "london",
          "resolution_date": "2026-09-29", "winning_band_id": WIN}


def _stored(centre, sigma, width, at="2026-09-28T20:36:00+00:00", lead=1, **over):
    probs = compute_band_probabilities(centre, sigma, "C", B)
    return [dict({"band_id": b, "computed_at": at, "raw_prob": round(clamp_prob(p), 6),
                  "calibrated_prob": round(clamp_prob(p), 6), "centre_c": centre, "sigma_c": sigma,
                  "station_width_c": width, "lead_days": lead, "observed_floor_c": None,
                  "forecast_version": "aaaaaaaa-0000-0000-0000-000000000000"}, **over)
            for b, p in probs]


def _midnight():
    return ws.local_midnight("2026-09-29", "Europe/London")


def test_the_scores_are_the_ladders_own_worked_by_hand():
    rows = _stored(19.4, 1.5, 1.0)
    row, why = ws.score_market(MARKET, "C", B, ws.day_ahead_pricing(rows, _midnight()), station_max_c=19.6)
    assert why is None
    served = {r["band_id"]: r["raw_prob"] for r in rows}
    width = {b: round(clamp_prob(p), 6) for b, p in compute_band_probabilities(19.4, 1.0, "C", B)}
    assert row["log_loss_served"] == pytest.approx(-math.log(served[WIN]), abs=1e-6)
    assert row["log_loss_width"] == pytest.approx(-math.log(width[WIN]), abs=1e-6)
    assert row["brier_width"] == pytest.approx(sum((p - (b == WIN)) ** 2 for b, p in width.items()), abs=1e-6)
    assert row["hit_served"] and row["hit_width"], "19.4 C sits in the 19-20 band either way"
    assert row["log_loss_width"] < row["log_loss_served"], "a narrower width on a right centre scores better"
    assert row["reproduce_max_abs"] <= ws.REPRODUCE_TOL
    assert row["crps_width"] < row["crps_served"]
    assert row["cover80_served"] and row["cover80_width"]
    assert (row["served_sigma_c"], row["station_width_c"], row["n_bands"]) == (1.5, 1.0, 5)
    assert row["priced_at"] == "2026-09-28T20:36:00+00:00" and row["for_date"] == "2026-09-29"


def test_a_wrong_centre_punishes_the_narrow_width():
    rows = _stored(21.8, 1.5, 0.8)                  # the winner 19-20 is far from 21.8
    row, _ = ws.score_market(MARKET, "C", B, ws.day_ahead_pricing(rows, _midnight()))
    assert row["log_loss_width"] > row["log_loss_served"]
    assert row["station_max_c"] is None and row["crps_served"] is None and row["cover80_width"] is None


def test_the_pricing_is_the_last_before_the_citys_own_midnight_at_lead_one_or_more():
    la = ws.local_midnight("2026-09-29", "America/Los_Angeles")
    assert la == dt.datetime(2026, 9, 29, 7, 0, tzinfo=dt.timezone.utc), "PDT is UTC-7"
    rows = (_stored(19.0, 1.5, 1.0, at="2026-09-29T04:36:00+00:00")        # 21:36 local, the day before: this one
            + _stored(18.0, 1.5, 1.0, at="2026-09-29T00:36:00+00:00")      # earlier
            + _stored(25.0, 1.5, 1.0, at="2026-09-29T08:36:00+00:00")      # after the day began
            + _stored(26.0, 1.5, 1.0, at="2026-09-29T06:00:00+00:00", lead=0))   # same day
    at, ladder = ws.day_ahead_pricing(rows, la)
    assert at == dt.datetime(2026, 9, 29, 4, 36, tzinfo=dt.timezone.utc)
    assert {r["centre_c"] for r in ladder.values()} == {19.0}
    assert ws.day_ahead_pricing(rows, dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc)) is None


@pytest.mark.parametrize("rows, why", [
    (_stored(19.4, 1.5, None), "no stored width on the pricing"),
    (_stored(19.4, 1.0, 1.0), "the stored width priced (the switch was on)"),
    (_stored(19.4, 1.5, 1.0, observed_floor_c=19.0), "the pricing carries an observed floor"),
    (_stored(19.4, 1.5, 1.0, calibrated_prob=0.2), "a calibration map was applied"),
    (_stored(19.4, 1.5, 1.0)[:4], "the pricing does not cover the canonical ladder"),
    (_stored(19.4, 1.5, 1.0, raw_prob=None), "a band of the pricing has no probability"),
    ([], "no day-ahead pricing before the local day"),
])
def test_what_cannot_be_compared_fairly_is_skipped_and_named(rows, why):
    pricing = ws.day_ahead_pricing(rows, _midnight()) if rows else None
    row, reason = ws.score_market(MARKET, "C", B, pricing)
    assert row is None and reason == why


def test_a_ladder_that_does_not_reproduce_is_not_scored():
    rows = _stored(19.4, 1.5, 1.0)
    rows[0]["raw_prob"] += 0.01                     # not what (centre_c, sigma_c) produces
    rows[0]["calibrated_prob"] = rows[0]["raw_prob"]
    row, why = ws.score_market(MARKET, "C", B, ws.day_ahead_pricing(rows, _midnight()))
    assert row is None and why == "the served ladder does not reproduce from its centre and width"


def test_a_winner_off_the_ladder_is_skipped():
    m = dict(MARKET, winning_band_id="99999999-0000-0000-0000-000000000000")
    row, why = ws.score_market(m, "C", B, ws.day_ahead_pricing(_stored(19.4, 1.5, 1.0), _midnight()))
    assert row is None and why == "the winner is not on the ladder"


def _rows(dates, per_date, gain):
    out = []
    for d in dates:
        for i in range(per_date):
            g = gain(d, i)
            out.append({"for_date": d, "unit": "C" if i % 4 else "F", "served_sigma_c": 1.5, "station_width_c": 1.0,
                        "log_loss_served": 2.0, "log_loss_width": 2.0 - g, "brier_served": 0.8,
                        "brier_width": 0.8 - g / 10, "hit_served": i % 3 == 0, "hit_width": i % 2 == 0,
                        "station_max_c": 20.0, "crps_served": 0.7, "crps_width": 0.7 - g / 5,
                        "cover80_served": True, "cover80_width": i % 5 != 0})
    return out


def test_the_verdict_needs_min_dates_and_a_lower_bound_above_zero():
    days = [f"2026-10-{d:02d}" for d in range(1, 11)]
    few = ws.summarise(_rows(days[:ws.MIN_DATES - 1], 40, lambda d, i: 0.1))
    assert few["verdict"] == "not yet" and few["dates"] == ws.MIN_DATES - 1
    ready = ws.summarise(_rows(days, 40, lambda d, i: 0.1 + 0.01 * (i % 3)))
    assert ready["verdict"] == "ready" and ready["log_loss_gain_90"][0] > 0
    # i % 3 over 40 city-days: fourteen 0s, thirteen 1s, thirteen 2s -> 0.1 + 0.01 * 39 / 40
    assert ready["log_loss_gain"] == pytest.approx(0.1 + 0.01 * 39 / 40, abs=1e-4)
    worse = ws.summarise(_rows(days, 40, lambda d, i: -0.1))
    assert worse["verdict"] == "the width scores worse"
    mixed = ws.summarise(_rows(days, 40, lambda d, i: 0.3 if int(d[-2:]) % 2 else -0.3))
    assert mixed["verdict"] == "not yet" and mixed["log_loss_gain_90"][0] < 0 < mixed["log_loss_gain_90"][1]
    assert ws.summarise([]) ["verdict"] == "not yet"


def test_the_bootstrap_resamples_whole_dates_and_is_repeatable():
    days = [f"2026-10-{d:02d}" for d in range(1, 9)]
    rows = _rows(days, 30, lambda d, i: 0.5 if d == "2026-10-01" else 0.0)
    a, b = ws.summarise(rows), ws.summarise(rows)
    assert a == b, "a fixed seed gives the same interval every night"
    # one date carries all the gain: a date-block interval must reach down to 0
    assert a["log_loss_gain_90"][0] == pytest.approx(0.0, abs=1e-9)


def _run(monkeypatch, markets, done, prices, labels=(), dry=False):
    import sys
    import types
    import common as real_common
    written, logged, fact = {}, [], [dict(r) for r in done]
    common = types.ModuleType("common")

    def rest_all(path, params=None, **k):
        p = dict(params or [])
        if path == "v_venue_market_resolution":
            assert p["resolution_state"] == "eq.confirmed" and p["resolution_date"].startswith("gte.")
            return [m for m in markets if str(m["resolution_date"]) >= p["resolution_date"][4:]]
        if path == "fact_station_width_score":
            return [dict(r) for r in fact]
        if path == "cities":
            return [{"city_key": "london", "unit": "C", "timezone": "Europe/London"}]
        if path == "v_canonical_bands":
            return [dict(b, market_id=m["market_id"]) for m in markets for b in B
                    if m["market_id"] in p["market_id"]]
        if path == "band_probabilities":
            return [r for r in prices if r["band_id"] in p["band_id"]]
        if path == "derived_city_day_features":
            return list(labels)
        raise AssertionError(path)

    def upsert(table, rows, key):
        written.setdefault(table, []).extend(rows)
        fact.extend(rows)
        return len(rows)
    common.rest_all = rest_all
    common.upsert = upsert
    common.upsert_replace = lambda *a, **k: (_ for _ in ()).throw(AssertionError("a fact is never replaced"))
    common.day_had_ended = real_common.day_had_ended
    common.log_run = lambda job, status, n, detail: logged.append((job, status, n, detail))
    monkeypatch.setitem(sys.modules, "common", common)
    argv = ["--today", "2026-10-01"] + (["--dry-run"] if dry else [])
    return ws.main(argv), written, logged


def test_a_night_scores_the_new_markets_once_and_logs_the_verdict(monkeypatch):
    early = dict(MARKET, market_id="33333333-0000-0000-0000-000000000000", resolution_date="2026-09-20")
    prices = _stored(19.4, 1.5, 1.0)
    labels = [{"city_key": "london", "obs_date": "2026-09-29", "max_c": 19.6,
               "computed_at": "2026-09-30T05:00:00+00:00"}]
    detail, written, logged = _run(monkeypatch, [MARKET, early], [], prices, labels)
    rows = written["fact_station_width_score"]
    assert [r["market_id"] for r in rows] == [MARKET["market_id"]], "before FORWARD_FROM is never scored"
    assert rows[0]["station_max_c"] == 19.6, "a whole-day station maximum is used"
    job, status, n, d = logged[0]
    assert (job, status, n) == ("P3.9_width_score", "ok", 1)
    assert d["summary"]["markets"] == 1 and d["summary"]["verdict"] == "not yet"
    assert d["priors"]["forward_from"] == ws.FORWARD_FROM
    # the next night: already scored, nothing written again
    detail, written, _ = _run(monkeypatch, [MARKET], rows, prices, labels)
    assert "fact_station_width_score" not in written and detail["already_scored"] == 1


def test_a_dry_run_writes_and_logs_nothing(monkeypatch):
    detail, written, logged = _run(monkeypatch, [MARKET], [], _stored(19.4, 1.5, 1.0), dry=True)
    assert written == {} and logged == []
    assert detail["scored_tonight"] == 1 and detail["summary"]["markets"] == 1
