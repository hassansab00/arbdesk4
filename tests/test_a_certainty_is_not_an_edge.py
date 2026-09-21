"""A model probability of exactly 0 or 1 is a rounding artefact, not a forecast.

MEASURED ON THE LIVE DATABASE, 21 Sep, over 24 hours:

    band_probabilities      5,500 rows
      calibrated_prob = 0     827   (15.0%)

    edges built on one, tradeable, block_reason NULL:   23
      worst:  NO @ 0.727,  net edge +25.5pp,  confidence 0.038,  $59 fillable
              NO @ 0.740,  net edge +24.3pp,  confidence 0.008,  $151 fillable

Two independent things had to be true for that to reach a desk, and both were:

  1 round(p, 6) in probability_engine sends anything under 5e-7 to 0.0, and a
    band four sigma out is genuinely down there. edge_engine reads the NO side
    as `1.0 - calibrated_prob`, so the band arrives as model_prob == 1.0.

  2 edge_engine's only implausibility guard is a MAGNITUDE rule - block an edge
    over 40 points. A certainty quoted against a market at 0.727 is a 25-point
    edge, which is under the rule.

So the guard let through exactly the edges whose size made them look reasonable
and whose provenance made them worthless.
"""
import edge_engine as ee
import probability_engine as pe


def test_the_floor_is_the_same_number_on_both_sides():
    # If these ever drift, the producer can emit a value the consumer will
    # happily trade - which is the whole failure, one constant apart.
    assert ee.PROB_FLOOR == pe.PROB_FLOOR


def test_clamp_never_returns_a_certainty():
    for p in (0.0, 1.0, -1e-9, 1 + 1e-9, 3e-9, 1 - 3e-9):
        q = pe.clamp_prob(p)
        assert 0.0 < q < 1.0, f"{p} clamped to {q}"


def test_a_tail_probability_survives_rounding_to_six_places():
    # 3e-8 is a real four-sigma band, not a degenerate input. Before the clamp
    # this rounded to exactly 0.0 and its NO side became exactly 1.0.
    assert round(3e-8, 6) == 0.0                       # the artefact itself
    assert round(pe.clamp_prob(3e-8), 6) > 0.0
    assert round(1.0 - pe.clamp_prob(3e-8), 6) < 1.0


def test_clamp_leaves_ordinary_probabilities_alone():
    # This must not be a confidence gate in disguise: a band the lattice has a
    # real opinion about is untouched, to six places.
    for p in (0.001, 0.05, 0.5, 0.95, 0.999):
        assert round(pe.clamp_prob(p), 6) == round(p, 6)


def test_a_floored_probability_is_not_tradeable():
    assert ee.prob_is_at_floor(0.0)
    assert ee.prob_is_at_floor(1.0)
    assert ee.prob_is_at_floor(ee.PROB_FLOOR)
    assert ee.prob_is_at_floor(1.0 - ee.PROB_FLOOR)


def test_the_ceiling_side_is_caught_too():
    # The 23 that got through were NO-side rows at model_prob 1.0. A guard that
    # only looked at the zero end would have missed every one of them.
    assert ee.prob_is_at_floor(1.0 - 1e-9)


def test_confident_is_not_floored():
    # 0.99 is a strong opinion the model has earned and may trade on; 0.999999
    # is the storage resolution and is not.
    for p in (0.90, 0.95, 0.99, 0.999, 0.01, 0.001):
        assert not ee.prob_is_at_floor(p), p


def test_a_missing_probability_is_not_a_floored_one():
    # stale_data and prob_at_floor are different diagnoses; collapsing them
    # would hide a feed outage behind a modelling reason.
    assert not ee.prob_is_at_floor(None)
