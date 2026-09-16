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
