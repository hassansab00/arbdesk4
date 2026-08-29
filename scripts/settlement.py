"""
AD4 settlement + ledger (Task 11). Daily plus on-demand.

50 of 54 cities resolve on weather.gov/wrh/timeseries?site=<icao>
[SOURCED]. Hong Kong uses HKO - the spec never gives HKO's actual data
URL, so this module does not guess one (see NotImplementedError below);
wire it in once that URL is confirmed.

BLOCKED, per the spec's own instruction - verify before relying on it:
this repo's existing weather_observations archive is sourced from IEM
METAR (scripts/ingest_observations.py, source="IEM"), NOT
weather.gov/wrh/timeseries - a DIFFERENT surface from the one the rules
actually point at. They are almost certainly the same observations
underneath, but "almost certainly" is not an acceptable standard for the
thing that decides who gets paid. `settings.settlement_verified` gates
auto-settlement: while false (the default), settlement runs in
dry-run mode - it computes and logs what it WOULD settle, but does not
write to paper_trades, until a real spot-check (docs/settlement_
verification.md) has been run with actual network access and the
setting flipped. This session has no outbound access to weather.gov to
run that check itself (blocked by this sandbox's egress policy, not a
production constraint) - see docs/settlement_verification.md for exact
instructions to run it for real.
"""
import datetime as dt
import re

import requests

from common import rest, insert, _cfg, _headers, get_cities
import cost_model

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"


def _upcoming_or_past_unclosed_markets():
    """Markets whose resolution_date has passed but aren't marked closed."""
    return rest("markets", [
        ("select", "market_id,city_key,resolution_date,event_slug,condition_id,closed"),
        ("closed", "eq.false"),
        ("resolution_date", f"lt.{dt.date.today().isoformat()}"),
    ])


