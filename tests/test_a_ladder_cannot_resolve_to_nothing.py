"""Eleven bands, none of them won, on 410 market-days.

A daily-high ladder is mutually exclusive AND exhaustive - the open tails mean
every possible maximum lands in exactly one band. So a market-day whose every
band is settled_yes = false is not an outcome. It is a gap wearing an
outcome's clothes.

Measured 2026-09-22 on fact_band_outcome:

    market-days with exactly one winner      696   avg 10.96 bands frozen
    market-days with NO winner recorded      410   avg 10.49 bands frozen
    market-days with two or more winners       0

Every one of the 410 is resolution_date 2026-08-26 to 09-05, frozen between
09-04 and 09-08. The half-band gap in those averages is the winner, missing.

HOW. bank_bands froze each band the moment the venue confirmed it, and
common.upsert() is ignore-duplicates, so whatever landed first is the record
for ever. The venue confirms a band that CANNOT win before it confirms the one
that did - a band strictly below the running maximum is decidable hours before
the day ends. A run landing in that gap froze the losers, marked them done,
and never came back. Same family as derived_model_promotion freezing at its
first write, in a different table.

WHAT IT COST. 44% of the rows behind the calibration haircut - the thing that
adjusts every price the desk shows - came from ladders that never resolved.
That is not a neutral sample: every band is a loss, so it drags the fitted map
toward "the model is far too confident" for a reason that has nothing to do
with the model. It did NOT corrupt the strategy verdicts; zero settled signals
fall on such a day, which was checked before this was written.

AND IT IS NOT COMPUTED BACK. The desk settles on the VENUE's record, never on
its own observed maximum - that separation is the whole point of
v_venue_band_resolution, and the 410 days' venue rows have since been pruned.
Filling them in from observed_max_c would be inventing settlement evidence and
calling it history. They are named instead.
"""

import pathlib
import re

import databank


ROOT = pathlib.Path(__file__).resolve().parents[1]
MARKET = "30000000-0000-0000-0000-000000000001"


def ladder(n, winner_index, confirmed=None, market=MARKET):
    """A ladder of n bands; winner_index < 0 means nobody won."""
    bands, res = [], []
    for i in range(n):
        bid = f"20000000-0000-0000-0000-0000000000{i:02d}"
        bands.append({"band_id": bid, "market_id": market, "band_lo": 20 + i,
                      "band_hi": 21 + i, "open_low": False, "open_high": False})
        res.append({"band_id": bid, "market_id": market,
                    "settled_yes": (i == winner_index),
                    "resolution_state": (confirmed[i] if confirmed else "confirmed"),
                    "confirmed_at": "now"})
    return bands, res


def run(monkeypatch, bands, res, frozen=(), market_state="confirmed"):
    def rows(path, params=None):
        if path == "fact_band_outcome":
            return [{"band_id": b} for b in frozen]
        if path == "markets":
            return [{"market_id": MARKET, "city_key": "london",
                     "resolution_date": "2026-09-12"}]
        if path == "v_venue_market_resolution":
            return [{"market_id": MARKET, "resolution_state": market_state}]
        if path == "v_venue_band_resolution":
            return res
        if path == "bands":
            return bands
        if path == "band_probabilities":
            return [{"band_id": b["band_id"], "raw_prob": 0.09,
                     "calibrated_prob": None, "computed_at": "now"} for b in bands]
        if path in ("v_opportunities", "v_latest_edge"):
            return []
        raise AssertionError(path)

    monkeypatch.setattr(databank, "rest", rows)
    monkeypatch.setattr(databank, "rest_all",
                        lambda path, params=None, **kw: rows(path, params))
    return databank.bank_bands({}, 7, False)


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def test_a_ladder_with_no_winner_is_not_frozen(monkeypatch):
    """The regression itself: eleven confirmed bands, none of them the winner.
    That is the venue not finished, not a day on which nothing won."""
    bands, res = ladder(11, winner_index=-1)
    assert run(monkeypatch, bands, res) == []


def test_a_ladder_with_one_winner_is_frozen(monkeypatch):
    bands, res = ladder(11, winner_index=4)
    out = run(monkeypatch, bands, res)
    assert len(out) == 11
    assert sum(1 for r in out if r["settled_yes"]) == 1


