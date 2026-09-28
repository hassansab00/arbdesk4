"""Plan v2.4 P3.10: the venue's record (tools/market_history.py) and the study
that judges the model against the market on it (tools/market_vs_model.py)."""
import importlib.util
import math
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mh = _tool("market_history")
mvm = _tool("market_vs_model")


def test_a_label_reads_as_the_canonical_half_open_bucket():
    # the four forms, as v_canonical_bands stores them (London and NYC, 20 Sep 2026)
    assert mh.parse_band("16°C or below") == (None, 17, True, False, "C")
    assert mh.parse_band("17°C") == (17, 18, False, False, "C")
    assert mh.parse_band("26°C or higher") == (26, None, False, True, "C")
    assert mh.parse_band("60-61°F") == (60, 62, False, False, "F")
    assert mh.parse_band("59°F or below") == (None, 60, True, False, "F")
    assert mh.parse_band("-3°C") == (-3, -2, False, False, "C")
    assert mh.parse_band("-4--3°F") == (-4, -2, False, False, "F")
    assert mh.parse_band("-5°C or below") == (None, -4, True, False, "C")
    assert mh.parse_band("Will it rain?") is None


def test_the_settlement_station_is_read_from_the_page_or_the_rules():
    assert mh.station_icao("https://www.weather.gov/wrh/timeseries?site=eham") == "EHAM"
    assert mh.station_icao("https://www.wunderground.com/history/daily/gb/london/EGLC") == "EGLC"
    assert mh.station_icao("https://www.hko.gov.hk/en/cis/dailyExtract.htm") == ""
    assert mh.station_icao("") == ""
    assert mh.station_text("highest temperature recorded by NOAA at the Ben Gurion International Airport "
                           "in degrees Celsius on 23 Mar") == "Ben Gurion International Airport"
    assert mh.station_text("the highest temperature recorded at the London City Airport Station in degrees") \
        == "London City Airport Station"
    assert mh.station_text("recorded by the Hong Kong Observatory in degrees Celsius") == "Hong Kong Observatory"
    assert mh.description_url("... available here: https://www.weather.gov/wrh/timeseries?site=LTFM. Then") \
        == "https://www.weather.gov/wrh/timeseries?site=LTFM"


def test_an_events_day_and_city_come_from_its_slug():
    assert mh.event_date("highest-temperature-in-london-on-jan-25", "2025-01-25T12:00:00Z").isoformat() == "2025-01-25"
    assert mh.event_date("highest-temperature-in-london-on-march-3-2026", "2026-03-03T12:00:00Z").isoformat() \
        == "2026-03-03"
    assert mh.event_date("arch-highest-temperature-in-paris-on-may-17-2026", "2026-05-17T12:00:00Z").isoformat() \
        == "2026-05-17"
    m = mh.SLUG_CITY.match("arch-highest-temperature-in-new-york-city-on-may-17-2026")
    assert m.group(1) == "arch-" and mh.city_key_of(m.group(2)) == "nyc"
    assert mh.city_key_of("buenos-aires") == "buenos_aires" and mh.city_key_of("atlantis") is None


def test_a_ladder_is_read_lowest_first_with_its_winner():
    def market(label, token, prices, closed=True):
        return {"groupItemTitle": label, "clobTokenIds": f'["{token}", "no"]', "outcomePrices": prices,
                "closed": closed}
    event = {"markets": [market("18°C", "t18", '["1", "0"]'), market("16°C or below", "t16", '["0", "1"]'),
                         market("19°C or higher", "t19", '["0", "1"]'), market("17°C", "t17", '["0", "1"]')]}
    bands, why = mh.ladder(event)
    assert why is None
    assert [(b[0], b[1], b[7], b[8]) for b in bands] == [
        (0, "16°C or below", "t16", 0), (1, "17°C", "t17", 0), (2, "18°C", "t18", 1), (3, "19°C or higher", "t19", 0)]
    mixed = {"markets": [market("16°C or below", "a", '["0", "1"]'), market("60-61°F", "b", '["1", "0"]')]}
    assert mh.ladder(mixed) == (None, "mixed units ['C', 'F']")
    open_ = {"markets": [market("17°C", "a", '["0.4", "0.6"]', closed=False)]}
    assert mh.ladder(open_)[0][0][8] == ""                        # unresolved: no winner recorded


