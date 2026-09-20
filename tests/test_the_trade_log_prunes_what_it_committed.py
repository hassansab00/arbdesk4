"""A delete that can outrun its own durable write.

paper_trade_log.yml exports closed trades to web/public/paper-trades and then
deletes them from Postgres. The two used to happen in ONE step, with the commit
afterwards:

    1  export_paper_trades.py --prune-days 30   writes the files, deletes rows
    2  git add / commit / push

The runner's filesystem is not the archive; the repository is. Anything that
went wrong between 1 and 2 - a failed commit, a failed push, a cancelled job, a
lost runner - left those trades in NO PLACE AT ALL. That is not hypothetical:
the observations archive index was written to a runner and destroyed with it on
every run for days, and it was only an index. Here the same gap deletes rows.

AND THE PUSH REPORTED SUCCESS WHEN IT FAILED. The retry loop was

    for i in 1 2 3 4; do
      git push ... && break
      echo "push failed, retrying"; sleep ...
      git pull --rebase ...
    done

Four failures and the loop simply ends; the step's exit status is the trailing
`git pull --rebase`, which usually succeeds. So a total push failure was a green
step - and under the old ordering the rows were already gone by then.

THREE THINGS FIX IT, and this file holds all three:

  1 the order is export, commit, prune - never fused
  2 the push fails the step when no attempt succeeded
  3 prune() asks GIT whether the files are committed, so the guarantee does not
    rest on step ordering that someone can rearrange later
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "paper_trade_log.yml").read_text(encoding="utf-8")
SCRIPT = (ROOT / "scripts" / "export_paper_trades.py").read_text(encoding="utf-8")


def test_the_export_step_does_not_also_prune():
    """The fused step is the defect. Exporting and deleting in one run means
    the delete happens before anything durable has been written."""
    step = WORKFLOW[WORKFLOW.index("- name: Export closed trades"):]
    step = step[:step.index("- name:", 10)]
    assert "--prune-days" not in step, (
        "the export step prunes again, so rows are deleted before the commit that "
        "makes their file durable"
    )


def test_the_prune_runs_after_the_commit():
    order = [WORKFLOW.index("- name: Export closed trades"),
             WORKFLOW.index("- name: Commit the log"),
             WORKFLOW.index("- name: Prune exported trades from Postgres")]
    assert order == sorted(order), (
        "the steps are no longer export -> commit -> prune, so the delete can precede "
        "the only write that outlives the runner"
    )


def test_a_push_that_never_succeeded_fails_the_step():
    """Otherwise the prune step runs on a green commit step that pushed nothing."""
    step = WORKFLOW[WORKFLOW.index("- name: Commit the log"):]
    step = step[:step.index("- name:", 10)]
    assert re.search(r"pushed=1", step) and re.search(r'if \[ "\$pushed" != "1" \]', step), (
        "the push loop can run out of retries and still report success, which makes the "
        "prune that follows it delete rows whose file never left the runner"
    )
    assert "exit 1" in step


def test_the_prune_refuses_anything_not_committed():
    """The guarantee that does not depend on step ordering. Someone can
    rearrange a workflow; this asks git."""
    assert "def uncommitted(" in SCRIPT
    assert 'git", "status", "--porcelain"' in SCRIPT, (
        "prune() no longer asks git whether the files it is about to delete rows for "
        "are committed"
    )
    body = SCRIPT[SCRIPT.index("def prune("):SCRIPT.index("def main(")]
    assert "dirty = uncommitted()" in body and "REFUSING TO PRUNE" in body, (
        "prune() reads the filesystem again without checking the files are in the repo"
    )
    assert body.index("dirty = uncommitted()") < body.index("on_disk = sorted(read_existing())"), (
        "the commit check runs after the ids are gathered, so it cannot stop the delete"
    )


def test_prune_only_needs_a_window():
    """--prune-only with no --prune-days would prune on a null cutoff."""
    body = SCRIPT[SCRIPT.index("def main("):]
    assert "--prune-only needs --prune-days" in body
