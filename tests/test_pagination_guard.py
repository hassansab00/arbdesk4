"""No engine read may ask a bare rest() for more rows than the server returns.

PostgREST caps every response at db-max-rows - 1,000 on this project - and
ignores a larger ?limit= without a word. A bare rest() with limit=10000 is
therefore not "up to ten thousand rows"; it is "the first thousand, in whatever
order, and the rest silently dropped". That single fact measured forecast skill
for one city out of 54, stopped the weather model from ever fitting, and froze
the databank's band outcomes. common.rest_all exists to read a bounded scope
completely; this test keeps every larger read on it.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER_CAP = 1000

# A bare rest( call - not rest_all( - up to its closing bracket, with a limit
# expressed either as a ("limit", "N") tuple or a "limit": "N" dict entry.
CALL = re.compile(r'(?<![\w.])rest\(\s*"([^"]+)"\s*,(.*?)\)\s*(?:\n|:|\.|,|\])', re.S)
LIMIT = re.compile(r'"limit"\s*[,:]\s*"(\d+)"')
DYNAMIC = re.compile(r'"limit"\s*,\s*str\(')


def _bare_reads_over_cap():
    found = []
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        src = path.read_text()
        for m in CALL.finditer(src):
            line = src[:m.start()].count("\n") + 1
            for lim in LIMIT.finditer(m.group(2)):
                if int(lim.group(1)) > SERVER_CAP:
                    found.append(f"{path.relative_to(ROOT)}:{line} rest(\"{m.group(1)}\") limit={lim.group(1)}")
            if DYNAMIC.search(m.group(2)):
                found.append(f"{path.relative_to(ROOT)}:{line} rest(\"{m.group(1)}\") pages with a computed limit")
    return found


def test_no_bare_read_asks_for_more_than_the_server_returns():
    assert _bare_reads_over_cap() == [], (
        "these reads ask for more than PostgREST's 1,000-row cap and would be "
        "silently truncated - use common.rest_all with a stable order:\n  "
        + "\n  ".join(_bare_reads_over_cap()))


def test_rest_all_refuses_to_run_without_a_stable_order():
    import common
    import pytest
    with pytest.raises(ValueError):
        common.rest_all("markets", [], order="")
