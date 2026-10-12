"""When does a station report reach a reader? Aviation Weather's own receipt
time of every METAR it holds for the active cities' stations (one request; it
answers the last hours), against the report's reading time. Each report's
latency is written out with the summary, so the rule a table uses
(features.REPORT_LAG_S, REPORT_LAG_V2_S) can be checked against it.

    python tools/wxpredict/report_latency.py --out data/eval/wxpredict/report_latency_<date>.json
"""
import argparse
import datetime as dt
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common  # noqa: E402

AWC = "https://aviationweather.gov/api/data/metar"


def quantile(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def summarise(reports):
    """{all: {n, p50, p75, p90, p95}, by_station: {icao: {...}}} of minutes from reading to receipt."""
    def block(v):
        return {"n": len(v), **{f"p{int(p * 100)}": round(quantile(v, p), 2) for p in (0.5, 0.75, 0.9, 0.95)}}
    by = {}
    for r in reports:
        by.setdefault(r["icao"], []).append(r["latency_min"])
    return {"all": block([r["latency_min"] for r in reports]), "by_station": {s: block(v) for s, v in sorted(by.items())}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    stations = sorted({c["icao"].upper() for c in common.active_cities().values() if c.get("icao")})
    asked = dt.datetime.now(dt.timezone.utc)
    r = requests.get(AWC, params={"ids": ",".join(stations), "format": "json", "hours": 72}, timeout=60)
    r.raise_for_status()
    reports = []
    for x in r.json():
        obs = dt.datetime.fromtimestamp(x["obsTime"], dt.timezone.utc)
        rec = dt.datetime.fromisoformat(x["receiptTime"].replace("Z", "+00:00"))
        reports.append({"icao": x["icaoId"], "obs_time": obs.strftime("%Y-%m-%dT%H:%MZ"),
                        "receipt_time": rec.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "latency_min": round((rec - obs).total_seconds() / 60, 2)})
    reports.sort(key=lambda x: (x["icao"], x["obs_time"]))
    out = {"asked_at": asked.strftime("%Y-%m-%dT%H:%MZ"), "source": AWC, "stations_asked": stations,
           "stations_answered": sorted({x["icao"] for x in reports}),
           "reports_read": [reports and min(x["obs_time"] for x in reports), reports and max(x["obs_time"] for x in reports)],
           "summary": summarise(reports), "reports": reports}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as fh:
        fh.write(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("asked_at", "reports_read")} | {"all": out["summary"]["all"],
                     "answered": len(out["stations_answered"]), "asked": len(stations)}, indent=1))
    return out


if __name__ == "__main__":
    main()