def gamma_market_resolution(condition_id):
    """
    Best-effort read of Gamma's resolution status for one market.
    SCHEMA/API ASSUMPTION: gamma-api.polymarket.com/markets?condition_ids=
    returns a list with `closed`/`umaResolutionStatus`/`outcomePrices` -
    verify against a live response before trusting in production; this
    session cannot reach gamma-api.polymarket.com to confirm the exact
    shape (network egress blocked here, not in GitHub Actions).
    """
    r = requests.get(GAMMA_MARKETS_URL, params={"condition_ids": condition_id}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def resolution_source_url(icao):
    return f"https://www.weather.gov/wrh/timeseries?site={icao}"


def fetch_resolution_source_reading(icao, for_date):
    """
    Fetches the Hourly Data view for `icao` and extracts the max
    temperature for `for_date`. PARSING NOT YET VERIFIED AGAINST A LIVE
    PAGE (see module docstring) - this makes a best effort against a
    couple of plausible page shapes and raises rather than silently
    returning a wrong number if neither matches. Confirm and adjust this
    parser as the first step of docs/settlement_verification.md before
    trusting its output.
    """
    url = resolution_source_url(icao)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    html = r.text

    # Attempt 1: an embedded JSON data blob (common for weather.gov's
    # client-rendered timeseries pages).
    m = re.search(r"var\s+obsData\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if m:
        import json
        try:
            rows = json.loads(m.group(1))
            temps = [row["temp"] for row in rows if row.get("date", "").startswith(for_date) and row.get("temp") is not None]
            if temps:
                return max(temps)
        except Exception:
            pass

    raise RuntimeError(
        f"TODO: unmeasured - could not parse {url} for {for_date}. "
        "Page structure not yet confirmed live (see docs/settlement_verification.md); "
        "do not guess a value here."
    )


def is_revision_window_closed(city_key, for_date):
    """
    Revisions to `for_date`'s reading count until the following date's
    first datapoint publishes. Uses this repo's own (frequently-polled)
    weather_observations archive as the "has the next day started"
    signal - a separate question from whether that archive's SOURCE
    matches the resolution surface (the thing settlement_verification.md
    checks).
    """
    next_day = (dt.date.fromisoformat(for_date) + dt.timedelta(days=1)).isoformat()
    rows = rest("weather_observations", [
        ("select", "valid_at"), ("city_key", f"eq.{city_key}"),
        ("valid_at", f"gte.{next_day}"), ("limit", "1"),
    ])
    return bool(rows)


def settle_via_rpc(market_id, settled_value, winning_band_id, source, verified_at):
    r = requests.post(f"{_cfg()['url']}/rest/v1/rpc/settle_markets", headers=_headers(),
                      json={"p_market_id": market_id, "p_settled_value": settled_value,
                            "p_winning_band_id": winning_band_id, "p_source": source,
                            "p_verified_at": verified_at}, timeout=60)
    r.raise_for_status()
    return r.json()


def find_winning_band(bands, settled_value, unit):
    """bands: list of {band_id, band_lo, band_hi, open_low, open_high}."""
    local_value = settled_value * 9.0 / 5.0 + 32.0 if unit == "F" else settled_value
    for b in bands:
        if b.get("open_low") and local_value < b["band_hi"]:
            return b["band_id"]
        if b.get("open_high") and local_value >= b["band_lo"]:
            return b["band_id"]
        if b.get("band_lo") is not None and b.get("band_hi") is not None and b["band_lo"] <= local_value < b["band_hi"]:
            return b["band_id"]
    return None


def main():
    settings = {r["key"]: r["value"] for r in rest("settings", {"select": "key,value"})}
    verified = bool((settings.get("settlement_verified") or {}).get("value", False))
    cities = {c["city_key"]: c for c in get_cities(require_coords=False)}

    markets = _upcoming_or_past_unclosed_markets()
    print(f"{len(markets)} unclosed markets past resolution_date")

    n_settled, n_flagged, n_dry_run = 0, 0, 0
    for m in markets:
        city = cities.get(m["city_key"], {})
        icao = city.get("icao")
        unit = city.get("unit", "C")

        gamma = None
        try:
            if m.get("condition_id"):
                gamma = gamma_market_resolution(m["condition_id"])
        except Exception as e:
            print(f"  ! Gamma read failed for {m['city_key']}: {e}")

        if not is_revision_window_closed(m["city_key"], m["resolution_date"]):
            print(f"  {m['city_key']} {m['resolution_date']}: revision window still open, skipping")
            continue

        try:
            if icao == "HKO" or city.get("resolution_source") == "HKO":
                raise NotImplementedError(
                    "TODO: unmeasured - Hong Kong's HKO data URL is not given anywhere in the "
                    "spec; do not guess one. Wire this in once confirmed."
                )
            settled_value = fetch_resolution_source_reading(icao, m["resolution_date"])
        except Exception as e:
            print(f"  ! resolution source read failed for {m['city_key']}: {e}")
            n_flagged += 1
            continue

        bands = rest("bands", [("select", "band_id,band_lo,band_hi,open_low,open_high"),
                                ("market_id", f"eq.{m['market_id']}")])
        winning_band_id = find_winning_band(bands, settled_value, unit)

        gamma_disputed = False
        if gamma is not None and gamma.get("closed") and winning_band_id:
            gamma_disputed = True  # TODO: unmeasured - compare gamma's outcomePrices/winning
            # token against winning_band_id's token_yes once the real Gamma response shape
            # (see gamma_market_resolution docstring) is confirmed against a live call.

        if gamma_disputed:
            n_flagged += 1
            print(f"  ! {m['city_key']} {m['resolution_date']}: resolution source vs. Gamma "
                  f"mismatch flagged, NOT auto-settled - RESOLUTION_RISK")
            continue

        if not verified:
            n_dry_run += 1
            print(f"  DRY RUN (settlement_verified=false): would settle {m['city_key']} "
                  f"{m['resolution_date']} at {settled_value} -> band {winning_band_id}")
            continue

        try:
            n_trades = settle_via_rpc(m["market_id"], settled_value, winning_band_id,
                                       source=resolution_source_url(icao),
                                       verified_at=dt.datetime.now(dt.timezone.utc).isoformat())
            n_settled += 1
            print(f"  settled {m['city_key']} {m['resolution_date']}: {n_trades} trades closed")
        except Exception as e:
            print(f"  ! settle_via_rpc failed for {m['city_key']}: {e}")
            n_flagged += 1

    print(f"\nsettled={n_settled} flagged={n_flagged} dry_run={n_dry_run}")


if __name__ == "__main__":
    main()
