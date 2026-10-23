"""The routine-report temperature contract, separate from all-report weather.

Only contracts explicitly naming AWC's raw METAR endpoint use this definition.
Older WRH contracts and their frozen records keep their original definition.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from decimal import Decimal, ROUND_FLOOR
from urllib.parse import parse_qs, urlsplit

AWC_URL = re.compile(r"https://aviationweather\.gov/api/data/metar\?[^\s<>\\]+", re.I)
HEADER = re.compile(r"^(?:(METAR|SPECI)\s+)?(?:COR\s+)?([A-Z0-9]{4})\s+(?:COR\s+)?(\d{2})(\d{2})(\d{2})Z(?:\s|$)")
T_GROUP = re.compile(r"(?<!\S)T([01])(\d{3})\d{4}(?!\S)")
BODY_TEMP = re.compile(r"(?<!\S)(M?\d{2})/(?:M?\d{2}|//)?(?!\S)")


def source_url(rules: str) -> str | None:
    match = AWC_URL.search(rules.replace("\\n", "\n"))
    return match.group().rstrip(".,)") if match else None


def source_parameters(url: str) -> tuple[str, dt.datetime]:
    parsed = urlsplit(url)
    q = parse_qs(parsed.query)
    if (parsed.scheme != "https" or parsed.netloc != "aviationweather.gov"
            or parsed.path != "/api/data/metar" or parsed.fragment
            or set(q) != {"ids", "format", "date", "hours"}
            or any(len(v) != 1 for v in q.values())
            or not re.fullmatch(r"[A-Z0-9]{4}", q["ids"][0].upper())
            or q["format"] != ["raw"] or q["hours"] != ["27"]):
        raise ValueError("Unsupported AWC contract source parameters")
    at = dt.datetime.fromisoformat(q["date"][0].replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() != dt.timedelta(0):
        raise ValueError("AWC source date must specify UTC")
    return q["ids"][0].upper(), at


def temperature_c(report: str) -> Decimal | None:
    """SPECI is never qualifying; T+exactly eight digits wins over the body.

    This does not infer routine status from a report's minute, pressure or COR.
    The source's report text, including an absent METAR label, is decisive.
    """
    text = report.strip().rstrip("=").strip()
    if text.startswith("SPECI"):
        return None
    body, separator, remarks = text.partition(" RMK ")
    matches = list(T_GROUP.finditer(remarks)) if separator else []
    if len(matches) > 1:
        raise ValueError("Ambiguous hourly temperature remarks")
    if matches:
        m = matches[0]
        return Decimal(m[2]) / 10 * (-1 if m[1] == "1" else 1)
    m = BODY_TEMP.search(body)
    if m is None:
        return None
    return Decimal(m[1][1:]) * -1 if m[1].startswith("M") else Decimal(m[1])


def rounded_unit(celsius: Decimal, unit: str) -> int:
    value = celsius * Decimal(9) / 5 + 32 if unit == "F" else celsius
    if unit not in ("C", "F"):
        raise ValueError("Unknown market unit")
    return int((value + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


def raw_reports(raw: bytes, station: str, reference: dt.datetime):
    """Parse AWC's current raw response, refusing conflicting duplicate times.

    METAR times omit month/year. The source query's dated UTC anchor selects
    the nearest month, including year transitions. No station substitution.
    """
    text = raw.decode("utf-8-sig").strip()
    rows = {}
    for line in text.splitlines():
        line = line.strip().rstrip("=").strip()
        if not line:
            continue
        m = HEADER.match(line)
        if m is None or m[2] != station:
            raise ValueError("AWC response contains an unrecognized report or wrong station")
        day, hour, minute = map(int, m.group(3, 4, 5))
        candidates = []
        for shift in (-1, 0, 1):
            month_index = reference.year * 12 + reference.month - 1 + shift
            year, month0 = divmod(month_index, 12)
            if day <= calendar.monthrange(year, month0 + 1)[1]:
                candidates.append(dt.datetime(year, month0 + 1, day, hour, minute, tzinfo=dt.timezone.utc))
        when = min(candidates, key=lambda x: abs((x - reference).total_seconds()))
        if abs((when - reference).total_seconds()) > 4 * 86400:
            raise ValueError("AWC report is outside the requested date window")
        if when in rows and rows[when]["raw"] != line:
            raise ValueError("AWC shows conflicting reports for one observation time")
        rows[when] = {"when": when, "routine": m[1] != "SPECI",
                      "temp_c": temperature_c(line), "raw": line}
    return [rows[k] for k in sorted(rows)]
