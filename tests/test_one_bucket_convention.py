"""One bucket convention everywhere (plan v2 P2.5).

Raw `bands` and `markets` hold what the collectors wrote. Before 6 Sep that
was the inclusive convention, zero-width labels and a wrong unit on a third
of the markets. The corrections live append-only in
proprietary_data_corrections, and v_canonical_bands / v_canonical_markets
apply them. Measured on the live database, 23 Sep:

    bands whose raw bounds differ from the canonical ones   9,183 of 20,161
    markets whose raw unit differs from the canonical one     667 of 1,855
    resolution dates of those rows                 2025-01-20 .. 2026-09-05

So a reader of the raw tables is right about every day since 6 Sep and wrong
about history - the kind of mistake nothing notices until a backfill or a
refit reads the past.

Reading raw `bands` or `markets` is fine for ids, tokens, dates, labels and
flags. It is wrong for the bucket: band_lo, band_hi, open_low, open_high and
markets.unit. This file makes every raw read a listed decision, and fails a
raw read that asks for the bucket.

The live catalog agreed on 23 Sep: pg_depend showed five views reading those
columns from the raw tables - v_band_price_history, v_opportunities,
v_prediction_ladder_bands, v_city_day_readiness, and v_opportunities_candidate,
which is in no file in this repository.

THE FOUR IN THE REPOSITORY ARE READ BY THE BROWSER (anon), AND THAT DECIDES IT.
v_canonical_bands and v_canonical_markets are security_invoker over
proprietary_data_corrections, which anon cannot read. security_invoker checks
the base tables as the role running the query even when the view is reached
through an owner-rights view. Switching the three owner-rights views to the
canonical ones was applied live on 23 Sep at 16:23:22Z and every anon read
of them failed with "permission denied for table proprietary_data_corrections"
until the revert at 16:26:05Z. The equivalence checks had all run as the owner.
So they stay on the raw tables, listed below, until Hassan decides how anon
may read corrected bounds. What that costs, measured: v_band_price_history
and everything on v_opportunities read today onward, where raw and canonical
agree exactly (40,781 and 880 rows, EXCEPT ALL 0 both ways);
v_prediction_ladder_bands differs on 7,340 of 17,611 rows, 21 Aug - 5 Sep.
"""

import glob
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

BUCKET = re.compile(r"\b(band_lo|band_hi|open_low|open_high|unit)\b")

# ---------------------------------------------------------------------------
# scripts/ - every PostgREST read of the raw tables
# ---------------------------------------------------------------------------
SCRIPT_RAW_READS = {
    "scripts/export_paper_trades.py": "bands: band_id -> band_label for the export",
    "scripts/paper_settlement.py": "markets: ids and dates; bands: condition and token ids "
                                   "the venue is asked about",
    "scripts/paper_worker.py": "bands: condition_id for an order",
    "scripts/verify_resolution_source.py": "markets: city and date only",
    "scripts/weather_outcomes.py": "markets: rules_text and last_seen_at, which the canonical "
                                   "view does not carry; the unit comes from v_canonical_markets",
    "scripts/databank.py": "markets: ids and dates; the bounds come from v_canonical_bands",
}

RAW_CALL = re.compile(r"rest(?:_all)?\(\s*['\"](bands|markets)['\"]", re.S)
SELECT_AFTER = re.compile(r"['\"]select['\"]\s*[:,]\s*['\"]([^'\"]*)['\"]")


def _script_raw_reads():
    out = []
    for path in sorted(glob.glob(str(ROOT / "scripts" / "**" / "*.py"), recursive=True)):
        rel = str(pathlib.Path(path).relative_to(ROOT))
        src = open(path).read()
        for m in RAW_CALL.finditer(src):
            window = src[m.end():m.end() + 400]
            sel = SELECT_AFTER.search(window)
            out.append((rel, m.group(1), sel.group(1) if sel else None,
                        src.count("\n", 0, m.start()) + 1))
    return out


def test_every_raw_read_in_scripts_is_a_listed_decision():
    unlisted = sorted({f"{rel}:{line}" for rel, _, _, line in _script_raw_reads()
                       if rel not in SCRIPT_RAW_READS})
    assert unlisted == [], (
        "these read raw bands/markets and are not listed - read v_canonical_bands / "
        "v_canonical_markets, or list the file here with what it reads: " + ", ".join(unlisted))


def test_no_script_reads_the_bucket_from_the_raw_tables():
    offenders = []
    for rel, table, select, line in _script_raw_reads():
        if select is None:
            offenders.append(f"{rel}:{line} ({table}, no select= to check)")
        elif BUCKET.search(select):
            offenders.append(f"{rel}:{line} ({table}: {select})")
    assert offenders == [], (
        "the bucket (band_lo/band_hi/open_low/open_high/unit) read from a raw table is "
        "the pre-6-Sep convention for 9,183 bands and 667 markets: " + "; ".join(offenders))


def test_the_list_has_not_gone_stale():
    reading = {rel for rel, _, _, _ in _script_raw_reads()}
    assert sorted(set(SCRIPT_RAW_READS) - reading) == []


