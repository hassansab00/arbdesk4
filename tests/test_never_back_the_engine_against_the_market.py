"""Hassan, 24 Sep: "never favour losing bets", for every city.

Measured on settled day-ahead calls (v_city_hit_history, 13-23 Sep, head to
head): when the engine's favourite bucket and the market's differed, the
market's won C 62 of 155 and F 18 of 41, the engine's C 34 and F 8. A trade
backing the engine against the market's favourite is blocked in edge_engine
until the engine proves it wins those disagreements.
"""
import edge_engine as ee


def _record(unit, engine_won, market_won, neither):
    rows = []
    for won, lost in ((True, False),) * engine_won + ((False, True),) * market_won + ((False, False),) * neither:
        rows.append({"unit": unit, "model_call": "a", "market_call": "b", "head_to_head": True,
                     "model_hit": won, "market_hit": lost})
    return rows


def test_the_measured_record_keeps_the_gate_on_in_both_units():
    gate = ee.against_market_gate(_record("C", 34, 62, 59) + _record("F", 8, 18, 15))
    assert gate["C"]["on"] and gate["F"]["on"]
    assert (gate["C"]["days"], gate["C"]["engine_won"], gate["C"]["market_won"]) == (155, 34, 62)


def test_no_record_is_the_prior_and_the_prior_is_on():
    gate = ee.against_market_gate([])
    assert gate["C"]["on"] and gate["F"]["on"]


def test_the_gate_lifts_only_on_enough_evidence():
    # the engine winning clearly, but on too few days: still on
    assert ee.against_market_gate(_record("C", 15, 5, 5))["C"]["on"]
    # the engine winning clearly on 60 days: lifted
    assert not ee.against_market_gate(_record("C", 40, 10, 10))["C"]["on"]
    # agreeing days never count as evidence
    agree = [{"unit": "C", "model_call": "a", "market_call": "a", "head_to_head": True,
              "model_hit": True, "market_hit": True}] * 100
    assert ee.against_market_gate(agree)["C"]["days"] == 0


def _row(band, side, p, px):
    return {"band_id": band, "side": side, "model_prob": p, "market_price": px, "tradeable": True}


def test_san_francisco_24_sep_the_no_on_the_market_favourite_is_against_the_market():
    # engine 41% on 80-81F at 2c; market 30c on 74-75F
    rows = [_row("74", "YES", 0.001, 0.298), _row("74", "NO", 0.999, 0.739),
            _row("80", "YES", 0.407, 0.022), _row("80", "NO", 0.593, 0.991),
            _row("78", "YES", 0.163, 0.204), _row("78", "NO", 0.837, 0.80)]
    idx = ee.against_market_rows(rows)
    flagged = {(rows[i]["band_id"], rows[i]["side"]) for i in idx}
    assert ("74", "NO") in flagged, "NO on the market's favourite"
    assert ("80", "YES") in flagged and ("78", "YES") in flagged, "YES on buckets the market does not favour"
    assert ("74", "YES") not in flagged, "buying the market's own favourite is not against it"
    assert ("80", "NO") not in flagged and ("78", "NO") not in flagged


def test_when_the_engine_and_the_market_agree_nothing_is_against_the_market():
    rows = [_row("74", "YES", 0.5, 0.40), _row("74", "NO", 0.5, 0.62),
            _row("76", "YES", 0.3, 0.30), _row("76", "NO", 0.7, 0.72)]
    assert ee.against_market_rows(rows) == set()
