"""The climb profile reads the last 30 local days (plan v2 P1.6 phase 2, step
1; 29 Sep; docs/HISTORY_WINDOWS_2026-09-29.md).

v_city_climb_profile_live used to say two years and read whatever retention
left. Scored out of sample over a year, 30 days beat 60 by 0.0085 CRPS in
every quarter and all history was worse than 60 by 0.032, so the window is
stated in the view instead of borrowed from the prune.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import archive_observations as ao  # noqa: E402

WINDOW_DAYS = 30
MIGRATION = ROOT / "supabase" / "migrations" / "20260929100000_the_climb_profile_reads_thirty_days.sql"


def _body(path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^create or replace view v_city_climb_profile_live as\n.*?^having count\(\*\) >= 20;",
                  text, re.S | re.M)
    assert m, f"no v_city_climb_profile_live in {path.name}"
    return m.group(0)


def test_the_three_definitions_are_one():
    """ad4_26 defines it, ad4_28 redefines it (and wins), the migration put it
    live. Three copies that drift would mean the install order decides what
    the trajectory prices from."""
    bodies = {p: _body(p) for p in (ROOT / "sql" / "ad4_26_temp_trend.sql",
                                    ROOT / "sql" / "ad4_28_feature_cache.sql", MIGRATION)}
    assert len(set(bodies.values())) == 1, [p.name for p in bodies]


def test_the_window_is_the_measured_one():
    body = _body(ROOT / "sql" / "ad4_28_feature_cache.sql")
    assert "2 years" not in body
    assert f"::date - {WINDOW_DAYS}\n" in body, "the local-day window is not 30 days"
    # whole days only, as scored: today, part-way through, is not one of them
    assert "::date - 1\n" in body, "the window reaches into today"
    assert "o.valid_at > now() - interval '32 days'" in body, "the index bound no longer covers the window"


def test_retention_keeps_the_window_whole():
    """The view reads the 30 whole local days before today; the oldest of
    them starts up to 14 hours before its UTC date (UTC+14), and the prune
    cuts part-way through a day. Raw readings must reach 32 days back, or the
    window's oldest day would be read from what a cut left."""
    assert ao.TABLES["observations"]["min_keep_days"] >= WINDOW_DAYS + 2


# ---------------------------------------------------------------------------
# FRESH SUPABASE, PART 2A (8 Oct): the definition that wins reads the caches.
# ad4_26 and ad4_28 run before derived_city_day_hours exists, so they keep the
# readings-only body above; ad4_weather_readers_read_the_caches.sql redefines
# it after ad4_97, as migration 20261008200000 put it live.
# ---------------------------------------------------------------------------
CACHE_MIGRATION = ROOT / "supabase" / "migrations" / "20261008200000_the_long_weather_readers_read_the_caches.sql"
CACHE_INSTALL = ROOT / "sql" / "ad4_weather_readers_read_the_caches.sql"


def _defines_the_view(path):
    return "create or replace view v_city_climb_profile_live as" in path.read_text(encoding="utf-8")


def test_the_definition_that_wins_is_the_migrations():
    order = [line.strip() for line in (ROOT / "sql" / "INSTALL_ORDER.txt").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    last = [f for f in order if _defines_the_view(ROOT / "sql" / f)][-1]
    assert last == CACHE_INSTALL.name, f"{last} defines the view after the cached one"
    assert order.index(CACHE_INSTALL.name) > order.index("ad4_97_evidence_cache.sql"), \
        "the cached definition is installed before the cache it reads"
    assert _body(CACHE_INSTALL) == _body(CACHE_MIGRATION)


def test_the_cached_definition_reads_the_same_window():
    body = _body(CACHE_MIGRATION)
    assert "2 years" not in body
    # the 30 whole local days before today, from the readings and the cache alike
    assert body.count("between f.local_today - 30") == 2, "the readings and the cache read different windows"
    assert "and f.local_today - 1\n" in body
    assert "d.obs_date between f.local_today - 30 and f.local_today - 1" in body
    # the readings for the days they hold whole, the cached hours before
    assert ">= f.first_whole" in body and "d.obs_date < f.first_whole" in body
    assert "from derived_city_day_hours d" in body
