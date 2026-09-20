"""Several desks, each with its own settings, and a way to put one away.

Everything Hassan asked for here already existed in the database and in the
API route, and none of it reached the screen:

  paper_desk_create(name, cash, mode, parent, policy)   the form sent name and
      cash and hard-coded mode='manual' with no policy, so every desk arrived
      identical and had to be configured afterwards - the opposite of "try a
      setting without disturbing the one already running"
  paper_desk_archive(id, archived)                      no button at all
  the /api/paper-desk route                             already accepted
      create_desk, update_desk, reset_desk and archive_desk

So these tests are about the SURFACE, and about the two properties that make
the surface safe.

ARCHIVE IS NOT DELETE. A desk's trades are evidence about a strategy and
outlive any interest in the desk. paper_desk_archive sets archived_at and
force-pauses; it touches nothing else. The UI has to say so, and there has to
be a way back - the route filters archived desks out of the switcher, so
without a restore path "Archive" would be a delete wearing a softer word.

A NUMBER YOU CANNOT EXPLAIN IS A NUMBER YOU SHOULD NOT TYPE. "Minimum net edge
per share (USD)" is a label only its author could act on. Every input that
takes a decision now carries a hint, and they live in one object so their
existence can be checked rather than hoped for.

Verified against the live database on 2026-09-20, in a transaction that was
rolled back: created mode=automatic and PAUSED anyway, archived it off the
active list while keeping it in the archived view, restored it STILL PAUSED.
"""

import re

import pytest

PAGE = "web/app/paper-trades/page.tsx"
AUTOMATION = "web/components/PaperAutomation.tsx"
HINTS = "web/components/Hint.tsx"
SQL = "sql/ad4_59_paper_desks.sql"
ROUTE = "web/app/api/paper-desk/route.ts"


@pytest.fixture(scope="module")
def page():
    return open(PAGE).read()


@pytest.fixture(scope="module")
def hints():
    return open(HINTS).read()


# ---------------------------------------------------------------------------
# a new desk is a new set of SETTINGS
# ---------------------------------------------------------------------------
def test_creating_a_desk_sends_its_mode_and_its_policy(page):
    """It used to send p_mode:'manual' and no policy at all."""
    assert "p_mode:newDesk.mode" in page, "mode is hard-coded again"
    assert "p_policy:{" in page, "the create form sends no policy"
    for field in ("strategies", "cities", "max_plan_usd", "max_exposure_usd", "min_edge"):
        assert f"{field}:" in page, f"the create form does not set {field}"


def test_every_mode_the_database_accepts_is_offerable(page):
    """paper_desk_create validates mode in (manual, assisted, automatic).
    Offering fewer would make the form the limit rather than the rule."""
    sql = open(SQL).read()
    assert "p_mode not in ('manual', 'assisted', 'automatic')" in sql
    for mode in ("manual", "assisted", "automatic"):
        assert f'value="{mode}"' in page, f"{mode} cannot be chosen at creation"


def test_a_new_desk_cannot_be_offered_a_retired_city(page):
    """The scope picker on the create form is the fifth thing that decides
    'which cities' - see the retirement tests."""
    m = re.search(r"from\('cities'\)[^;]*", page)
    assert m, "the create form no longer lists cities"
    assert "'status','active'" in m.group(0)


def test_a_desk_is_created_paused_whatever_mode_is_chosen():
    """The one property that makes offering 'automatic' at creation safe. A
    desk that starts trading the moment it is named is not a thing anyone
    wants to discover afterwards."""
    sql = open(SQL).read()
    create = sql[sql.index("function paper_desk_create"):sql.index("function paper_desk_update")]
    assert "entries_paused" in create
    assert re.search(r"values\s*\(p_name[^;]*?true", create, re.S), (
        "paper_desk_create no longer forces entries_paused on insert")


