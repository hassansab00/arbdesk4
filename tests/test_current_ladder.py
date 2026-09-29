"""The current prediction between pricing runs (plan v2.3 P4.9).

scripts/current_ladder.py decides which same-day city-days the hourly tick
prices again (the station has passed the current pick since it was priced, by
the card's rule on the engine's floor bucket) and shapes the row
publish_current_ladders takes. tests/database/current-ladder.cjs holds the
database side; test_tick.py the whole tick.
"""
import current_ladder as cl

# A Fahrenheit ladder the way the venue lists it: 69 or below, 70-71, 72-73, 74 or above.
BANDS = [
    {"band_id": "lo", "band_lo": None, "band_hi": 70, "open_low": True, "open_high": False},
    {"band_id": "b70", "band_lo": 70, "band_hi": 72, "open_low": False, "open_high": False},
    {"band_id": "b72", "band_lo": 72, "band_hi": 74, "open_low": False, "open_high": False},
    {"band_id": "hi", "band_lo": 74, "band_hi": None, "open_low": False, "open_high": True},
]
C_725F = (72.5 - 32) * 5 / 9          # read as 73 F: the 72-73 bucket


def test_standing_is_the_card_s_rule_on_the_engine_s_floor_bucket():
    assert cl.standing(C_725F, "F", BANDS, "b72") == 0          # the reading's own bucket
    assert cl.standing(C_725F, "F", BANDS, "hi") == 0           # above it: still open
    assert cl.standing(C_725F, "F", BANDS, "b70") == 1          # one below: only a lower venue read
    assert cl.standing(C_725F, "F", BANDS, "lo") == 2           # two below: cannot win
    assert cl.standing(None, "F", BANDS, "b70") is None
    assert cl.standing(C_725F, "F", BANDS, "not-on-the-ladder") is None
    # the venue reads whole degrees half up: 71.5 F is 72, the 72-73 bucket
    assert cl.standing((71.5 - 32) * 5 / 9, "F", BANDS, "b70") == 1
    assert cl.standing((71.4 - 32) * 5 / 9, "F", BANDS, "b70") == 0


MARKET_OF = {("nyc", "2026-09-24"): {"market_id": "m0", "unit": "F"},
             ("nyc", "2026-09-25"): {"market_id": "m1", "unit": "F"},
             ("chi", "2026-09-24"): {"market_id": "m2", "unit": "F"}}
BY_MARKET = {"m0": BANDS, "m1": BANDS, "m2": BANDS}


def pick(city, target, band, floor=None):
    return {"city_key": city, "target_date": target, "band_id": band, "observed_floor_c": floor}


def test_a_pick_the_station_passed_since_its_price_is_priced_again():
    floors = {"nyc": ("2026-09-24", C_725F)}
    assert cl.reprice_targets([pick("nyc", "2026-09-24", "b70")], floors, MARKET_OF, BY_MARKET, {}) == [
        ("nyc", "2026-09-24")]


def test_a_pick_already_priced_against_that_reading_is_not():
    floors = {"nyc": ("2026-09-24", C_725F)}
    # priced when the day already stood at 72.5 F: the pick stands where it stood
    assert cl.reprice_targets([pick("nyc", "2026-09-24", "b70", floor=C_725F)], floors,
                              MARKET_OF, BY_MARKET, {}) == []
    # still open, or no reading, or another day's reading: nothing to do
    assert cl.reprice_targets([pick("nyc", "2026-09-24", "b72")], floors, MARKET_OF, BY_MARKET, {}) == []
    assert cl.reprice_targets([pick("nyc", "2026-09-24", "b70")], {}, MARKET_OF, BY_MARKET, {}) == []
    assert cl.reprice_targets([pick("nyc", "2026-09-25", "b70")], floors, MARKET_OF, BY_MARKET, {}) == [], (
        "tomorrow's pick is not today's reading's business")


def test_a_city_day_the_tick_prices_anyway_is_not_priced_twice():
    floors = {"nyc": ("2026-09-24", C_725F)}
    assert cl.reprice_targets([pick("nyc", "2026-09-24", "b70")], floors, MARKET_OF, BY_MARKET, {},
                              skip=[("nyc", "2026-09-24")]) == []


def test_worst_first_and_bounded():
    floors = {"nyc": ("2026-09-24", C_725F), "chi": ("2026-09-24", C_725F)}
    got = cl.reprice_targets([pick("nyc", "2026-09-24", "b70"), pick("chi", "2026-09-24", "lo")],
                             floors, MARKET_OF, BY_MARKET, {})
    assert got == [("chi", "2026-09-24"), ("nyc", "2026-09-24")], "a pick that cannot win comes first"
    many = {("c%02d" % i, "2026-09-24"): {"market_id": "m%d" % i, "unit": "F"} for i in range(20)}
    got = cl.reprice_targets([pick(c, t, "lo") for c, t in many],
                             {c: (t, C_725F) for c, t in many}, many,
                             {m["market_id"]: BANDS for m in many.values()}, {})
    assert len(got) == cl.REPRICE_MAX


def _rows(probs, **extra):
    base = {"centre_c": 22.1, "sigma_c": 0.9, "observed_floor_c": C_725F,
            "computed_at": "2026-09-24T22:35:05+00:00"}
    base.update(extra)
    return [dict(base, band_id=b, calibrated_prob=p) for b, p in probs.items()]


def test_the_row_is_the_whole_ladder_with_its_time_reason_and_path():
    row, why = cl.ladder_row("nyc", "2026-09-24", "m0", _rows({"lo": 0.0, "b70": 0.100003, "b72": 0.7, "hi": 0.2}),
                             ["x", "priced_from:station_correction:v1"], "station_max", None, "git:abc")
    assert why is None
    assert row == {"city_key": "nyc", "target_date": "2026-09-24", "market_id": "m0",
                   "priced_at": "2026-09-24T22:35:05+00:00", "reason": "station_max", "checkpoint": None,
                   "engine_version": "git:abc", "priced_from": "station_correction:v1",
                   "centre_c": 22.1, "sigma_c": 0.9, "observed_floor_c": C_725F,
                   "ladder": {"lo": 0.0, "b70": 0.100003, "b72": 0.7, "hi": 0.2}}


def test_a_ladder_that_is_not_whole_is_not_published():
    rows = _rows({"b70": 0.5, "b72": 0.5})
    rows[0]["calibrated_prob"] = None
    assert cl.ladder_row("nyc", "2026-09-24", "m0", rows, [], "station_max", None, "v")[0] is None
    assert cl.ladder_row("nyc", "2026-09-24", "m0", _rows({"b70": 0.5, "b72": 0.4}), [], "station_max",
                         None, "v") == (None, "ladder sums to 0.900000")
