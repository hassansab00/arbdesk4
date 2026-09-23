"""A Celsius bucket is one temperature; a Fahrenheit bucket is two.

Predictive -> a city -> "Hit and miss, day by day" printed the Celsius
winner, our call and the market's call as "22-23". The venue's bucket is
22°C. v_city_hit_history built its label from the stored edges, and a band
is half-open [band_lo, band_hi): "22-23" is the edges of the single degree
22, and "70-72" the edges of 70-71°F.

Measured on the live database, 23 Sep, closed buckets in v_canonical_bands:

    unit  width  bands   labels that are a range
    C     1      12,582  0        ("22°C")
    F     2       3,869  3,869    ("70-71°F")

So the view now prints the venue's own label from v_canonical_bands, and its
fallback (a band the canonical view does not know) is half-open too. Applied
live 23 Sep in one transaction that checked 383 rows before and after, the
hit flags, Brier scores, p-on-winner and errors identical (EXCEPT ALL both
ways), and 0 Celsius ranges left.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
VIEW = (ROOT / "sql" / "ad4_85_city_hit_history.sql").read_text()


def _ladder_cte():
    m = re.search(r"create (?:or replace )?view v_city_hit_history as\s+with ladder as \((.*?)\n\),",
                  VIEW, re.S | re.I)
    assert m, "the ladder CTE of v_city_hit_history moved; update this test"
    return m.group(1)


def test_the_label_is_the_venues():
    ladder = _ladder_cte()
    assert re.search(r"left join v_canonical_bands cb on cb\.band_id = o\.band_id", ladder)
    assert re.search(r"coalesce\(cb\.band_label,", ladder)


def test_the_fallback_is_half_open():
    ladder = _ladder_cte()
    # band_hi is excluded: the top of the range is band_hi - 1 ...
    assert "o.band_hi - 1" in ladder
    # ... and a one-degree band is one number, not "lo-hi".
    assert re.search(r"when o\.band_hi - o\.band_lo = 1 then trim\(to_char\(o\.band_lo", ladder)
    # the old closed-interval form must not come back
    assert not re.search(r"'-' \|\| trim\(to_char\(o\.band_hi, ", ladder), \
        "the label prints band_hi, which is outside the bucket"
