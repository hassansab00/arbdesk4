"""UI contracts that are pure file reads - no node, no browser, never skipped.

These exist because each one is a mistake that was actually made and reported,
some of them more than once. A comment saying "do not put signals in the rail"
is advice; a test is a rule.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

# --------------------------------------------------------------------------
# The right-hand rail is a CITY MONITOR, not signals, and never an overlay.
#
# Signals sat there through six versions and was wrong in that slot every
# time: a queue you work through is not a thing you glance at, and with every
# strategy shipping disabled it held one sentence about being switched off,
# permanently, on every page. The sixth version also overlaid the page, which
# nobody asked for - a side panel that covers the thing you opened it beside
# is worse than no panel.
# --------------------------------------------------------------------------
def test_the_rail_does_not_render_signals():
    src = open(os.path.join(WEB, "components", "RightRail.tsx")).read()
    assert "SignalsPanel" not in src, "signals belong on a page, not in the glance column"


def test_the_rail_never_overlays_the_page():
    src = open(os.path.join(WEB, "components", "RightRail.tsx")).read()
    for banned in ("fixed inset-0", 'role="dialog"', "z-40", "z-50"):
        assert banned not in src, f"the rail must dock, not float ({banned})"
    assert "shrink-0" in src and "<aside" in src, "it has to be a real column"


def test_the_rail_shows_movement_events_and_freshness():
    src = open(os.path.join(WEB, "components", "RightRail.tsx")).read()
    assert "live_weather" in src
    assert "v_city_peak_approach" in src, "which way it is moving and how fast"
    assert "weather_events" in src, "what fired"
    assert "fmtAge" in src, "how stale"


def test_signals_still_has_a_home():
    """Removing it from the rail must not remove approve/dismiss from the app."""
    src = open(os.path.join(WEB, "app", "page.tsx")).read()
    assert "SignalsPanel" in src


# --------------------------------------------------------------------------
# A missing relation names the file that CREATES it.
#
# Every Predictive panel failed with "run sql/ad4_00_preflight.sql", which is
# right about one file in thirty. The views it wanted come from ad4_31.
# --------------------------------------------------------------------------
def test_the_missing_relation_message_is_derived_not_hardcoded():
    src = open(os.path.join(WEB, "components", "DataState.tsx")).read()
    assert "SQL_OWNER" in src
    # only the executable text - the comment explains the bug by name
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
    assert "ad4_00_preflight" not in code, "it must not name one file for every relation"


def test_the_sql_owner_map_is_current():
    """Generated from sql/*.sql. If this fails, run tools/gen_sql_owner.py."""
    import importlib.util

    root = os.path.dirname(WEB)
    spec = importlib.util.spec_from_file_location(
        "gen_sql_owner", os.path.join(root, "tools", "gen_sql_owner.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    on_disk = open(os.path.join(WEB, "lib", "sqlOwner.ts")).read()
    assert on_disk == mod.render(mod.build()), \
        "web/lib/sqlOwner.ts is stale - run python3 tools/gen_sql_owner.py"


def test_every_predictive_view_maps_to_ad4_31():
    import json as _json
    src = open(os.path.join(WEB, "lib", "sqlOwner.ts")).read()
    owner = _json.loads(src[src.index("{"):].rstrip().rstrip(";"))
    for v in ("v_forecast_convergence", "v_prediction_ladder", "v_prediction_scorecard",
              "v_bankroll_curve", "v_edge_scaling"):
        assert owner.get(v) == "ad4_31_predictive.sql", (v, owner.get(v))
