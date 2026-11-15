"""The model audit of 15 Nov 2026 (docs/MODEL_AUDIT_2026-11-15.md): the numbers
the document, the migration and the engine quote are the committed results', and
the study's shrinkage is the station correction's own."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
OUT = os.path.join(ROOT, "data", "eval", "model_audit_2026-11-15")
DOC = open(os.path.join(ROOT, "docs", "MODEL_AUDIT_2026-11-15.md")).read()
SQL = open(os.path.join(ROOT, "supabase", "migrations", "20261115120000_same_day_station_pricing.sql")).read()
ENGINE = open(os.path.join(ROOT, "scripts", "probability_engine.py")).read()


def _served():
    return json.load(open(os.path.join(OUT, "served.json")))


def _study():
    return json.load(open(os.path.join(OUT, "station_weights_study.json")))


def _signed(x):
    return f"{x:+.4f}"


def test_the_same_day_evidence_quoted_is_the_result():
    by = _served()["same_day_path"]["served_by"]
    for cp in ("morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h"):
        g = by[f"forecast:{cp}"]
        assert g["ci90"][0] > 0, f"the corrected ladder beats the forecast path at {cp}"
        for text in (DOC, SQL, ENGINE):
            assert _signed(g["log_loss_gain_served_minus_corrected"]) in text, cp
    assert all(by[k]["ci90"][1] < 0 for k in by if k.startswith("wxpredict:")), "and loses only to wxrd3"
    m = by["forecast:morning"]
    assert (m["n"], m["dates"]) == (1866, 41)
    assert f"{_signed(m['ci90'][0])}, {_signed(m['ci90'][1])}" in SQL and "35.7% -> 40.1%" in SQL
    later = _served()["same_day_path"]["morning_later_dates"]
    assert later["dates"] == 20 and later["ci90"][0] > 0
    assert _signed(later["log_loss_gain_served_minus_corrected"]) in DOC
    assert "+0.2320" in SQL and "+0.2320" in ENGINE


def test_the_accuracy_table_is_the_result():
    by = _served()["by_checkpoint"]
    eve, morning = by["d1_eve"]["2026-10-17..2026-11-14"], by["morning"]["2026-10-17..2026-11-14"]
    assert morning["log_loss"] > eve["log_loss"] and morning["hit"] < eve["hit"], "the defect the audit found"
    for b in (eve, morning):
        assert f"{100 * b['hit']:.1f}% / {100 * b['market_hit']:.1f}%" in DOC
        assert f"{b['log_loss']:.3f} / {b['market_log_loss']:.3f}" in DOC
    assert f"morning top pick right {100 * morning['hit']:.1f}%, log loss {morning['log_loss']:.3f}" in SQL
    assert f"evening before {100 * eve['hit']:.1f}%, {eve['log_loss']:.3f}" in SQL
    for b in by.values():
        r = b["2026-10-17..2026-11-14"]
        assert r["market_hit"] > r["hit"] and r["market_log_loss"] < r["log_loss"], "the market leads everywhere"


def test_every_city_is_in_the_table():
    cities = [c for c, v in _served()["per_city"].items() if v["d1_eve"].get("n")]
    assert len(cities) == 47
    assert all(f"| {c} |" in DOC for c in cities)


def test_the_study_s_numbers_are_the_document_s():
    s = _study()
    for lead, b in s["forward"]["by_lead"].items():
        assert b["mae_gain_prod_minus_own_w"]["ci90"][0] > 0
        assert f"{b['mae_prod']:.3f} C | {b['mae_own']:.3f} | {b['mae_own_w']:.3f}" in DOC, lead
    r = s["forward"]["served_calls_replayed"]
    assert r["morning"]["combination"]["log_loss_gain_over_served"]["ci90"][0] > 0
    assert r["morning"]["own_w"]["log_loss"] < r["morning"]["combination"]["log_loss"] < r["morning"]["served"]["log_loss"]
    assert r["d1_eve"]["replay_of_served_max_log_loss_difference"] < 0.02
    lad = s["record"]["ladders_at_00"]
    assert lad["weights_ar"]["log_loss_gain_over_base"]["ci90"][0] > 0
    assert lad["market"]["log_loss"] < lad["weights_ar"]["log_loss"], "still behind the market"
    assert s["record"]["mae_c"]["base_lead2"] > s["record"]["mae_c"]["base"]


def test_the_study_shrinks_a_cell_as_the_station_correction_does():
    np = pytest.importorskip("numpy")  # noqa: F841
    import station_correction as sc
    import station_weights_study as S
    pairs, errors = [], {}
    for i in range(12):
        for city, off in (("london", 0.8), ("paris", -0.4), ("oslo", 0.1)):
            if city == "oslo" and i >= 3:
                continue                       # under MIN_N: the pool
            y, fc = 20.0 + i * 0.1, 20.0 + i * 0.1 - off - (i % 3) * 0.2
            pairs.append((city, f"2026-10-{i + 1:02d}", "m", 1, fc, y))
            errors.setdefault((city, "m"), []).append(y - fc)
    table = sc.fit(pairs)
    bias, pooled, mse, pmse = S.shrunk(errors)
    assert pooled["m"] == pytest.approx(table["pooled"][("m", 1)][0])
    for city in ("london", "paris", "oslo"):
        assert bias[(city, "m")] == pytest.approx(table["cells"][("m", 1, city)][0])
    assert bias[("oslo", "m")] == pytest.approx(pooled["m"])
    assert mse[("london", "m")] > 0 and pmse["m"] > 0


def test_audit_blocks():
    import model_audit as A
    rows = [{"hit": 1, "ll": 0.5, "hit_m": 1, "ll_m": 0.4, "city_key": "x", "target_date": "d1", "centre_c": "20.0",
             "top_prob": "0.55"},
            {"hit": 0, "ll": 1.5, "hit_m": None, "ll_m": None, "city_key": "x", "target_date": "d2", "centre_c": "",
             "top_prob": "0.35"}]
    b = A.block(rows, {("x", "d1"): 21.0})
    assert (b["n"], b["hit"], b["log_loss"], b["market_n"], b["market_hit"]) == (2, 0.5, 1.0, 1, 1.0)
    assert (b["centre_n"], b["centre_bias_c"], b["centre_mae_c"]) == (1, 1.0, 1.0)
    assert A.block([]) == {"n": 0}
    assert A.reliability(rows) == [{"from": 0.3, "n": 1, "stated": 0.35, "won": 0.0},
                                   {"from": 0.5, "n": 1, "stated": 0.55, "won": 1.0}]
