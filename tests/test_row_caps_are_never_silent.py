"""A query that was cut short must say so.

PostgREST's max-rows is 1,000 on this project, and it does not error when it
truncates - it returns 1,000 rows and a 200. So `.limit(4000)` never returns
more than 1,000, and a page that filters those rows in the browser shows a
slice of the alphabet and looks complete.

It has happened: the Predictive page asked v_forecast_convergence for 20,000
rows and filtered by city client-side. The view holds 55,000 rows ordered by
city_key, so PostgREST returned the alphabetically first cities and every
other city's chart said "no data for this city" - which is exactly what the
page saw, and completely wrong.

useQuery takes the same number that was passed to .limit() and sets
`truncated` when the answer comes back at the ceiling. This keeps every call
site honest, because the failure is invisible without it.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

# Below this, the server's 1,000-row ceiling cannot be the thing that cut the
# answer short, so a missing declaration costs nothing.
REACHES_THE_CEILING = 1000


def _sources():
    for f in sorted(WEB.rglob("*.ts")) + sorted(WEB.rglob("*.tsx")):
        if "node_modules" in f.parts or ".next" in f.parts:
            continue
        yield f


def _declarations(src):
    """(line, limit, declared?) for every .limit(N) that can reach the ceiling.

    Deliberately a TEXT check rather than a parse. Every serious attempt to
    parse the call properly choked on the same things TypeScript is full of -
    JSX braces, template literals, a comment block between the arguments - and
    a parser that silently swallows the rest of the file reports offenders that
    are not there. The shape this codebase actually writes is the limit passed
    within a few lines of the .limit() call, so that is what is checked.
    """
    # Comments in this repo explain the ceiling in prose that quotes the
    # numbers - ".limit(20000)" appears in the very docstring describing the
    # bug - so they are removed before anything is counted.
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//[^\n]*$", "", src, flags=re.M)
    for m in re.finditer(r"\.limit\(\s*(\d+)\s*\)", src):
        n = int(m.group(1))
        if n < REACHES_THE_CEILING:
            continue
        tail = src[m.end(): m.end() + 600]
        tail = re.sub(r"//[^\n]*", "", tail)
        tail = re.sub(r"/\*.*?\*/", "", tail, flags=re.S)
        # the number as its own argument, before the call closes
        stop = tail.find(");")
        window = tail if stop < 0 else tail[:stop + 1]
        declared = re.search(rf",\s*{n}\s*[,)\n]", window) is not None
        yield src[:m.start()].count("\n") + 1, n, declared


def test_every_query_that_can_hit_the_ceiling_declares_its_limit():
    offenders = []
    for f in _sources():
        # useCityStats' fallback is a plain Promise.all, not a useQuery, so it
        # has no flag to set. It reports the cut in words instead, which
        # test_the_fallback_path_says_so_too pins.
        if f.name == "useCityStats.ts":
            continue
        # The masthead counts markets with the server's own exact count and
        # only reads rows for the distinct days, saying "days+" when that read
        # was cut short. test_the_masthead_counts_with_the_server pins it.
        if f.name == "Header.tsx":
            continue
        for line, n, declared in _declarations(f.read_text(encoding="utf-8")):
            if not declared:
                offenders.append(
                    f"{f.relative_to(ROOT)}:{line} asks for {n} rows and never tells "
                    f"useQuery, so a server-side cut is invisible")
    assert not offenders, (
        "these queries can be truncated by the server without saying so:\n  "
        + "\n  ".join(offenders))


def test_the_flag_fires_at_exactly_the_ceiling_not_only_at_the_asked_limit():
    """The case it exists for: a query asked for 4,000, was cut to 1,000 by the
    platform, and looks complete because 1,000 is less than 4,000. A guard that
    only compared against the CLIENT's number would never fire on it."""
    src = (WEB / "lib" / "useQuery.ts").read_text()
    assert re.search(r"1000|1_000", src), "nothing in useQuery knows the server's ceiling"
    assert "truncated" in src


def test_the_fallback_path_says_so_too():
    """useCityStats builds the same shape from raw tables when v_city_stats is
    unavailable, in a plain Promise.all that cannot use the flag. 54 cities by
    3 models by 8 leads is about 1,300 forecast rows for one day."""
    src = (WEB / "lib" / "useCityStats.ts").read_text()
    assert "1,000-row ceiling" in src


def test_a_page_that_shows_the_truncation_exists():
    """The flag is only worth setting if something renders it."""
    ds = (WEB / "components" / "DataState.tsx").read_text()
    assert "truncated" in ds


def test_the_masthead_counts_with_the_server_not_with_the_rows_it_got():
    """"N live markets over M settlement days" is the desk describing its own
    coverage. Counting the returned array would understate it the day there are
    more than a thousand forward markets - and understate it silently."""
    src = (WEB / "components" / "Header.tsx").read_text()
    assert 'count: "exact"' in src
    assert "mkt.count" in src
    assert "daysPartial" in src, "the day count is a floor when the read was cut short"
