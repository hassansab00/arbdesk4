"""The paper desk page has to answer three questions without a SQL client.

On 16 Sep the automatic chain had been running for hours - firing 90 to 111
signals a cycle and producing zero proposals - and nothing in the platform said
so. Finding out took a hand-written query against ingest_log. That is not a
thing a desk owner should have to do to learn whether their desk is alive.

  what is it doing      PaperPipelineStatus, one tile per step
  what has it traded    PaperTradeHistory, repo archive merged with Postgres
  did it make money     the outcome tiles above that table

These assert the contracts that are easy to break silently: that the two data
sources cannot double count, that a zero which SUCCEEDED is still flagged, and
that an empty desk does not get reported as a losing one.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "web" / "components" / "PaperPipelineStatus.tsx"
HISTORY = ROOT / "web" / "components" / "PaperTradeHistory.tsx"
PAGE = ROOT / "web" / "app" / "paper-trades" / "page.tsx"


def test_both_components_are_actually_on_the_page():
    """A component nobody renders is the same as no component."""
    src = PAGE.read_text(encoding="utf-8")
    assert "<PaperPipelineStatus" in src
    assert "<PaperTradeHistory" in src
    assert "'Trades'," in src, "Trades must be a tab, and the first one"
    assert "useState('Trades')" in src, (
        "the page is called Paper Trades; it should open on the trades")


def test_the_status_block_covers_every_step_of_the_chain():
    """A chain is only as diagnosable as its least visible link."""
    src = STATUS.read_text(encoding="utf-8")
    for job in ("signal_engine", "paper_plans", "paper_worker",
                "paper_settlement", "paper_exits"):
        assert f'"{job}"' in src, f"{job} is in the pipeline but not on the page"


def test_a_step_that_succeeded_and_did_nothing_is_not_shown_as_healthy():
    """This is how the chain actually fails: every step returns ok and the
    desk trades nothing. Green tiles all the way down would be a lie."""
    src = STATUS.read_text(encoding="utf-8")
    assert "idle ? \"text-warn\"" in src or 'idle ? "text-warn"' in src, (
        "a successful run that produced zero must not be coloured as good")
    assert "ran and produced nothing" in src, (
        "the first idle step should explain itself in words, not leave five "
        "amber tiles to be interpreted")


def test_the_page_does_not_promise_a_schedule_github_does_not_keep():
    """GitHub delays cron by thirty minutes to three hours under load. A bare
    'next run 16:15' would have the owner refreshing at 16:16."""
    src = STATUS.read_text(encoding="utf-8")
    assert "often late" in src


def test_only_the_step_the_page_can_really_trigger_offers_a_button():
    """Signals, proposals, settlement and exits run in GitHub Actions and the
    browser cannot start them. A button that quietly did nothing would be worse
    than no button."""
    src = STATUS.read_text(encoding="utf-8")
    assert "runPaperWorker" in src
    assert "The only step this page can trigger" in src


def test_the_archive_and_the_database_cannot_double_count():
    """A trade that closed this morning is in Postgres and not yet in the
    repository export. Summing both would report it twice."""
    src = HISTORY.read_text(encoding="utf-8")
    assert "new Map<string, Trade>()" in src
    assert "byId.set(t.trade_id, t)" in src
    # live first, archive second, so the archive overwrites
    live = src.index("for (const t of live)")
    archived = src.index("for (const t of archived)")
    assert live < archived, (
        "the archive must be applied last so it wins; a closed trade never "
        "changes, so preferring it costs nothing and matches what a reviewer "
        "reading the repo sees")


def test_an_empty_desk_is_not_reported_as_a_losing_one():
    """A win rate of 0% reads as 'it loses every time'. That is a different
    statement from 'it has not finished a trade yet'."""
    src = HISTORY.read_text(encoding="utf-8")
    assert "closed.length ? wins / closed.length : undefined" in src
    assert 'value={s.winRate === undefined ? "—"' in src


def test_the_trade_row_carries_what_was_asked_for():
    """Strategy, city, contract and dates - the four things you need to tell
    one trade from another."""
    src = HISTORY.read_text(encoding="utf-8")
    for column in ("City", "Contract", "Resolves", "Strategy", "Entry", "Exit", "Net"):
        assert f'"{column}"' in src, f"the trades table has no {column} column"


def test_a_partial_fill_says_so():
    src = HISTORY.read_text(encoding="utf-8")
    assert "partial_fill &&" in src


def test_gross_and_net_are_both_shown_and_explained():
    """The gap between them is what the venue took, and it is the number a
    paper desk most often forgets to subtract."""
    src = HISTORY.read_text(encoding="utf-8")
    assert "Gross P&L" in src and "Net P&L" in src
    assert "what the venue took" in src


def test_the_archive_is_fetched_rather_than_imported():
    """Importing the jsonl would bake it into the bundle at build time, so a
    page left open would never show a newly exported trade."""
    src = HISTORY.read_text(encoding="utf-8")
    assert '"/paper-trades/index.json"' in src
    assert "import" not in src.split("async function fetchArchive")[1].split("}")[0]


def test_a_missing_archive_is_not_an_error():
    """Before the first export there is no index.json, and that is the normal
    state of a new desk rather than a broken page."""
    src = HISTORY.read_text(encoding="utf-8")
    assert "if (!index.ok) return []" in src


# ---------------------------------------------------------------------------
# IS IT ON, AND HOW DO I TURN IT OFF
#
# The switch existed: a checkbox reading "Pause new automatic entries", inside
# a policy form, on a tab, behind a Save button. Stopping the desk meant pick
# desk, click Automation, scroll, tick, save - four steps and a guess about
# which of them actually stopped it - while nothing on the page said whether it
# was running in the first place.

CONTROL = ROOT / "web" / "components" / "PaperDeskControl.tsx"
RUN_ROUTE = ROOT / "web" / "app" / "api" / "paper-run" / "route.ts"


def test_the_control_panel_is_the_first_thing_under_the_desk_picker():
    src = PAGE.read_text(encoding="utf-8")
    assert "<PaperDeskControl" in src
    assert src.index("<PaperDeskControl") < src.index("<PaperPipelineStatus"), (
        "status and the switch come before the telemetry")
    assert src.index("<PaperDeskControl") < src.index("Manual paper ticket")


def test_every_way_a_desk_can_be_idle_has_a_state():
    """Four states, and the fourth is the point."""
    src = CONTROL.read_text(encoding="utf-8")
    for state in ("ACTIVE", "PAUSED", "MANUAL", "STALLED"):
        assert f'key: "{state}"' in src, f"{state} is not a state the page can show"


def test_a_desk_that_is_on_but_cannot_trade_is_not_shown_as_running():
    """A desk with no strategies, no cities or no spare cash looks exactly like
    a working desk that has not found a trade yet, and will sit there for ever.
    That is the state the old page could not express."""
    src = CONTROL.read_text(encoding="utf-8")
    assert "No strategies chosen" in src
    assert "No cities chosen" in src
    assert "No available cash" in src
    # ...and each names what to do about it rather than only what is wrong.
    assert src.count("in Settings") >= 2


def test_stopping_is_one_click_and_rewrites_nothing_else():
    """set_policy takes mode and policy as well as the pause flag, so the
    switch has to send them back untouched or flipping it quietly saves
    whatever the form last held."""
    src = CONTROL.read_text(encoding="utf-8")
    assert "p_mode: desk.mode" in src and "p_policy: desk.policy" in src
    assert "setPaused(true)" in src and "setPaused(false)" in src


def test_the_run_button_is_never_disabled_and_explains_itself_in_place():
    """It was gated on a server check that came back "not configured" three
    times for three different reasons, and the only way to see why was to hover
    a tooltip on a deployment nobody but its owner can reach. A control that
    refuses to explain itself in place is worse than one that fails loudly."""
    src = CONTROL.read_text(encoding="utf-8")
    assert 'disabled={!!busy}\n                title="Starts the full chain' in src, (
        "the run button must not be gated on the configuration check")
    assert "Run cycle:{\" \"}" in src, "the wiring state must be on the page, not in a tooltip"
    assert "canRun.missing?.join" in src


def test_the_narrow_action_stays_narrow():
    """Fill queued is not the same as running the cycle, and the panel has to
    keep saying which is which - one looks for new trades, the other only
    settles what is already queued."""
    src = CONTROL.read_text(encoding="utf-8")
    assert "runPaperWorker" in src
    assert "Does not look for new trades" in src


def test_the_dispatch_route_takes_no_parameters_from_the_browser():
    """The workflow, the ref and the repository are fixed on the server, so
    there is nothing for a caller to inject."""
    src = RUN_ROUTE.read_text(encoding="utf-8")
    assert "const WORKFLOW = 'pipeline_intraday.yml'" in src
    assert "request.json()" not in src, "the run route must not read a body"
    assert "process.env.GITHUB_DISPATCH_TOKEN" in src
    assert "sameOrigin" in src or "origin !== new URL(request.url).origin" in src


def test_an_unconfigured_dispatch_names_the_variable_to_set():
    src = RUN_ROUTE.read_text(encoding="utf-8")
    assert "GITHUB_DISPATCH_TOKEN" in src and "GITHUB_REPOSITORY" in src
    assert "Actions: read and write" in src, (
        "say which permission the token needs, or setting it up is guesswork")
    assert "status: 503" in src


def test_a_started_cycle_does_not_pretend_to_be_a_finished_one():
    """The dispatch returns immediately; the run takes minutes."""
    src = RUN_ROUTE.read_text(encoding="utf-8")
    assert "takes a few minutes" in src


def test_the_manual_ticket_is_folded_away_unless_it_is_the_only_way_to_act():
    """It bypasses strategies entirely. On an automatic desk it is noise."""
    src = PAGE.read_text(encoding="utf-8")
    assert "<details className={card} open={selected.mode==='manual'}>" in src
    assert "bypassing strategies" in src, "say what it is for, next to its name"


def test_the_page_no_longer_points_at_a_tab_that_was_renamed():
    """Three places told the owner to go to the Automation tab. It is called
    Settings now, and a page that sends you to a tab that does not exist is
    worse than one that says nothing."""
    src = PAGE.read_text(encoding="utf-8")
    assert "Automation</strong> tab" not in src
    assert "on the Automation tab" not in src
    assert "'Settings'" in src


def test_only_a_missing_token_can_disable_the_run_button():
    """Two rounds of a button that would not turn on. First it required
    GITHUB_REPOSITORY beside the token; then it inferred the repository from
    Vercel's VERCEL_GIT_REPO_* variables, which only exist when the project is
    Git-connected AND system environment variables are exposed - so it stayed
    disabled again, for a reason invisible from the outside.

    The repository is a constant that has never changed and is in every git
    remote here. It is written down. The environment still overrides it, but
    nothing needs configuring, and there is exactly ONE thing left to check."""
    src = RUN_ROUTE.read_text(encoding="utf-8")
    assert "const DEFAULT_REPO = 'hassansab00/arbdesk4'" in src
    assert ": DEFAULT_REPO" in src, "the constant must be the final fallback"
    assert "configured: !!token," in src, (
        "the repository can no longer be missing, so it must not gate the button")
    assert "missing: token ? [] : ['GITHUB_DISPATCH_TOKEN']" in src


def test_the_page_says_a_redeploy_is_needed():
    """Vercel bakes environment variables in at build time. Setting one and
    watching nothing change is the exact trap this hit, three times."""
    src = CONTROL.read_text(encoding="utf-8")
    assert "bakes them in at build time" in src


# ---------------------------------------------------------------------------
# THE PAGE OPENED ON AN EMPTY DESK
#
# It auto-selected accounts[0] over a list ordered by created_at. On 16 Sep
# that was "Main paper account" - manual, paused, 0 trades, 0 positions - while
# "Wide edge, all US" held 10 trades and 9 open positions. So opening Paper
# Trades showed a dead desk with nothing on it, and nothing on the page hinted
# that a live one existed two entries down a dropdown.

def test_the_page_opens_on_the_desk_that_is_trading():
    src = PAGE.read_text(encoding="utf-8")
    assert "accounts.data[0].account_id" not in src, (
        "creation order picked a paused manual desk over one holding 9 positions")
    assert "(b.open_positions??0)-(a.open_positions??0)" in src
    assert "(b.trade_count??0)-(a.trade_count??0)" in src


def test_the_choice_degrades_when_the_counts_are_absent():
    """The edge-gateway path returns plain accounts with no activity columns.
    A selection that breaks without them would swap one empty page for another."""
    src = PAGE.read_text(encoding="utf-8")
    assert "open_positions?:number" in src and "trade_count?:number" in src
    assert "??0" in src, "every count must have a fallback"


def test_the_dropdown_says_which_desk_is_which():
    """Picking the right desk should not require remembering which one trades."""
    src = PAGE.read_text(encoding="utf-8")
    assert "open`" in src and "trades`" in src


def test_the_counts_come_through_the_service_key_not_the_browser():
    """anon holds no grant on paper_positions, so the browser cannot count them
    itself. The desk list is already a server route; the view goes there."""
    route = (ROOT / "web" / "app" / "api" / "paper-desk" / "route.ts").read_text(encoding="utf-8")
    assert "v_paper_desk_activity" in route
    assert "from('paper_accounts')" not in route.split("resource==='accounts'")[1][:400], (
        "the accounts resource must read the activity view")
