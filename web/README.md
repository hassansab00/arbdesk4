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
Supabase project first, starting with `sql/ad4_00_preflight.sql` - see
`docs/GO_LIVE.md` for the exact ordered run.

**Every fetch has three states and none of them is a blank screen**
(`lib/useQuery.ts` + `components/DataState.tsx`):

- *loading* - a spinner with a label, never an indefinite blank;
- *error* - the raw Supabase/Postgres message, verbatim and unswallowed,
  so `relation "v_opportunities" does not exist` reaches the screen, plus
  the concrete next step for that class of error;
- *empty* - what is missing, WHY it is empty, and WHICH workflow fills it.

If the two `NEXT_PUBLIC_SUPABASE_*` variables are missing from the build,
`components/ConfigBanner.tsx` says so at the top of every page and names
the variables. `app/error.tsx` and `app/global-error.tsx` catch anything
that still throws, so a client exception shows its message rather than
Next's default blank "Application error" screen.

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
- `lib/useQuery.ts` / `components/DataState.tsx` - the loading/error/empty
  contract every page uses.
- `lib/spread.ts` - the Goals engine: a direct TypeScript port of ArbDesk
  1's `spread` / `spreadNo` / `spreadWeighted` / `invertYesTarget` /
  `invertNoTarget` / `coverageIndices` / `buildTiers` / ladder-walking
  functions, so a plan built here matches AD4-1's numbers to the cent.
  What changed is only where the inputs come from: real bands, real ask
  ladders from `book_snapshots`, and model probabilities from
  `band_probabilities`, instead of a hand-typed board.

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
- Goals is the AD4-1 Goal section, not an approximation of it: the four
  risk tiers, the profit <-> budget solve toggle, Yes-lock / No-lock /
  Mix / Weighted sides, the stop floor, the slippage buffer, cent-exact
  legs, the $1-order-minimum hint and the guaranteed-only book check all
  behave identically. Its multi-day projection compounds today's
  single-day economics forward and says so - it assumes each day is an
  independent repeat of the same spread, which correlated weather days
  violate. That is a modelling choice, stated on the page, not a claim.
- Market volume is a first-class input, everywhere, and is never merged
  with book depth into one "liquidity" number: depth (`fillable_usd_*`)
  is what the current quote can absorb, volume (`v_band_volume` /
  `v_city_volume`) is what has actually traded. Both are shown side by
  side on the board, the opportunity cards, the calculator, the goals
  legs and tiers, the clusters map, and analytics. The ranking score
  multiplies by a saturating factor `volume/(volume+k)` that can only
  discount an illiquid market, never inflate a liquid one; `k` and the
  thin-market cutoffs live in `settings.volume_thresholds`, are labelled
  provisional with no evidential basis, and are UI-settable.

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

Vercel, from `main`, **Root Directory = `web`**, with
`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` set as
environment variables. No other config needed - there is no API layer to
deploy alongside it, and `web/vercel.json` already sets the framework,
build, dev and install commands for that root.

Use the **publishable** key — `sb_publishable_…` on newer Supabase
projects, or the legacy `anon` JWT. Never a secret key: anything prefixed
`NEXT_PUBLIC_` is compiled into the JavaScript every visitor downloads, so a
secret key set here is published the moment the page is served and must then
be **rotated**, not just replaced. The secret key (`sb_secret_…` /
`service_role`) belongs only in the GitHub Actions secret
`SUPABASE_SERVICE_KEY` and in n8n Config nodes.

`lib/keyGuard.ts` enforces this rather than trusting it: it classifies the
configured key across both Supabase key generations, and `lib/supabase.ts`
refuses to construct a client from a secret one — so no request is ever
attempted with it — while `ConfigBanner` states on every page that the key
is already public and must be rotated. Verified end to end against a built
app with a planted secret key: banner on every route, zero outbound
requests.

Both env vars are inlined at **build** time, so adding them after a failed
deploy needs a redeploy, not a restart. The build itself does not need
them - `npm install && npm run build` with a completely empty environment
exits 0, which is the exact condition Vercel builds under before the
variables are read.

Full first-run sequence: `docs/GO_LIVE.md`.
