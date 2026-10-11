"""The exposure ledger (data/eval/wxpredict/exposure_ledger.json) says which
dates each WXPredict question has been scored on, so a rule is never tested on
dates it was found, tuned or looked at on (the Codex action plan of 11 Oct,
section 2). It holds only if every result is registered and stays inside the
dates registered for it, and if untouched_from is past every date scored."""
import calendar
import datetime as dt
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL = ROOT / "data" / "eval" / "wxpredict"
LEDGER = json.loads((EVAL / "exposure_ledger.json").read_text())


def _dates_of(result):
    """The first and last event date a result scored: its window, or its
    test months cut at the table's newest date."""
    if "window" in result:
        return result["window"][0], result["window"][1]
    if "test_months" in result:
        first, last = result["test_months"][0], result["test_months"][-1]
        y, m = map(int, last.split("-"))
        end = f"{last}-{calendar.monthrange(y, m)[1]:02d}"
        return f"{first}-01", min(end, LEDGER["table"]["recorded"]["dates"][1])
    return None


def test_every_result_is_registered_once():
    results = sorted(p.name for p in EVAL.glob("*.json") if p.name != "exposure_ledger.json")
    registered = [r for q in LEDGER["questions"] for r in q["results"]]
    assert sorted(registered) == results, "register each result in exposure_ledger.json in the PR that commits it"
    assert len(set(registered)) == len(registered)


def test_each_result_stays_inside_its_registered_dates():
    for q in LEDGER["questions"]:
        for name in q["results"]:
            span = _dates_of(json.loads((EVAL / name).read_text()))
            if span is None:
                assert q["scored"] == [], f"{name} scores no dates; {q['id']} must register none"
                continue
            lo, hi = q["scored"]
            assert lo <= span[0] and span[1] <= hi, f"{name} scored {span}, outside {q['id']}'s {q['scored']}"


def test_untouched_from_is_after_every_date_scored():
    newest = max(q["scored"][1] for q in LEDGER["questions"] if q["scored"])
    day_after = (dt.date.fromisoformat(newest) + dt.timedelta(days=1)).isoformat()
    assert LEDGER["untouched_from"] >= day_after, "a result scored a date the ledger calls untouched"


def test_a_pending_test_is_on_dates_not_yet_seen():
    for q in LEDGER["questions"]:
        pending = q.get("pending")
        if pending:
            assert pending["test_from"] >= LEDGER["untouched_from"], f"{q['id']} would be tested on seen dates"
            for d in q.get("developed_on", []):
                assert d < pending["test_from"]


def test_each_rule_is_where_the_ledger_says():
    for q in LEDGER["questions"]:
        src = (ROOT / q["rule_file"]).read_text()
        assert q["rule_marker"] in src, f"{q['id']}: '{q['rule_marker']}' not in {q['rule_file']}"
        if q.get("rule_commit"):
            assert re.fullmatch(r"[0-9a-f]{7,40}", q["rule_commit"])
