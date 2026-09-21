"""An archive is seven independent jobs. One of them failing is not all of them.

THE RUN THIS IS ABOUT. 21 Sep 08:29, the first scheduled Archive Observations
to include the new `books` dataset:

    === books ===
    50,486 rows -> books-2026-08-23-to-2026-09-14.csv.gz
    verified: 50,486 rows read back from the release
    Traceback (most recent call last):
      ...
    requests.exceptions.HTTPError: prune_book_redundancy -> HTTP 500:
      {"code":"57014","message":"canceling statement due to statement timeout"}
    ##[error]Process completed with exit code 1

`books` sorts first of seven. forecasts, observations, research, resolution
and trades were never attempted - on a database sitting at 111% of a 500 MB
tier, whose ONLY mechanism for getting back under it is this script.

main() already took max() of each table's return code, so every refusal
run_one anticipates - a failed verify, a count mismatch, a stale feature cache
- left the remaining tables to run. An exception did not: it unwound through
the loop to sys.exit, and the difference between archiving five tables and
archiving none was which one happened to raise.

The exit code still carries the worst result, so a broken table keeps CI red
until somebody fixes it. It just no longer takes the other six with it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import archive_observations as ao  # noqa: E402


@pytest.fixture
def archive(monkeypatch):
    """Run main() over every table with run_one and the log stubbed out."""

    def run(failing=(), rc_by_name=None):
        attempted, logged = [], []

        def fake_run_one(spec, name, args):
            attempted.append(name)
            if name in failing:
                raise RuntimeError(f"{name} -> HTTP 500: statement timeout")
            return (rc_by_name or {}).get(name, 0)

        monkeypatch.setattr(ao, "run_one", fake_run_one)
        monkeypatch.setattr(ao, "log_run",
                            lambda job, status, rows, detail: logged.append((job, status, detail)))
        monkeypatch.setattr(sys, "argv", ["archive_observations.py", "--commit"])
        return ao.main(), attempted, logged

    return run


def test_the_table_that_raises_does_not_take_the_others_with_it(archive):
    """The 21 Sep run, exactly: books raises and sorts first."""
    rc, attempted, _ = archive(failing={"books"})

    assert attempted[0] == "books", "the test no longer reproduces the run it is about"
    assert set(attempted) == set(ao.TABLES), (
        f"only {attempted} were attempted - one table's exception is still cancelling the rest"
    )
    assert rc != 0, "a table raised and the workflow would have gone green"


def test_every_table_can_be_the_one_that_fails(archive):
    """Not a property of `books`. Any of the seven, same answer."""
    for name in sorted(ao.TABLES):
        rc, attempted, _ = archive(failing={name})
        assert set(attempted) == set(ao.TABLES), f"{name} raising skipped {set(ao.TABLES) - set(attempted)}"
        assert rc != 0


def test_a_failure_is_recorded_where_the_desk_looks_for_it(archive):
    """ingest_log is how anyone asks 'did the archive run'. A crash that
    writes nothing there is indistinguishable from a run that never fired -
    which is exactly how this failure was first misread."""
    _, _, logged = archive(failing={"books"})

    assert [j for j, _s, _d in logged] == ["archive_books"]
    job, status, detail = logged[0]
    assert status == "attention"
    assert "RuntimeError" in detail["error"] and "statement timeout" in detail["error"], (
        f"the log says {detail['error']!r}, which does not say what went wrong"
    )


def test_the_worst_return_code_still_survives(archive):
    """A graceful refusal on one table and a clean run on the rest is still a
    failed archive."""
    rc, attempted, logged = archive(rc_by_name={"resolution": 1})
    assert set(attempted) == set(ao.TABLES)
    assert rc == 1
    assert logged == [], "a return code was logged as an exception"


def test_all_seven_clean_is_still_green(archive):
    rc, attempted, logged = archive()
    assert rc == 0 and set(attempted) == set(ao.TABLES) and logged == []
