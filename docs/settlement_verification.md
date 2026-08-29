# Settlement source verification — TODO: unmeasured

**Status:** not yet run. This file is a placeholder, not a result.

## Why this file exists (spec Task 11, "BLOCKED" note)

The resolution rules point at `weather.gov/wrh/timeseries?site=<icao>`.
This repo's existing `weather_observations` archive
(`scripts/ingest_observations.py`) is sourced from **IEM METAR**, a
different front-end. They are almost certainly the same observations
underneath, but the spec is explicit: "almost certainly" is not an
acceptable standard for the thing that decides who gets paid. Before
`settings.settlement_verified` is flipped to `true` (see
`sql/ad4_settlement.sql`), someone with real network access needs to
spot-check one settled city-day against both surfaces and record the
result here.

This build session could not do that itself: outbound requests to
`weather.gov` and `api.weather.gov` are blocked by this sandbox's egress
policy (confirmed - both return `CONNECT tunnel failed, response 403,
connect_rejected (organization policy)`). This is a property of the dev
sandbox, not of production - GitHub Actions runners have normal internet
access, so `scripts/settlement.py` will actually be able to reach these
sites when it runs there.

A second, related gap: `scripts/settlement.py`'s
`fetch_resolution_source_reading()` parses the weather.gov timeseries
page looking for an embedded `obsData` JSON blob, but **that parsing has
never been checked against a live page** - it's a best guess at a
plausible structure, not a confirmed one. Fixing the parser is step 1
below, not optional.

## How to actually run this

1. Pick one city with at least one already-resolved (past) market and a
   known ICAO code (from the `cities` table).
2. Fetch `https://www.weather.gov/wrh/timeseries?site=<icao>` for that
   date and inspect the actual page source - find where the hourly
   temperature series actually lives (view source / dev tools network
   tab). Update `fetch_resolution_source_reading()` in
   `scripts/settlement.py` to match reality if the `obsData` regex
   doesn't hit.
3. Also fetch `https://api.weather.gov/stations/<icao>/observations` for
   the same window (this is the OTHER surface, the one our IEM-sourced
   archive is closest to in spirit but not identical to).
4. Compare all three: our `weather_observations` (IEM), `api.weather.gov`,
   and `weather.gov/wrh/timeseries` (the actual resolution source) for
   that city-day's max temperature.
5. Record the outcome below:

   ```
   City:              TODO
   ICAO:              TODO
   Date checked:      TODO
   IEM max:           TODO
   api.weather.gov max: TODO
   weather.gov/wrh/timeseries max (the resolution source): TODO
   Match?             TODO
   ```

6. If all three agree (or IEM/api.weather.gov are within measurement
   noise of the actual resolution source), it's reasonable to treat IEM
   as a fast proxy for day-to-day monitoring while still verifying the
   *actual* settlement value against `weather.gov/wrh/timeseries`
   specifically before paying out - `scripts/settlement.py` already does
   this correctly (it never settles off the IEM archive, only off a
   direct fetch of the real resolution source).
7. Once confirmed, run
   `update settings set value = '{"value": true, ...}' where key =
   'settlement_verified';` to take settlement out of dry-run mode.

Until this file has real values in it, `scripts/settlement.py` runs in
dry-run mode by design (see `settings.settlement_verified`) - it computes
and prints what it would settle, but writes nothing to `paper_trades`.
