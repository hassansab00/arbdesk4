import settlement as st


def bands_fixture():
    return [
        {"band_id": "tail_low", "band_lo": None, "band_hi": 20, "open_low": True, "open_high": False},
        {"band_id": "b20", "band_lo": 20, "band_hi": 21, "open_low": False, "open_high": False},
        {"band_id": "b21", "band_lo": 21, "band_hi": 22, "open_low": False, "open_high": False},
        {"band_id": "tail_high", "band_lo": 22, "band_hi": None, "open_low": False, "open_high": True},
    ]


def test_find_winning_band_closed_band_celsius():
    assert st.find_winning_band(bands_fixture(), settled_value=20.4, unit="C") == "b20"


def test_find_winning_band_open_low_tail():
    assert st.find_winning_band(bands_fixture(), settled_value=5.0, unit="C") == "tail_low"


def test_find_winning_band_open_high_tail():
    assert st.find_winning_band(bands_fixture(), settled_value=40.0, unit="C") == "tail_high"


def test_find_winning_band_fahrenheit_conversion():
    # 20C = 68F; bands are still expressed in C in this fixture, so use a
    # unit="F" fixture to prove the conversion path actually runs.
    f_bands = [
        {"band_id": "f68", "band_lo": 68, "band_hi": 70, "open_low": False, "open_high": False},
        {"band_id": "f70", "band_lo": 70, "band_hi": 72, "open_low": False, "open_high": False},
    ]
    # settled_value is stored in Celsius (20C = 68F) -> should land in f68
    assert st.find_winning_band(f_bands, settled_value=20.0, unit="F") == "f68"


def test_find_winning_band_no_match_returns_none():
    bands = [{"band_id": "b1", "band_lo": 20, "band_hi": 21, "open_low": False, "open_high": False}]
    assert st.find_winning_band(bands, settled_value=99.0, unit="C") is None
