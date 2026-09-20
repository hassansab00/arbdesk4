"""Two functions that moved the trade and not the money.

Found while covering the engine rather than the desks. Neither is called by
anything - sql/ad4_38_grants.sql says so in its own comment, "NOTHING calls
either one - not the app, not the scripts" - and both were reachable by
service_role, which the API routes and every script hold.

WHAT THEY DID.

close_position(trade_id, exit_price, reason) set closed_at, gross_pnl, net_pnl
and close_price on paper_trades, and wrote a ledger row. It never touched
paper_accounts.cash, paper_positions or paper_activity. So a trade would read
as closed with a realised P&L while the desk's balance never moved and the
position went on holding its shares - the books stop reconciling the instant it
runs, and the identity that proves it (cash = starting cash - open basis +
realised) fails by exactly the P&L it invented.

log_paper_trade(jsonb) inserted straight into paper_trades with NO account_id,
bypassing the order, the position and the cash ledger. The trade belonged to no
desk: invisible to every per-desk number, present in every global count.

WHY THEY REFUSE RATHER THAN BEING DELETED. Both signatures are load-bearing -
ad4_13_reconcile rebuilds them and then CHECKS close_position's signature
against paper_trades.trade_id's type, and ad4_38_grants names both in the array
it revokes from anon and authenticated. Removing them means editing a
schema-repair file's own self-verification. Keeping the signature and refusing
in the body removes the whole risk and costs nothing, and the refusal names the
path that does maintain the books.

THE RIGHT PATHS, which these tests also pin:
    a position closes by submit_paper_exit (a real exit order) or by venue
    settlement calling close_paper_trades
    a trade is recorded by complete_paper_order, whose trigger writes
    paper_trades with the account, the position and the cash
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RECONCILE = (ROOT / "sql" / "ad4_13_reconcile.sql").read_text(encoding="utf-8")
RPC = (ROOT / "sql" / "ad4_rpc.sql").read_text(encoding="utf-8")


def _body(sql, marker, stop):
    start = sql.index(marker)
    return sql[start:sql.index(stop, start)]


CLOSE = _body(RECONCILE, "create function close_position(", "raise notice 'reconcile: close_position")
LOG_RECONCILE = _body(RECONCILE, "create or replace function log_paper_trade(",
                      "raise notice 'reconcile: log_paper_trade")
LOG_RPC = _body(RPC, "create or replace function log_paper_trade(", "\n$$;")


BODIES = [("close_position (ad4_13)", CLOSE),
          ("log_paper_trade (ad4_13)", LOG_RECONCILE),
          ("log_paper_trade (ad4_rpc)", LOG_RPC)]
IDS = [n for n, _ in BODIES]


@pytest.mark.parametrize("name,body", BODIES, ids=IDS)
def test_it_refuses(name, body):
    assert "raise exception" in body, f"{name} no longer refuses"


BODIES = [("close_position (ad4_13)", CLOSE),
          ("log_paper_trade (ad4_13)", LOG_RECONCILE),
          ("log_paper_trade (ad4_rpc)", LOG_RPC)]
IDS = [n for n, _ in BODIES]


@pytest.mark.parametrize("name,body", BODIES, ids=IDS)
def test_it_no_longer_writes_the_trade_tables(name, body):
    """The actual defect: a write to paper_trades that no cash movement
    accompanies. A refusal that still wrote would be no refusal at all."""
    for verb in ("insert into paper_trades", "update paper_trades",
                 "insert into ledger", "update ledger"):
        assert verb not in body.lower(), (
            f"{name} still runs `{verb}` - it moves the trade without moving the money"
        )


@pytest.mark.parametrize("name,body,path", [
    ("close_position",  CLOSE,   "submit_paper_exit"),
    ("log_paper_trade", LOG_RPC, "complete_paper_order"),
], ids=["close_position", "log_paper_trade"])
def test_the_refusal_names_the_path_that_does_maintain_the_books(name, body, path):
    """A refusal that does not say what to do instead just moves the problem."""
    assert path in body, (
        f"{name} refuses without naming {path}, so whoever hits it has no way forward"
    )


def test_the_signature_check_still_has_something_to_check():
    """ad4_13 verifies close_position's first argument against
    paper_trades.trade_id's type. Keeping the signature is the whole reason
    these refuse rather than being dropped, so the check must still be here."""
    assert "check_name := 'close_position signature'" in RECONCILE
    assert "create function close_position(p_trade_id %1$s" in RECONCILE


def test_nothing_in_the_repository_calls_either_one():
    """The premise. They are safe to refuse only because nothing depends on
    them, and that has to stay true - a caller added later would now get an
    exception rather than silent corruption, but it should not be added."""
    # INVOCATIONS, not mentions. Two things share these names and neither is a
    # call: scripts/paper_engine.py defines its own Python close_position(),
    # which ad4_38_grants.sql already flags as "unrelated", and web/lib
    # described the RPC convention in a comment. Matching the bare word makes
    # this test fail on prose while still passing on a real call.
    invocation = re.compile(
        r"(?:\.rpc\(|rpc/|select\s+|perform\s+)[\"'\s]*"
        r"(?:close_position|log_paper_trade)\s*\(")
    callers = []
    roots = (list(ROOT.glob("scripts/**/*.py")) + list(ROOT.glob("tools/*.py"))
             + list(ROOT.glob("web/app/**/*.ts")) + list(ROOT.glob("web/lib/*.ts"))
             + list(ROOT.glob("supabase/functions/**/*.ts")) + list(ROOT.glob("n8n/*.json")))
    for path in roots:
        if "node_modules" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in invocation.finditer(text):
            line = text[text.rfind("\n", 0, m.start()) + 1:m.end()]
            if line.lstrip().startswith("def "):
                continue                      # paper_engine.py's own definition
            callers.append(path.relative_to(ROOT).as_posix() + ": " + line.strip()[:60])
    assert not callers, (
        f"{callers} call close_position or log_paper_trade. Neither maintains cash, "
        "positions or the activity ledger; close through submit_paper_exit and record "
        "through complete_paper_order."
    )
