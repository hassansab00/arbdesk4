"""The automatic desk's fills have to reach the table the pages actually read.

paper_trades is what web/app/page.tsx, analytics/page.tsx and goals/page.tsx
select from. Until 16 Sep it was written by exactly one thing - log_paper_trade,
the approve-by-hand RPC - so the automatic desk's first four real fills produced
four positions, zero trades, and a dashboard claiming the desk had never traded.

These assert the wiring, not the intention: which tables the triggers hang off,
that the resolution close exists at all (a band held to resolution never sees a
SELL, so without it net_pnl is null for ever), and that the fee arithmetic in
the FIFO split conserves what was paid.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase" / "migrations"
             / "20260916140000_paper_trades_from_automatic_fills.sql")


def sql():
    return " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_a_fill_and_a_settlement_both_write_the_trade():
    s = sql()
    assert "after update of status on public.paper_orders" in s, (
        "nothing records a fill, so the desk's trades stay invisible")
    assert "after insert on public.paper_position_settlements" in s, (
        "a band held to resolution never sees a SELL order - without this "
        "close, net_pnl is null for ever and analytics stays empty")


def test_the_two_hardened_rpcs_are_not_rewritten():
    """complete_paper_order and settle_paper_inventory carry the cash ceiling,
    the lease check and the book-evidence freshness test. Re-deriving those to
    add a bookkeeping insert risks a desk that spends money it does not have."""
    s = sql()
    for fn in ("function public.complete_paper_order",
               "function public.settle_paper_inventory"):
        assert fn not in s, f"{fn} is redefined here; hang a trigger off it instead"


def test_a_replayed_fill_cannot_record_the_trade_twice():
    s = sql()
    assert "create unique index if not exists paper_trades_order_unique" in s
    assert "where order_id is not null" in s, (
        "rows from log_paper_trade have no order_id and must not collide")


def test_the_trade_knows_which_desk_city_and_day_it_belongs_to():
    """A closed trade has to stay readable after its band row is pruned, and a
    multi-desk platform has to be able to say whose trade it was."""
    s = sql()
    for col in ("account_id", "order_id", "city_key", "resolution_date"):
        assert f"add column if not exists {col}" in s, col


def test_closing_is_fifo_and_splits_rather_than_rounding():
    s = sql()
    assert "order by opened_at, trade_id" in s, "a close must take the oldest entry first"
    assert "take := least(remaining, t.shares)" in s
    assert "t.shares - take" in s, (
        "an exit smaller than the position must leave the remainder open at "
        "its own entry price, not discard or re-price it")


def test_the_split_divides_the_entry_fee_rather_than_duplicating_it():
    """Measured live on the Milan position: 159.47 shares carrying $0.37874 of
    entry fee, closed 100 and left 59.47, produced 0.23750 + 0.14124 = 0.37874.
    Scaling one number and leaving the other whole is the easy mistake."""
    s = sql()
    assert "coalesce(t.fee_paid,0) * (t.shares - take) / t.shares" in s
    assert "part_entry_fee := coalesce(t.fee_paid,0) * take / t.shares" in s


def test_gross_and_net_differ_by_both_fees():
    s = sql()
    assert "gross_pnl = part_notional - t.avg_fill_price * take" in s
    assert ("net_pnl = part_notional - part_fee - t.avg_fill_price * take - part_entry_fee") in s, (
        "net must subtract the exit fee AND the entry fee; gross subtracts neither")


def test_an_automatic_fill_is_not_labelled_user_approved():
    s = sql()
    assert "not coalesce(automatic, false)" in s, (
        "approved_by_user defaults to true on this table; an automatic fill "
        "nobody approved must not claim otherwise")
