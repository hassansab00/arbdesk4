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