def test_pooling_with_no_model_weight_is_the_market():
    mkt, mdl = [0.1, 0.6, 0.3], [0.5, 0.3, 0.2]
    assert all(abs(a - b) < 1e-12 for a, b in zip(mvm.pool(mkt, mdl, 1.0, 0.0), mkt))
    p = mvm.pool(mkt, mdl, 2.0, 0.0)                               # a > 1 sharpens the market
    assert abs(sum(p) - 1) < 1e-12 and p[1] > mkt[1]


def test_the_second_stage_recovers_the_weights_that_made_the_outcomes():
    rng = random.Random(3)
    rows = []
    for _ in range(3000):
        mkt = [rng.random() + 0.05 for _ in range(6)]
        s = sum(mkt)
        mkt = [x / s for x in mkt]
        mdl = [rng.random() + 0.05 for _ in range(6)]
        s = sum(mdl)
        mdl = [x / s for x in mdl]
        truth = mvm.pool(mkt, mdl, 1.3, 0.4)
        u, c, w = rng.random(), 0.0, 5
        for i, q in enumerate(truth):
            c += q
            if u < c:
                w = i
                break
        rows.append((mkt, mdl, w))
    a, b = mvm.fit_pool(rows)
    assert abs(a - 1.3) < 0.15 and abs(b - 0.4) < 0.15
    a_only, b_only = mvm.fit_pool(rows, with_model=False)
    assert b_only == 0.0 and mvm.A_BOUNDS[0] <= a_only <= mvm.A_BOUNDS[1]
    w = mvm.fit_anchor(rows)
    assert 0.0 < w <= 1.0                                          # the model carries information here


def test_the_summaries_mean_what_they_say():
    assert mvm.median_index([0.2, 0.2, 0.2, 0.4]) == 2
    assert mvm.median_index([0.6, 0.4]) == 0
    m, v = mvm.moments([0.0, 1.0, 0.0])
    assert m == 1.0 and v == 0.0
    assert mvm.top([0.1, 0.5, 0.4]) == 1
    assert abs(mvm.logloss([0.25, 0.75], 1) + math.log(0.75)) < 1e-12
    assert abs(mvm.brier([0.25, 0.75], 1) - (0.0625 + 0.0625)) < 1e-12
    mean, lo, hi = mvm.boot([("d1", 1.0), ("d1", 3.0), ("d2", 2.0)])
    assert mean == 2.0 and lo <= mean <= hi
    lo, hi = mvm.wilson(5, 10)
    assert lo < 0.5 < hi


def test_the_study_reads_only_committed_inputs():
    src = (ROOT / "tools" / "market_vs_model.py").read_text()
    assert "urllib" not in src and "requests" not in src and "rest(" not in src
    assert "compute_band_probabilities(mu, sigma" in src           # priced as the engine prices
    assert "sc.fit(" in src and "sc.combine(" in src and "sc.fit_width(" in src


def test_the_replay_inputs_use_the_studys_events_and_the_day_itself():
    src = (ROOT / "tools" / "p310_replay_inputs.py").read_text()
    assert "mvm.load_events(tz, cities)" in src                    # the same events as the study
    assert "dt.time(0), zone" in src and "day + dt.timedelta(days=1)" in src
    assert "w[0] <= t < w[1]" in src                               # 00:00 to 23:59 local, nothing after
    assert 'f"{eid}:{e[\'winner\']}"' in src                        # the venue's winner, nothing else


def test_the_cost_study_prices_after_the_decision_and_checks_real_asks():
    # P3.10 part 3.2: a price from before the decision leaked the reading the
    # model trades on; the corrected study prices after it and checks the
    # books' real asks on the same rows.
    t = _tool("p310_s10_after_costs")
    assert t.AFTER_S == 3600
    assert abs(t.fee(0.5) - 0.0125) < 1e-12                    # 0.05 x p x (1 - p)
    rows = [{"model_top": "e:1", "market_top": "e:2", "winner": "e:1", "model_top_prob": "0.7",
             "target_date": "2026-09-01"}]
    got = t.trades(rows, {(0, "model"): 0.40}, {}, "model", 0.05, "ask")
    assert len(got) == 1 and abs(got[0]["cost"] - (0.40 + 0.05 * 0.4 * 0.6)) < 1e-12 and got[0]["win"] == 1.0
    assert t.trades(rows, {(0, "model"): 0.66}, {}, "model", 0.05, "ask") == []     # the margin is not cleared
    src = (ROOT / "tools" / "p310_s10_after_costs.py").read_text()
    assert "prices_at(rows, after=True)" in src and "book_asks(rows)" in src


