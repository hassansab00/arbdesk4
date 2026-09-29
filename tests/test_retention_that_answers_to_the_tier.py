"""Retention that answers to the tier, instead of a guess made once.

529 MB against a 500 MB free tier. The obvious diagnosis is a broken prune,
and it is wrong. Measured 2026-09-22, the archive-then-prune cycle was
working and caught up:

    beyond its keep window     eligible to prune
    book_snapshots  29,935     1,770   the rest are cited by an edge, or are
                                       the only snapshot of their band-hour
    research         4,465     4,465
    resolution       3,089     3,089
    edges            3,322     3,322

and export_cold walks the whole keyset until exhausted, so nothing is falling
behind. Everything eligible comes out on the next run - about 25 MB - and two
days later the database is back over.

THE STEADY STATE HAS NO HEADROOM. Seven datasets each hold a window chosen on
its own merits, and the sum of seven reasonable local decisions is a database
slightly larger than the plan it runs on. Nobody decided that, which is why
nobody was going to notice it either.

So each dataset declares two numbers: the window it wants, and the shortest
one it can survive on. Under the high-water mark everything gets what it
wants; over it, everything drops to its floor until the size comes back down.
Storage becomes self-correcting rather than a decision somebody has to
remember to take again in a fortnight.

NOTHING IS DELETED, under pressure or otherwise. Every dataset goes to
data/archive/<name>/ in the repository first and the prune refuses unless the
verified archive count matches. A shorter window moves the line between "in
Postgres" and "in git", and moves it back when the pressure lifts.
"""

import pathlib

import pytest

import archive_observations as arch


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase/migrations"
            / "20260922210000_retention_that_answers_to_the_tier.sql")


class _Args:
    def __init__(self, keep_days=None):
        self.keep_days = keep_days


# --------------------------------------------------------------------------
# 1. Every dataset declares a floor, and the floor is a floor.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(arch.TABLES))
def test_every_dataset_declares_how_short_it_can_go(name):
    spec = arch.TABLES[name]
    assert spec.get("min_keep_days"), (
        f"{name} has no min_keep_days, so it will never give anything back "
        "under pressure while every other dataset does"
    )


@pytest.mark.parametrize("name", sorted(arch.TABLES))
def test_a_floor_is_never_longer_than_the_window_it_floors(name):
    # NOT STRICTLY SHORTER. This asserted `<` and that is what put research
    # at 1 and resolution at 1: both windows already sit ON their prune's own
    # guard (2 and 3), so a strictly shorter floor is one the database
    # refuses - and on 23 Sep, the first run under pressure, it did. Where the
    # guard leaves room the floor must give something back; that is asserted
    # against the parsed guards in test_a_floor_the_database_refuses_is_not_a_floor.
    spec = arch.TABLES[name]
    assert 0 < spec["min_keep_days"] <= spec.get("keep_days", 90)


def test_the_observation_floor_clears_the_prunes_own_refusal():
    # prune_observations refuses outright under 32 days (plan v2 P1.6 phase 2,
    # step 5: the climb profile's 30 whole local days). A floor below that
    # would make every pressured run fail instead of freeing anything.
    assert arch.TABLES["observations"]["min_keep_days"] >= 32
    assert "p_keep_days < 32" in (ROOT / "sql/ad4_29_retention.sql").read_text()


# --------------------------------------------------------------------------
# 2. Which window a run picks.
# --------------------------------------------------------------------------

# A dataset whose floor is below its window, so the governor has a choice to
# make. It was `books` until P1.6 phase 3, step 3.2 put both at 3 (29 Sep).
FLOORED = next(n for n, s in sorted(arch.TABLES.items()) if s["min_keep_days"] < s.get("keep_days", 90))

def test_under_pressure_a_dataset_drops_to_its_floor(monkeypatch):
    monkeypatch.setattr(arch, "_rpc", lambda *a, **k: {"over": True, "pct_of_tier": 105.9,
                                                       "db_mb": 529.4, "verdict": "x"})
    days, why = arch.effective_keep_days(arch.TABLES[FLOORED])
    assert days == arch.TABLES[FLOORED]["min_keep_days"]
    assert why["over"] is True and why["source"] == "floor"


def test_with_room_to_spare_it_keeps_the_full_window(monkeypatch):
    monkeypatch.setattr(arch, "_rpc", lambda *a, **k: {"over": False, "pct_of_tier": 70.0})
    days, why = arch.effective_keep_days(arch.TABLES[FLOORED])
    assert days == arch.TABLES[FLOORED]["keep_days"]
    assert why["over"] is False


def test_an_operators_number_beats_the_governor(monkeypatch):
    monkeypatch.setattr(arch, "_rpc", lambda *a, **k: {"over": True})
    days, why = arch.effective_keep_days(arch.TABLES[FLOORED], override=21)
    assert days == 21 and why["source"] == "--keep-days"


def test_an_unreadable_pressure_keeps_history_rather_than_dropping_it(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("storage_pressure does not exist")
    monkeypatch.setattr(arch, "_rpc", boom)
    days, why = arch.effective_keep_days(arch.TABLES[FLOORED])
    assert days == arch.TABLES[FLOORED]["keep_days"], (
        "shortening retention because a health check was unreachable is how "
        "you lose history to a network blip"
    )
    assert why["over"] is None


def test_a_list_shaped_rpc_reply_is_handled(monkeypatch):
    # PostgREST returns a bare value for a scalar function and a list for some
    # shapes; guessing wrong here silently keeps the full window for ever.
    monkeypatch.setattr(arch, "_rpc", lambda *a, **k: [{"over": True, "pct_of_tier": 99.0}])
    days, _ = arch.effective_keep_days(arch.TABLES["research"])
    assert days == arch.TABLES["research"]["min_keep_days"]


def test_the_run_records_which_window_it_used_and_why():
    src = (ROOT / "scripts/archive_observations.py").read_text()
    assert 'keep_days, pressure = effective_keep_days(spec, args.keep_days)' in src
    assert src.count('"storage": pressure') >= 2, (
        "a window that changed with no note is a window nobody can explain "
        "when the chart goes sparse"
    )


# --------------------------------------------------------------------------
# 3. The governor itself.
# --------------------------------------------------------------------------

def test_the_budget_has_hysteresis():
    sql = MIG.read_text()
    assert '"high_water_pct": 92' in sql and '"target_pct": 85' in sql, (
        "one threshold makes the archiver flap between windows every run"
    )


def test_over_is_the_only_boolean_the_caller_reads():
    sql = MIG.read_text()
    assert "'over',           (v_pct >= v_high)" in sql
    src = (ROOT / "scripts/archive_observations.py").read_text()
    assert 'p.get("over")' in src
    assert "high_water_pct" not in src.split("def effective_keep_days")[1].split("def ")[0], (
        "a caller that compares the percentages itself will eventually "
        "compare them differently somewhere else"
    )


def test_the_governor_reads_and_writes_nothing_it_should_not():
    sql = MIG.read_text().lower()
    for verb in ("delete from", "drop ", "truncate"):
        assert verb not in sql
    assert "on conflict (key) do nothing" in sql, (
        "re-running must not reset a budget somebody tuned from the UI"
    )


def test_the_budget_is_editable_from_the_ui():
    assert '\'["storage_budget"]\'::jsonb' in MIG.read_text()
