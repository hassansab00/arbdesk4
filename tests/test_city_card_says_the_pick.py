"""Original calls and live picks have distinct labels; centres remain inputs."""
from pathlib import Path

CARD = (Path(__file__).resolve().parents[1] / "web" / "components" / "CityCards.tsx").read_text(encoding="utf-8")


def _card():
    return CARD[CARD.index("function Card("):]


def test_the_headline_is_the_pick_not_the_forecast():
    card = _card()
    assert 'FrozenCallPanel call={frozen}' in card
    head = card.index("Live forecast pick · updates as new data arrives")
    assert card.index('FrozenCallPanel call={frozen}') < head
    assert card.index('text-2xl font-semibold tabular-nums">{c.top_band}') > head
    assert "engine&rsquo;s predicted max" not in CARD, "the forecast centre is not the prediction"


def test_the_market_pick_sits_under_the_platform_pick():
    card = _card()
    assert card.index("Live forecast pick") < card.index("Market&rsquo;s pick") \
        < card.index("What the pick is built from")


def test_original_call_follows_selected_city_date_and_moment():
    assert 'selectedFrozenCall(frozenQ.data ?? [],c.city_key,c.for_date,moment)' in CARD
    assert 'selectedFrozenCall(frozenQ.data ?? [],openCard.city_key,openCard.for_date,moment)' in CARD
    assert 'frozenQ.refresh()' in CARD


def test_the_forecasts_are_labelled_inputs():
    card = _card()
    inputs = card[card.index("What the pick is built from"):]
    for label in ("Forecast centre", "Public forecasts", "Desk model"):
        assert label in inputs
    assert "A forecast, not the pick" in inputs


def test_no_red_disagreement_box_on_the_card():
    """Hassan, 27 Sep: remove the red "the platform and the market pick
    different winners" box. The market's own pick stays on the card as one
    line; the red box, its red border and the record it quoted are gone."""
    card = _card()
    assert "pick different winners" not in card
    assert "{c.disagrees && (" not in card
    assert 'c.disagrees ? "border-bad/40"' not in card
    assert "Market&rsquo;s pick" in card
