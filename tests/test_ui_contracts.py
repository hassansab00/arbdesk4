"""UI contracts that are pure file reads - no node, no browser, never skipped.

These exist because each one is a mistake that was actually made and reported,
some of them more than once. A comment saying "do not put signals in the rail"
is advice; a test is a rule.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def _read(rel):
    """Read a repo-relative source file. These tests read files and nothing
    else: no browser, no database, no skip marker - a UI contract that only
    holds when a server happens to be running is not a contract."""
    return open(os.path.join(ROOT, *rel.split("/"))).read()

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


# ---------------------------------------------------------------------------
# The pages added in the final round, and the two structural bugs that were
# actually found on them.
# ---------------------------------------------------------------------------

def test_analytics_has_all_four_group_headings_exactly_once():
    """The page promises "four questions, in the order a desk asks them".

    It shipped with group 3 MISSING and group 4 duplicated - so the P&L curve
    and attribution sat under the pricing heading, and "Can it take size?"
    appeared twice in a row. Both are invisible in a diff and obvious on the
    page.
    """
    src = _read("web/app/analytics/page.tsx")
    for n in (1, 2, 3, 4):
        assert src.count("n={%d}" % n) == 1, f"group {n} heading appears {src.count('n={%d}' % n)} times, expected once"


def test_analytics_sections_sit_under_the_right_question():
    """Forecast skill is a property of the FORECAST, so it belongs to group 1.

    It was under group 2 (pricing), which is exactly the "all over the place"
    complaint: the headings were right and the sections under them were not.
    """
    src = _read("web/app/analytics/page.tsx")
    g1 = src.index("n={1}")
    g2 = src.index("n={2}")
    g3 = src.index("n={3}")
    g4 = src.index("n={4}")
    assert g1 < g2 < g3 < g4, "group headings are out of order"
    assert g1 < src.index("Forecast skill by city") < g2, "forecast skill is not in group 1"
    assert g2 < src.index("Model against market") < g3, "model vs market is not in group 2"
    assert g3 < src.index("Realised P&amp;L") < g4, "the P&L curve is not in group 3"
    assert g4 < src.index("Liquidity: quoted against traded"), "liquidity is not in group 4"


def test_scatter_never_pads_a_non_negative_axis_below_zero():
    """A depth axis reading "-$1" is the chart claiming something impossible.

    The 10% padding was applied unconditionally, so a series of all-zero
    depths produced a negative tick under a chart of dollars quoted.
    """
    src = _read("web/components/charts.tsx")
    assert "ys.every((v) => v >= 0) && ylo < 0" in src
    assert "dedupeByLabel" in src, "duplicate axis labels are not deduped"


def test_new_routes_exist_and_are_in_the_nav():
    nav = _read("web/components/NavTabs.tsx")
    for route, label in [
        ("/strategies", "Strategies"),
        ("/globe", "Globe"),
        ("/databank", "Data Bank"),
    ]:
        assert os.path.exists(os.path.join(ROOT, "web", "app", route.lstrip("/"), "page.tsx")), f"{route} has no page"
        assert f'href: "{route}"' in nav, f"{route} is not in the nav"
        assert f'label: "{label}"' in nav


def test_only_s7_claims_the_peak_window():
    """s7 is the only strategy whose entry test keys on the peak window.

    Showing "ENTER NOW" over an s4 row states a property of the CITY'S DAY as
    a property of that trade. Both the pill and the Strategies page count were
    wrong this way first time round.
    """
    pill = _read("web/components/TradeTiming.tsx")
    assert 'includes("s7_pre_peak_gradient")' in pill
    board = _read("web/app/strategies/page.tsx")
    assert 's === "s7_pre_peak_gradient"' in board


def test_strategy_toggle_goes_through_the_rpc_not_a_table_write():
    """A table grant would also expose capital_cap_pct and max_concurrent."""
    src = _read("web/app/strategies/page.tsx")
    assert 'supabase.rpc("set_strategy_enabled"' in src
    assert 'from("strategies")' not in src, "the page must not write the table directly"
