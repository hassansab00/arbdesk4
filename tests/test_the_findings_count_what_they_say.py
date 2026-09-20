"""Every headline on the synthesis board is a sentence with a number in it.

Two of the eight were counting something else.

    "Across 636 cities measured the day's maximum lands at 13:59 local"
    "8,308 buckets across 51 cities price below what the desk thinks
     they are worth"

The whole `cities` table has 54 rows, 49 of them active. 636 is 53 cities x 12
months: derived_weather_peak is keyed by city AND month, and `count(*)` over it
was being read out as a city count. And `live_edges` - the finding whose
headline begins "currently" - read `from edges` with no bound of any kind, so
it counted every edge row ever computed, back to 3 September. Measured
2026-09-20 the honest figures are 53 cities and 217 buckets across 49: a 12x
and a 38x overstatement, on the two numbers an operator would quote.

NEITHER WAS A HARD BUG TO WRITE. The six findings around them all say
`count(distinct city_key)`, and the CTE one below `peaking` says it too. The
raw-table read is the same shape as the archive reading the table instead of
the view, and the reclaim cron covering three of six tables: a second way of
saying something that already had a right way, with nothing comparing them.

So this file compares them. It reads the finding CTEs out of the view and
holds every one of them to the same two rules, rather than checking the two
that were wrong.

The checks are STRUCTURAL - they read sql/ad4_40_synthesis.sql rather than
executing it - because that file lives in sql/ and the PGlite harness applies
only supabase/migrations. Both live figures were confirmed against the database
before and after the change.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNTHESIS = (ROOT / "sql" / "ad4_40_synthesis.sql").read_text(encoding="utf-8")
VIEW = SYNTHESIS[SYNTHESIS.index("create view v_synthesis_findings as"):
                 SYNTHESIS.index("comment on view v_synthesis_findings")]


def _findings():
    """key -> the CTE body that produces it."""
    out = {}
    for m in re.finditer(r"^(\w+) as \(\n(.*?)^\),?\n", VIEW, re.S | re.M):
        key = re.search(r"'([\w_]+)'\s+as key", m.group(2))
        if key:
            out[key.group(1)] = m.group(2)
    return out


FINDINGS = _findings()


def test_all_eight_findings_are_found():
    """A regex that stopped matching would make every test below vacuous."""
    assert len(FINDINGS) == 8, f"found {len(FINDINGS)}: {sorted(FINDINGS)}"
    assert "peak_hour" in FINDINGS and "live_edges" in FINDINGS


@pytest.mark.parametrize("key", sorted(FINDINGS))
def test_the_city_count_counts_distinct_cities(key):
    """`count(*)` over a table keyed by city AND something else is how "across
    636 cities" reached the page."""
    line = re.search(r"^\s*(.+?)\s+as n_cities", FINDINGS[key], re.M)
    assert line, f"{key} produces no n_cities at all"
    expr = line.group(1).strip()
    assert re.fullmatch(r"count\(distinct \w*\.?city_key\)::int", expr), (
        f"{key} counts cities as {expr!r}. It has to be a distinct count of city_key - "
        "anything else counts the rows of whatever table it reads, and those are keyed "
        "by city and month, or city and date, or band."
    )


def test_the_live_finding_reads_the_latest_edge_not_the_whole_history():
    """`edges` is an append-only log: one row per band, per side, per run. The
    current state of it is v_latest_edge, and three intraday runs inside one
    window would otherwise count the same bucket three times."""
    cte = FINDINGS["live_edges"]
    assert "from v_latest_edge" in cte, (
        "live_edges reads the raw edges table again. That is every edge ever computed, "
        "which is what made the board claim 8,308 live buckets against 217."
    )
    assert not re.search(r"from\s+(public\.)?edges\b", cte)


def test_the_live_finding_is_bounded_to_the_same_twelve_hours_as_the_rest():
    """Not a new threshold. phase1c_operational_readiness and
    phase1d_execution_readiness already call an edge fresh for twelve hours,
    and a second answer to "is this edge still live" is how the two would
    drift."""
    assert re.search(r"computed_at\s*>=\s*now\(\)\s*-\s*interval\s*'12 hours'",
                     FINDINGS["live_edges"]), (
        "live_edges has no freshness bound, so a retired city's last edge never expires "
        "and the headline's first word stops being true"
    )
    others = (ROOT / "supabase" / "migrations").glob("*readiness.sql")
    windows = {m for p in others
               for m in re.findall(r"computed_at >= now\(\) - interval '([^']+)'",
                                   p.read_text(encoding="utf-8"))}
    assert windows == {"12 hours"}, (
        f"the readiness views now call an edge fresh for {sorted(windows)}; live_edges "
        "still says 12 hours, so the board and the readiness panel disagree"
    )


def test_the_peak_average_is_weighted_by_the_days_it_claims_to_use():
    """The sentence offers "from 17,952 days of readings" as its basis, so the
    number in front of it is the average over those days - not over the 636
    month-buckets they happen to fall into."""
    assert re.search(r"sum\(peak_hour_local \* n_days\)\s*/\s*nullif\(sum\(n_days\), 0\)",
                     FINDINGS["peak_hour"]), (
        "peak_hour averages the month-buckets rather than the days, while quoting the "
        "day count as its evidence"
    )
