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

**It is now one button.** `.github/workflows/verify_resolution_source.yml`
does the whole procedure on a GitHub Actions runner, which unlike the build
sandbox has normal internet access.

> GitHub -> **Actions -> Verify Resolution Source -> Run workflow**
> (leave both inputs blank to auto-pick a city with a resolved past market
> and use yesterday's date, or name a `city_key` and a `YYYY-MM-DD`.)

Needs the same two secrets as every other workflow: `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY`.

`scripts/verify_resolution_source.py` does two things, in this order:

1. **Tests the real parser.** It calls
   `settlement.fetch_resolution_source_reading()` itself - the actual
   function that will decide settlements - not a copy of it. That parser
   has never been checked against a live page, so this is the step that
   matters. If it does not hit, the script does **not** guess a
   temperature: it dumps the real page structure (script sources, inline
   `var`/`const` names, candidate JSON blobs, whether the date appears at
   all, the first 1500 bytes of body) and **fails the job**. Fix
   `fetch_resolution_source_reading()` against that output and re-run.

2. **Compares all three surfaces** for the same city-day - our IEM archive
   (`weather_observations`), `api.weather.gov`, and
   `weather.gov/wrh/timeseries`, the one that actually settles - and
   prints a result block in exactly the shape this file wants below.

The script is **read-only**. It cannot flip `settlement_verified`, by
design: that stays a decision you make after reading the numbers yourself.

### Then

1. Copy the printed block into the `## Measured` section below.
2. If, and only if, the parser hit a live page **and** the surfaces agree
   (the script reports the spread and calls it), flip the gate:

   ```sql
   update settings
   set value = jsonb_set(value, '{value}', 'true')
   where key = 'settlement_verified';
   ```

3. Re-run `sql/ad4_99_verify.sql` - its `settlement is still gated` row
   will now read ATTENTION rather than PASS, which is the correct reading
   once you have deliberately opened the gate.

A disagreement between surfaces is not a nuisance to work around. It is
exactly what this check exists to catch: settling off the wrong surface
pays the wrong person.

## Measured

_(nothing yet - paste the workflow's output block here)_

Until this file has real values in it, `scripts/settlement.py` runs in
dry-run mode by design (see `settings.settlement_verified`) - it computes
and prints what it would settle, but writes nothing to `paper_trades`.
