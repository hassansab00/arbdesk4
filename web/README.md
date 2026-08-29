# AD4 web

Next.js (App Router) frontend, per Revision A §6/§9: reads Supabase directly
via `supabase-js` with the anon key, writes only through RPCs
(`sql/ad4_rpc.sql`). **Zero Vercel serverless functions, two environment
variables.**

## Setup

```
cd web
npm install
cp .env.local.example .env.local   # fill in the real anon key
npm run dev
```

Requires every `sql/ad4_*.sql` file to have been run against the target
Supabase project first (schema, RLS, RPCs) - see the repo root README/docs
for run order. Without that, pages will load but show empty states rather
than erroring, since every query here is defensive about missing data.

## Structure

- `app/` - the ten sections from Task 14 (`page.tsx` = Overview; `board`,
  `opportunities`, `calculator`, `clusters`, `live` (Task 14 §8.4 Live
  Weather), `analytics`, `backtest`, `campaigns`, `goals`) plus the
  persistent global bar and signals slide-out in `app/layout.tsx`.
- `components/` - `GlobalBar`, `NavTabs`, `SignalsPanel` (Supabase Realtime
  on `signals`), `WeatherIcon` (animated condition icons, CSS only).
- `lib/supabase.ts` - the one Supabase client, anon key only.
- `lib/types.ts` - TypeScript shapes for the views/RPCs this reads.
  Deliberately loose (optional fields) - see docs/schema_assumptions.md at
  the repo root for why the exact base schema isn't guaranteed.
- `lib/region.ts` - longitude-band region derivation for City Clusters
  (no `region` column exists in the schema) and IANA-timezone UTC-offset
  math (via `Intl`, no library) for the 24h peak-window timeline.

## What's real vs. approximated here

- Every read is a real query against the actual views/tables this build
  created (`v_opportunities`, `live_weather`, `weather_events`,
  `backtest_results`, etc.) - not mock data.
- City Clusters draws a plain equirectangular scatter (lon/lat -> x/y) with
  no coastline data, rather than a real map library - "one node per city"
  is satisfied; cartographic accuracy is not attempted. Region grouping is
  a longitude-band heuristic, not an authoritative mapping.
- Live Weather's condition icons are hand-rolled CSS/SVG animations (8
  conditions), not illustration-quality art - functional per the
  `prefers-reduced-motion`-respecting animation requirement.
- The backtest dashboard's equity curve is a small hand-rolled inline SVG
  polyline, not a charting library (none is loaded in this project).
- Goals' capacity reality-check uses a documented, provisional multiplier
  (5% of aggregate 5c-slippage depth converts to daily EV) - a sanity
  check, not a forecast; the page states plainly when a target looks
  unachievable rather than implying otherwise.

## Known residual dependency risk

`npm audit` reports a high-severity PostCSS advisory (arbitrary source-map
file disclosure) nested inside Next.js's own bundled copy of `postcss`
(`node_modules/next/node_modules/postcss`), not this project's direct
`postcss` dependency. The fix requires jumping to Next.js 16, a breaking
major-version change this build hasn't validated end-to-end - `npm run
build` and `npm run typecheck` both pass clean on 14.2.35, which already
resolves the one *critical* advisory that was on 14.2.5. Revisit before a
production deploy: either confirm the residual risk is acceptable (it's a
dev-time source-map path traversal, not a runtime request-handling issue)
or do the Next 16 upgrade as its own tested change.

## Deploy

Vercel, from `main`, with `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` set as environment variables. No other
config needed - there is no API layer to deploy alongside it.
