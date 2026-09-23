"""Four places where a transient absence was recorded as a settled fact.

Each one is the same shape: something was missing for a reason that would go
away, the code wrote down a verdict keyed to the missing thing, and the
verdict then outlived its own cause because nothing would ever ask again.

  1 weather_outcomes stamped a fingerprint of an EMPTY rules_text. sha256("")
    never moves while the column stays empty, so 233 city-days were skipped
    permanently - every one of them a market backfilled into `markets` on
    2026-09-15, already closed, with rules_fetched_at NULL because P0.5
    (Refresh Rules Text) did not exist until 09-12. weather_resolution_evidence
    has ZERO rows for 2026-09-06 to 09-11, about 3,000 unbanked
    fact_forecast_outcome rows - a fifth of the month's calibration sample.

  2 ingest_forecasts looked back ten days. A city whose window was lost
    dropped out of sight on day eleven, and four nightly runs afterwards
    reported 'ok' having written 0 rows. 13 of 48 active cities are missing
    278 city-days (1,946 rows, all seven leads each) over 2026-06-23..09-03.

  3 v_peak_hour_coverage called a city "thin" below 40 measured days while
    prune_observations keeps 90 - so a calendar month can supply at most 31
    and the bar was unreachable. 38 of 48 cities carried the word, and the
    10 that did not were rows nobody had recomputed since the prune caught up.

  4 ensemble_forecasts and regimes are empty Phase 0 tables nothing fills.
    The tempting tidy-up - adding them to the freshness spec - would turn
    "nobody has written this collector yet" into a permanent red row, because
    the spec's live branches test emptiness BEFORE fresh_hours.
"""

import datetime as dt
import pathlib
import re

import weather_outcomes as wo


ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1 - an absent input is not a verdict
# ---------------------------------------------------------------------------
def test_blank_rules_are_recognised_as_absent():
    for blank in (None, "", "   ", "\n\t "):
        assert wo.rules_are_absent(blank)
    assert not wo.rules_are_absent("The maximum temperature in degrees Celsius")


def test_an_empty_rules_text_is_not_fingerprinted():
    """The fingerprint is what makes a verdict permanent. An empty column is
    a question waiting on P0.5, not an answer."""
    src = (ROOT / "scripts/weather_outcomes.py").read_text(encoding="utf-8")
    stamp = re.search(r'\{"rules_sha256":.*?\},', src, re.S)
    assert stamp, "the fingerprint stamp is gone, not guarded"
    assert "rules_are_absent" in stamp.group(0), (
        "unsupported_source still fingerprints a blank rules_text, which is "
        "what retired 233 city-days permanently"
    )


def test_a_missing_rules_text_says_so_rather_than_being_an_absence():
    src = (ROOT / "scripts/weather_outcomes.py").read_text(encoding="utf-8")
    assert '"rules_missing": True' in src, (
        "no fingerprint is otherwise indistinguishable from a bug in the stamp"
    )


def test_real_rules_naming_an_unsupported_source_are_still_fingerprinted():
    """The bound matters. Not fingerprinting ANY unsupported_source would
    recreate the 2026-09-15 outage, where 250 of 250 city-day slots went on
    re-deciding the same unparseable markets and `captured` came back 0."""
    wu = ("The resolution source is Weather Underground for the daily "
          "maximum in degrees Celsius.")
    assert not wo.rules_are_absent(wu)
    a = wo._rules_fingerprint(wu, "C")
    assert a and a == wo._rules_fingerprint(wu, "C"), "must be stable"
    assert a != wo._rules_fingerprint(wu, "F"), "the unit is part of the input"


def test_the_fingerprint_moves_when_the_venue_republishes():
    """Which is the whole mechanism: a city-day becomes a target again on its
    own, with no code change, the moment the rules change."""
    before = wo._rules_fingerprint("no machine interface", "C")
    after = wo._rules_fingerprint("the daily maximum in degrees Celsius", "C")
    assert before != after


# ---------------------------------------------------------------------------
# 2 - a window that cannot see the hole
# ---------------------------------------------------------------------------
def test_the_forecast_catchup_window_outlives_a_lost_night():
    import os
    import importlib
    import ingest_forecasts

    src = pathlib.Path(ingest_forecasts.__file__).read_text(encoding="utf-8")
    assert 'FORECAST_CATCHUP_DAYS' in src, "the window is hard-coded again"
    m = re.search(r'FORECAST_CATCHUP_DAYS["\'],\s*["\'](\d+)["\']', src)
    assert m, "the default is not readable"
    assert int(m.group(1)) >= 30, (
        f"a {m.group(1)}-day window cannot see an eleven-day-old hole; 13 of "
        "48 cities lost 278 city-days that way"
    )


