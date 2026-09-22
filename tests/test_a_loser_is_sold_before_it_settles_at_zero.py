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