def test_a_new_maximum_is_an_event_only_when_it_enters_a_higher_bucket():
    r = _tool("p310_reaction")
    bands = [{"band_id": 0, "band_lo": None, "band_hi": 20.0}, {"band_id": 1, "band_lo": 20.0, "band_hi": 21.0},
             {"band_id": 2, "band_lo": 21.0, "band_hi": 22.0}, {"band_id": 3, "band_lo": 22.0, "band_hi": None}]
    events = {"e1": {"city": "london", "date": "2026-06-01", "unit": "C", "bands": bands, "winner": 2}}
    import datetime as dt
    def at(h, m):
        return int(dt.datetime(2026, 6, 1, h, m, tzinfo=dt.timezone.utc).timestamp())
    obs = {("london", "2026-06-01"): [(at(7, 0), 19.0), (at(10, 20), 20.2), (at(10, 50), 20.4),
                                      (at(12, 20), 21.0), (at(13, 20), 20.6), (at(15, 20), 21.4)]}
    evs, controls = r.find_events(events, {"london": "UTC"}, obs)
    # 07:00 is the first reading; 10:20 enters bucket 1; 10:50 stays in it; 12:20 enters 2; 15:20 rounds to 21: still 2
    assert [(e[3], e[4]) for e in evs] == [(at(10, 20), 1), (at(12, 20), 2)]
    assert len(controls) == 1 and all(abs(controls[0][3] - e[3]) >= r.CONTROL_GAP_MIN * 60 for e in evs)
    assert r.venue_reading(21.5, "C") == 22 and r.venue_reading(22.8, "F") == 73   # 73.04 F
    ps = [(100, 0.1), (160, 0.2), (220, 0.3)]
    assert r.first_at_or_after(ps, 161) == 0.3 and r.last_at_or_before(ps, 161) == 0.2
    assert r.first_at_or_after(ps, 221) is None


def test_the_minute_signal_enters_a_bucket_before_the_report():
    om = _tool("p310_one_minute")
    import datetime as dt
    day0 = int(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc).timestamp())
    at = lambda h, m: day0 + h * 3600 + m * 60
    # minutes: 70 F until 12:40, then 73 F; the 5-minute mean reaches 73 by the 12:45 mark
    minutes = {t: (70.0 if t < at(12, 40) else 73.0) for t in range(at(9, 0), at(15, 0), 60)}
    marks = dict(om.five_minute_marks(minutes, at(12, 30), at(12, 50)))
    assert marks[at(12, 40)] == 71 and marks[at(12, 45)] == 73          # (70*4 + 73) / 5 = 70.6 -> 71
    bands = [{"band_id": 0, "band_lo": None, "band_hi": 70.0}, {"band_id": 1, "band_lo": 70.0, "band_hi": 72.0},
             {"band_id": 2, "band_lo": 72.0, "band_hi": 74.0}, {"band_id": 3, "band_lo": 74.0, "band_hi": None}]
    events = {"e": {"city": "nyc", "date": "2026-07-01", "unit": "F", "bands": bands, "winner": 2}}
    c = lambda f: (f - 32) * 5 / 9
    obs = {("nyc", "2026-07-01"): [(at(9, 51), c(70)), (at(11, 51), c(70.5)), (at(12, 51), c(73))]}
    pre = om.find_pre_events(events, {"nyc": "UTC"}, obs, {"nyc": minutes}, {"nyc": "KLGA"})
    # the report at 12:51 enters bucket 2; the minutes did at the 12:45 mark, confirmed 6 min later
    assert [(p[3], p[4], p[5], p[6]) for p in pre] == [(at(12, 45), 2, 1, at(12, 51))]


def test_the_midnight_favourite_is_priced_after_the_decision_and_charged_its_cost():
    mf = _tool("p310_midnight_favourite")
    import datetime as dt
    ev = {"e": {"city": "london", "date": "2026-07-02", "winner": 1}}
    t = mf.decision_times(ev, {"london": "Europe/London"})
    assert t[("e", "d0_00")] == int(dt.datetime(2026, 7, 1, 23, 0, tzinfo=dt.timezone.utc).timestamp())   # BST
    assert t[("e", "d1_eve")] == t[("e", "d0_00")] - 6 * 3600
    got = mf.trade(ev["e"], 1, 0.55, {}, real_ask=0.60)
    assert abs(got["cost"] - (0.60 + 0.05 * 0.6 * 0.4)) < 1e-12 and got["win"] == 1.0
    assert mf.AFTER_MIN == 60 and "td <= t <= td + AFTER_MIN * 60" in (ROOT / "tools" /
                                                                      "p310_midnight_favourite.py").read_text()