def test_the_wider_window_is_still_one_request_per_city():
    """CHUNK_DAYS is 60, so 35 days costs nothing extra on a quiet night -
    which is why this is a safe default rather than a trade."""
    import ingest_forecasts
    src = pathlib.Path(ingest_forecasts.__file__).read_text(encoding="utf-8")
    m = re.search(r"^CHUNK_DAYS\s*=\s*(\d+)", src, re.M)
    assert m and int(m.group(1)) >= 35, (
        "the catch-up window must fit in one chunk or every night pays for it"
    )
    assert len(ingest_forecasts.chunks(
        dt.date(2026, 8, 18), dt.date(2026, 9, 22), int(m.group(1)))) == 1


def test_the_two_failure_counters_are_still_separate():
    """A refusal stops the chain and an unreached chunk does not. Pooling
    them is what this widening must not quietly undo."""
    import ingest_forecasts
    src = pathlib.Path(ingest_forecasts.__file__).read_text(encoding="utf-8")
    assert "'unreached_chunks': unreached_chunks" in src
    assert "'missing_chunks': missing_chunks" in src


# ---------------------------------------------------------------------------
# 3 - a bar the retention window cannot reach
# ---------------------------------------------------------------------------
PEAK = (ROOT / "sql/ad4_37_peak_hour.sql").read_text(encoding="utf-8")


def test_the_thin_bar_is_relative_to_what_retention_can_supply():
    assert "p.n_days < 40" not in PEAK, (
        "40 days is unreachable under a 90-day prune: one calendar month can "
        "supply at most 31, so every city reads 'thin' for ever"
    )
    assert "days_available" in PEAK, "the bar must compare against the window"


def test_the_available_days_are_bounded_by_the_retention_window():
    """It is the intersection of this calendar month with the last 90 days -
    not the length of the month, which would be the same unreachable bar in
    different clothes."""
    m = re.search(r"as days_available", PEAK)
    assert m, "days_available is used but never computed"
    block = PEAK[max(0, m.start() - 700):m.end() + 100]
    assert "90 days" in block, "the bar is not bounded by the retention window"
    assert "date_trunc('month'" in block, "the bar is not bounded by the month"


def test_a_peak_computed_before_the_prune_is_labelled_a_fossil():
    """9 of 12 months in derived_weather_peak were last written on 2026-09-13
    from observations that have since been deleted. Those rows read 'solid'
    purely because nobody had recomputed them."""
    assert "not recomputed since" in PEAK
    assert "interval '35 days'" in PEAK


def test_the_assumed_case_still_comes_first():
    """No measured peak at all is the one verdict that must never be softened
    into a count of days."""
    assumed = PEAK.index("ASSUMED - no measured peak")
    thin = PEAK.index("this month can supply")
    assert assumed < thin


# ---------------------------------------------------------------------------
# 4 - an empty table is not a stale one
# ---------------------------------------------------------------------------
FRESH = (ROOT / "sql/ad4_39_freshness.sql").read_text(encoding="utf-8")


def test_the_empty_phase_zero_tables_are_not_in_the_freshness_spec():
    spec = FRESH[FRESH.index("insert into data_freshness_spec"):
                 FRESH.index("on conflict (table_name)")]
    rows = re.findall(r"^\s*\('([a-z0-9_]+)'", spec, re.M)
    assert "ensemble_forecasts" not in rows
    assert "regimes" not in rows


def test_the_exclusion_is_written_down_rather_than_merely_true():
    """Otherwise the next person tidies it up and buys a permanent red row."""
    assert "DELIBERATELY NOT HERE" in FRESH
    assert "ensemble_forecasts" in FRESH and "regimes" in FRESH


def test_the_reason_names_the_mechanism_that_would_bite():
    assert "'empty'" in FRESH and "fresh_hours" in FRESH


def test_nothing_in_the_repo_actually_reads_the_orphan_table():
    """The claim the exclusion rests on, checked rather than asserted."""
    hits = []
    for d in ("scripts", "sql", "n8n", "tools", "web/lib", "web/app", "supabase"):
        root = ROOT / d
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or ".next" in f.parts or "node_modules" in f.parts:
                continue
            if "__pycache__" in f.parts:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "ensemble_forecasts" in text:
                hits.append(str(f.relative_to(ROOT)))
    # The nightly mirror (P1.7) names it only to say it does NOT copy it.
    import mirror_to_repo
    if "ensemble_forecasts" in mirror_to_repo.NOT_MIRRORED:
        hits = [h for h in hits if h != "scripts/mirror_to_repo.py"]
    assert hits == ["sql/ad4_39_freshness.sql"], (
        f"ensemble_forecasts is referenced by {hits} - it is no longer an "
        "orphan, so the exclusion has to be revisited"
    )