# ---------------------------------------------------------------------------
# archive, and the way back
# ---------------------------------------------------------------------------
def test_archive_never_deletes():
    sql = open(SQL).read()
    fn = sql[sql.index("function paper_desk_archive"):]
    fn = fn[:fn.index("$ad4$;") + 6]
    for forbidden in ("delete from", "drop ", "truncate"):
        assert forbidden not in fn.lower(), (
            f"paper_desk_archive contains {forbidden!r} — a desk's trades are "
            f"evidence about a strategy and outlive the desk")
    assert "archived_at" in fn and "entries_paused" in fn, (
        "archiving must both hide the desk and stop it trading")


def test_the_page_has_an_archive_button_and_says_nothing_is_deleted(page):
    assert "archive_desk" in page, "no way to archive a desk from the UI"
    assert "p_archived:true" in page
    low = page.lower()
    assert "nothing is deleted" in low or "nothing deleted" in low, (
        "the button has to say what it does — 'remove' reads as 'destroy'")


def test_archiving_can_be_undone_from_the_ui(page):
    """The route filters archived desks out of the switcher, so without this
    the button is a one-way door."""
    assert "p_archived:false" in page, "no restore"
    assert "eq('archived',true)" in page or 'eq("archived",true)' in page, (
        "nothing lists the archived desks, so a restored desk could never be "
        "found to restore")


def test_the_route_still_offers_all_four_commands():
    route = open(ROUTE).read()
    for cmd in ("create_desk", "update_desk", "reset_desk", "archive_desk"):
        assert cmd in route, f"{cmd} is no longer reachable from the UI"


def test_the_switcher_leaves_archived_desks_out():
    route = open(ROUTE).read()
    assert "is('archived_at',null)" in route, (
        "an archived desk would come back into the switcher, which is the "
        "clutter archiving exists to remove")


# ---------------------------------------------------------------------------
# every input explains itself
# ---------------------------------------------------------------------------
def test_the_hints_are_all_in_one_place(hints):
    assert "PAPER_HINTS" in hints
    keys = set(re.findall(r"^  (\w+):", hints, re.M))
    for expected in ("name", "starting_cash", "mode", "max_plan_usd",
                     "max_exposure_usd", "min_edge", "strategies", "cities",
                     "paused", "band", "side", "shares", "limit", "ceiling",
                     "reason", "archive", "restore"):
        assert expected in keys, f"no hint for {expected}"


def test_a_hint_is_visible_not_just_a_title_attribute(hints):
    """A bare title= is invisible: nothing on screen says an explanation
    exists, so nobody hovers."""
    assert "aria-label" in hints and "title=" in hints
    assert "cursor-help" in hints, "the marker has to look like it does something"


def test_every_hint_actually_says_something(hints):
    """A hint that restates the label is worse than none - it costs a hover
    and teaches nothing."""
    body = hints[hints.index("PAPER_HINTS"):]
    for key, text in re.findall(r'^  (\w+):\s*\n?\s*"([^"]{0,40})', body, re.M):
        assert len(text) > 20, f"the hint for {key} is too short to explain anything"


@pytest.mark.parametrize("field", ["band", "side", "shares", "limit", "ceiling", "reason"])
def test_every_manual_ticket_input_has_a_hint(page, field):
    assert f"H.{field}" in page or "H[field]" in page, (
        f"the {field} input on the manual ticket explains nothing")


@pytest.mark.parametrize("field", ["mode", "paused", "strategies", "cities"])
def test_every_policy_input_has_a_hint(field):
    src = open(AUTOMATION).read()
    assert f"H.{field}" in src, f"the {field} control in the policy editor explains nothing"


def test_the_money_limits_are_explained_in_the_policy_editor():
    src = open(AUTOMATION).read()
    assert "H[field]" in src, (
        "max_plan_usd, max_exposure_usd and min_edge are rendered from a map; "
        "the hint has to be rendered the same way or two of the three lose it")
