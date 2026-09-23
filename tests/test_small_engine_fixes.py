"""Plan v2 P3.7: the small engine fixes."""
import probability_engine as pe
import forecast_postprocess as fp


def test_the_engine_floors_a_postprocessed_width_like_the_fit_does():
    assert pe.POSTPROCESS_SIGMA_FLOOR_C == fp.SIGMA_FLOOR_C
    src = open(pe.__file__).read()
    assert 'sigma = max(POSTPROCESS_SIGMA_FLOOR_C, sigma_historical * float(pp["sigma_ratio"]))' in src


PUBLIC = {"model": "open_meteo_forecast", "run_at": "2026-09-23T06:00Z", "forecast_max_c": 24.0}
MROW = {"model_version": "abc123", "run_at": "2026-09-23T05:00Z", "predicted_max_c": 23.1}


def test_a_model_priced_centre_is_filed_under_the_model():
    label, cfg = pe.forecast_provenance(True, MROW, PUBLIC)
    assert label.startswith("model:abc123") and cfg["model"] == "arbdesk_weather_model"


def test_the_trajectory_on_a_promoted_model_names_both():
    """The mislabel P3.7 fixes: the centre moved off the model's number, so
    the old equality test filed this under the public forecast."""
    label, cfg = pe.forecast_provenance(True, MROW, PUBLIC, {"local_hour": 15})
    assert label.startswith("trajectory:15h:model:abc123")
    assert cfg["centre"] == "trajectory" and cfg["model"] == "arbdesk_weather_model"


def test_the_public_forecast_stays_the_public_forecast():
    label, cfg = pe.forecast_provenance(False, MROW, PUBLIC)
    assert label == "open_meteo_forecast:2026-09-23T06:00Z"
    label, _ = pe.forecast_provenance(False, None, PUBLIC, {"local_hour": 9})
    assert label == "trajectory:09h:open_meteo_forecast:2026-09-23T06:00Z"


def test_provenance_is_no_longer_decided_by_comparing_floats():
    src = open(pe.__file__).read()
    assert 'centre_corrected == float(mrow["predicted_max_c"])' not in src