@pytest.mark.parametrize("path,view", [
    ("scripts/databank.py", "v_canonical_bands"),
    ("scripts/paper_exits.py", "v_canonical_bands"),
    ("scripts/paper_exits.py", "v_canonical_markets"),
    ("scripts/weather_outcomes.py", "v_canonical_markets"),
])
def test_the_bucket_readers_read_the_canonical_views(path, view):
    assert f"'{view}'" in (ROOT / path).read_text() or f'"{view}"' in (ROOT / path).read_text()


# ---------------------------------------------------------------------------
# sql/ - every statement that reads the raw tables
# ---------------------------------------------------------------------------
# Statements that read raw bands/markets AND mention a bucket column, each
# with why that is not a raw bucket read. Keyed by file and the statement's
# first line.
SQL_BUCKET_STATEMENTS = {
    ("sql/ad4_31_predictive.sql", "create or replace view v_prediction_ladder as"):
        "superseded: ad4_68 (later in INSTALL_ORDER) rebuilds v_prediction_ladder on "
        "v_prediction_ladder_bands, listed below",
    ("sql/ad4_phase2.sql", "create view v_opportunities as"):
        "superseded: ad4_13_reconcile (later in INSTALL_ORDER) rebuilds v_opportunities",
    ("sql/ad4_phase2_ranking.sql", "create view v_opportunities as"):
        "superseded: ad4_13_reconcile (later in INSTALL_ORDER) rebuilds v_opportunities",
    ("sql/ad4_82_settlement_agreement.sql", "create or replace view v_settlement_agreement as"):
        "joins bands for market_id only; the bounds are v_coherent_band_outcome's",
    ("sql/ad4_diagnose.sql", "with"):
        "read-only diagnosis, not installed",
    ("sql/ad4_13_reconcile.sql", "execute $v$"):
        "v_opportunities: read by anon, see the header. Today onward only, where raw = canonical",
    ("sql/ad4_26_temp_trend.sql", "create or replace view v_band_price_history as"):
        "read by anon, see the header. Last 48 h only, where raw = canonical",
    ("sql/ad4_68_prediction_ladder_outcomes.sql", "create or replace view public.v_prediction_ladder_bands as"):
        "read by anon, see the header. Differs from canonical on 21 Aug - 5 Sep; waits on Hassan",
}

RAW_SQL = re.compile(r"\b(?:from|join)\s+(?:public\.)?(?:bands|markets)\b(?!\s*\()", re.I)
BUCKET_SQL = re.compile(r"\b(band_lo|band_hi|open_low|open_high)\b|\.unit\b", re.I)


def _sql_bucket_statements():
    out = []
    for path in sorted(glob.glob(str(ROOT / "sql" / "*.sql"))):
        rel = str(pathlib.Path(path).relative_to(ROOT))
        src = re.sub(r"--[^\n]*", "", open(path).read())
        for st in re.split(r";\s*\n", src):
            if RAW_SQL.search(st) and BUCKET_SQL.search(st):
                out.append((rel, st.strip().split("\n")[0].strip()))
    return out


def test_no_sql_statement_reads_the_bucket_from_the_raw_tables():
    unlisted = [s for s in _sql_bucket_statements() if s not in SQL_BUCKET_STATEMENTS]
    assert unlisted == [], (
        "these statements read raw bands/markets and mention a bucket column. Read "
        "v_canonical_bands / v_canonical_markets, or list the statement with why: "
        + "; ".join(f"{f}: {first}" for f, first in unlisted))


def test_the_sql_list_has_not_gone_stale():
    assert sorted(set(SQL_BUCKET_STATEMENTS) - set(_sql_bucket_statements())) == []


ANON_READ = ["sql/ad4_13_reconcile.sql", "sql/ad4_26_temp_trend.sql",
             "sql/ad4_68_prediction_ladder_outcomes.sql"]


@pytest.mark.parametrize("path", ANON_READ)
def test_an_anon_view_is_not_built_on_the_invoker_canonical_views(path):
    """The 23 Sep outage, as a rule: while v_canonical_* are security_invoker,
    a view the browser reads cannot be built on them."""
    canonical = (ROOT / "supabase/migrations/20260912230000_phase1_canonical_contracts.sql").read_text()
    assert "security_invoker" in canonical, (
        "v_canonical_* are no longer security_invoker - this rule, and the three raw "
        "views listed above, can now be revisited")
    src = re.sub(r"--[^\n]*", "", (ROOT / path).read_text())
    assert "v_canonical_bands" not in src and "v_canonical_markets" not in src, (
        f"{path} is read by anon and would fail with permission denied for "
        "proprietary_data_corrections")


def test_readiness_stays_on_the_raw_tables_on_purpose():
    """v_city_day_readiness is security_invoker and read by anon, and the
    canonical views read proprietary_data_corrections, which anon cannot and
    must not read. It covers today onward, where every correction is already
    in the past (the last corrected market is dated 5 Sep), and it carries
    its own check of the label grammar. If it ever moves to the canonical
    views, this is the test to delete - with the grant question answered."""
    src = (ROOT / "supabase/migrations/20260923010000_readiness_reads_one_row_per_band.sql").read_text()
    assert "security_invoker = true" in src
    assert "from public.bands b" in src
