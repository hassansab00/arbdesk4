"""A desk's record is not something any button is allowed to destroy.

"i still can delte a desk" - and the standing rule on this project is "never
delte anytin".

TWO DIFFERENT THINGS WERE TRUE AND ONLY ONE OF THEM WAS THE COMPLAINT.

What the page offers is ARCHIVE. It comes off the desk list, it stops trading,
every row stays, and the section below it restores the desk. The confirm
dialog says so in those words. That is correct and it is correctly labelled.

What the API offered, with no button anywhere in the UI calling it, was
`reset_desk` -> `paper_desk_reset`, and that function did this:

    delete from paper_position_settlements where account_id = ...
    delete from paper_positions           where account_id = ...
    delete from paper_orders              where account_id = ...
    delete from paper_trade_plans         where account_id = ...

Every order the desk ever placed, every proposal it ever made, and every block
reason that explained why - gone, permanently, with no archive and no undo. On
the live desk that is 77 orders, 360 plans and the 219 venue-minimum refusals
that are the whole reason the desk looked idle.

The function's OWN COMMENT already argued against it: "the activity log is the
audit trail, and a reset is an event in the desk's history, not an erasure of
it." That was written about paper_activity, which is append-only and physically
refuses a delete, and it was false of the four tables beside it.

THE LINE IS BETWEEN A RECORD AND A DERIVED TOTAL.

    paper_orders               what the desk asked the venue for  - a record
    paper_trade_plans          what it proposed and why it stopped - a record
    paper_position_settlements a settlement with its own proof_id  - a record
    paper_positions            shares, cost basis and realised P&L per band
                               and side - a RUNNING TOTAL of the three above

A reset now stops what is live and clears only the total. That is safe
precisely BECAUSE the records survive: the aggregate can be rebuilt from them,
which was not true before, when the reset deleted its own sources.

Verified against the live schema in a rolled-back transaction: orders 2 of 2
kept, plans 2 of 2 kept, settlements 1 of 1 kept, positions 0 of 1 left, the
live order canceled, and a blocked plan still blocked with its reason intact.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKS = (ROOT / "sql" / "ad4_59_paper_desks.sql").read_text(encoding="utf-8")
ROUTE = (ROOT / "web" / "app" / "api" / "paper-desk" / "route.ts").read_text(encoding="utf-8")

# The three tables that ARE the desk's record. Nothing may delete from these.
RECORD_TABLES = ("paper_orders", "paper_trade_plans", "paper_position_settlements",
                 "paper_trades", "paper_activity")
# The one table that is a running total of them, and so may be cleared.
DERIVED_TABLES = ("paper_positions",)


def _reset_body():
    start = DESKS.index("create or replace function paper_desk_reset(")
    return DESKS[start:DESKS.index("end $ad4$;", start)]


@pytest.mark.parametrize("table", RECORD_TABLES)
def test_the_reset_does_not_delete_the_desks_record(table):
    body = _reset_body()
    assert not re.search(rf"delete\s+from\s+(public\.)?{table}\b", body, re.I), (
        f"paper_desk_reset deletes from {table}. That table is the record the desk is "
        "judged on - what it asked for, what it proposed, and why it was stopped. A "
        "reset may stop a desk; it may not erase what it did."
    )


def test_the_reset_still_clears_the_running_total():
    """The other half. A reset that left positions standing would leave the
    desk holding exposure it no longer has the cash for."""
    body = _reset_body()
    assert re.search(r"delete\s+from\s+(public\.)?paper_positions\b", body, re.I), (
        "paper_desk_reset no longer clears paper_positions, so a reset desk keeps its "
        "open shares while its cash goes back to the starting balance"
    )


def test_the_reset_stops_live_work_instead_of_deleting_it():
    body = _reset_body()
    assert re.search(r"update\s+paper_orders\b[\s\S]{0,200}?status\s*=\s*'canceled'", body, re.I), (
        "live orders are no longer canceled, so a reset desk leaves working orders that "
        "can still fill against cash it no longer has"
    )
    assert re.search(r"update\s+paper_trade_plans\b[\s\S]{0,260}?status\s*=\s*'expired'", body, re.I)


def test_a_blocked_plan_keeps_its_reason_through_a_reset():
    """The most useful row on a quiet desk is the one saying why it was quiet.
    219 of 360 plans are blocked "below the venue minimum"; a reset that swept
    them would take the explanation with it."""
    body = _reset_body()
    plans = body[body.index("update paper_trade_plans"):]
    assert "'pending_approval', 'queued'" in plans, (
        "the plan sweep no longer restricts itself to live plans, so it can overwrite a "
        "blocked plan's reason"
    )
    assert "blocked" not in plans.split("get diagnostics")[0].lower().replace(
        "a blocked plan is left exactly as it is", ""), (
        "the reset now touches blocked plans"
    )


# --- and no other route may grow one -------------------------------------

def test_every_paper_desk_command_is_one_someone_has_looked_at():
    """The enumeration. reset_desk was reachable from this map with no caller
    anywhere in the UI, so nothing in the repository was comparing what the
    API can do against what anyone intended it to do."""
    block = ROUTE[ROUTE.index("const commands:Record<string,string>={"):]
    block = block[:block.index("};")]
    commands = dict(re.findall(r"(\w+)\s*:\s*'([\w]+)'", block))
    assert commands == {
        "create_account":  "create_single_paper_account",
        "create_desk":     "paper_desk_create",
        "update_desk":     "paper_desk_update",
        "reset_desk":      "paper_desk_reset",
        "archive_desk":    "paper_desk_archive",
        "submit_order":    "submit_single_paper_order",
        "cancel_order":    "cancel_single_paper_order",
        "set_policy":      "set_single_paper_policy",
        "approve_plan":    "approve_single_paper_plan",
        "submit_exit":     "submit_single_paper_exit",
        "set_exit_policy": "set_single_paper_exit_policy",
    }, (
        f"the paper-desk API exposes {sorted(commands)}. Adding one is fine - adding one "
        "without it being listed here is how a destructive command reaches the network "
        "with nobody having read it."
    )


@pytest.mark.parametrize("word", ["delete", "drop", "purge", "destroy", "erase", "wipe"])
def test_no_paper_desk_command_is_named_for_removal(word):
    block = ROUTE[ROUTE.index("const commands:Record<string,string>={"):]
    assert word not in block[:block.index("};")].lower(), (
        f"a paper-desk command mentions {word!r}. A desk comes off the list by being "
        "archived, which is reversible and keeps every row."
    )


def test_archiving_is_reversible_and_says_so_where_it_is_offered():
    page = (ROOT / "web" / "app" / "paper-trades" / "page.tsx").read_text(encoding="utf-8")
    assert "archive_desk" in page and "p_archived:false" in page, (
        "the page can archive a desk and cannot restore one, which makes archiving a "
        "one-way door wearing a reversible name"
    )
    assert "Nothing is deleted" in page