def test_a_ladder_with_two_winners_is_not_frozen(monkeypatch):
    """Two bands cannot both contain one maximum. Never seen live - 0 of
    1,106 - and it must stay impossible to bank rather than impossible to
    notice."""
    bands, res = ladder(11, winner_index=4)
    res[7]["settled_yes"] = True
    assert run(monkeypatch, bands, res) == []


def test_an_unconfirmed_band_holds_the_whole_ladder(monkeypatch):
    """This is the precise shape of the bug. Ten losers confirmed, the winner
    still pending: freezing the ten is what made the day permanent."""
    states = ["confirmed"] * 11
    states[4] = "pending"
    bands, res = ladder(11, winner_index=4, confirmed=states)
    assert run(monkeypatch, bands, res) == [], (
        "the confirmed losers were banked while the winner was still pending, "
        "and upsert is ignore-duplicates, so the day is wrong for ever"
    )


def test_the_ladder_is_judged_whole_including_bands_already_banked(monkeypatch):
    """`done` must be applied AFTER coherence. Judging only the unbanked
    remainder would see a one-band ladder with one winner and wave it
    through - which is how a half-frozen day finishes itself wrongly."""
    bands, res = ladder(11, winner_index=4)
    already = [b["band_id"] for b in bands if b["band_id"] !=
               "20000000-0000-0000-0000-000000000004"]
    out = run(monkeypatch, bands, res, frozen=already)
    assert [r["band_id"] for r in out] == ["20000000-0000-0000-0000-000000000004"]


def test_a_day_whose_winner_was_banked_without_its_losers_still_completes(monkeypatch):
    bands, res = ladder(11, winner_index=4)
    out = run(monkeypatch, bands, res,
              frozen=["20000000-0000-0000-0000-000000000004"])
    assert len(out) == 10 and not any(r["settled_yes"] for r in out)


def test_an_unconfirmed_market_is_still_refused(monkeypatch):
    """The pre-existing gate, kept: coherence is an extra condition, not a
    replacement for the venue having finished with the market."""
    bands, res = ladder(11, winner_index=4)
    assert run(monkeypatch, bands, res, market_state="partial") == []


def test_the_skipped_days_are_named_not_silent(monkeypatch, capsys):
    bands, res = ladder(11, winner_index=-1)
    run(monkeypatch, bands, res)
    out = capsys.readouterr().out
    assert "not frozen" in out and "london" in out and "0 winners" in out


# ---------------------------------------------------------------------------
# the views over what is already frozen
# ---------------------------------------------------------------------------
MIG = (ROOT / "supabase/migrations"
       / "20260922120000_a_ladder_cannot_resolve_to_nothing.sql").read_text(encoding="utf-8")


def test_coherence_is_asked_of_the_ladder_not_the_row():
    """v_verified_fact_band_outcome checked each band against the venue row by
    row, so eleven agreeing falses passed it one at a time. Coherence is a
    property of the market-day."""
    assert "group by f.city_key, f.for_date" in MIG
    assert "count(*) filter (where f.settled_yes) = 1" in MIG


def test_a_day_with_no_winner_is_named_rather_than_averaged_in():
    assert "NO WINNER RECORDED" in MIG
    assert "v_coherent_band_outcome" in MIG


def test_the_verified_view_now_requires_a_resolved_ladder():
    """Both checks are needed and both must stay: the row-by-row one cannot
    see an empty ladder, and the ladder one cannot see a band the venue
    disagrees with."""
    view = MIG[MIG.index("create or replace view public.v_verified_fact_band_outcome"):]
    assert "c.coherent" in view
    assert "f.settled_yes is not distinct from br.settled_yes" in view
    assert "br.resolution_state = 'confirmed'" in view


def test_nothing_recomputes_a_winner_from_the_observed_maximum():
    """The separation the desk settles on. Recovering these days from
    observed_max_c would be inventing settlement evidence and calling it
    history."""
    assert "observed_max_c" not in MIG.split("-- ===")[-1], (
        "the migration body must not derive a winner from the weather"
    )
    for forbidden in ("update public.fact_band_outcome", "delete from public.fact_band_outcome"):
        assert forbidden not in MIG.lower(), "fact tables are append-only"


def test_the_views_are_readable_by_the_app():
    assert re.search(r"grant select on public\.v_band_outcome_coherence", MIG)
    assert re.search(r"grant select on public\.v_coherent_band_outcome", MIG)
