// ===========================================================================
// THE VENUE IS THE TRUTH (plan v2.4 P3.10 part 3.1): v_venue_truth gives each
// settled city-day one label in Celsius - the venue's verified reading when it
// lies in the confirmed winner's bucket (or no winner is confirmed yet), else
// the winning bucket's midpoint reading; a tail or two winners give none. It
// is the service role's alone, and the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260928120000_the_venue_is_the_truth.sql'), 'utf-8');

const b = (n) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const m = (n) => `10000000-0000-0000-0000-${String(n).padStart(12, '0')}`;

(async () => {
  const db = new PGlite();
  // The shapes the view reads (live columns, 28 Sep); built by earlier
  // migrations in production, stood in for here as tables.
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.v_verified_weather_outcomes (city_key text, for_date date, observed_max_c numeric);
    create table public.v_fact_band_outcome_clean (band_id uuid, city_key text, for_date date, settled_yes boolean);
    create table public.v_canonical_bands (band_id uuid, market_id uuid, band_lo numeric, band_hi numeric);
    create table public.v_canonical_markets (market_id uuid, unit text);

    insert into public.v_canonical_markets values
      ('${m(1)}', 'C'), ('${m(2)}', 'C'), ('${m(3)}', 'F'), ('${m(4)}', 'F'), ('${m(5)}', 'C'), ('${m(6)}', 'C');
    insert into public.v_canonical_bands values
      ('${b(1)}', '${m(1)}', 22, 23),     -- london 20 Sep, won
      ('${b(2)}', '${m(2)}', 22, 23),     -- london 21 Sep, won
      ('${b(3)}', '${m(3)}', 72, 74),     -- nyc 20 Sep, won
      ('${b(4)}', '${m(4)}', 80, 82),     -- nyc 21 Sep, won
      ('${b(5)}', '${m(5)}', 30, null),   -- paris 20 Sep, the upper tail won
      ('${b(6)}', '${m(6)}', 10, 11),     -- oslo 20 Sep, one of two winners
      ('${b(7)}', '${m(6)}', 11, 12);     -- oslo 20 Sep, the other
    insert into public.v_fact_band_outcome_clean values
      ('${b(1)}', 'london', '2026-09-20', true), ('${b(2)}', 'london', '2026-09-21', true),
      ('${b(3)}', 'nyc', '2026-09-20', true), ('${b(4)}', 'nyc', '2026-09-21', true),
      ('${b(5)}', 'paris', '2026-09-20', true),
      ('${b(6)}', 'oslo', '2026-09-20', true), ('${b(7)}', 'oslo', '2026-09-20', true);
    insert into public.v_verified_weather_outcomes values
      ('london', '2026-09-20', 22.0),                     -- in the winner's bucket
      ('london', '2026-09-21', 23.0),                     -- one bucket above the winner
      ('nyc', '2026-09-20', ${(73 - 32) * 5 / 9}),         -- 73 F, in [72, 74)
      ('rome', '2026-09-20', 18.0);                       -- no winner confirmed yet
  `);
  await db.exec(MIG);
  await db.exec(MIG);                                     // re-runnable

  const rows = (await db.query(
    `select city_key, for_date::text as d, label_c, source from public.v_venue_truth order by 1, 2`)).rows;
  const got = Object.fromEntries(rows.map(r => [`${r.city_key} ${r.d}`, [Number(r.label_c), r.source]]));
  const near = (k, v, src) => {
    assert.ok(got[k], `${k} missing`);
    assert.ok(Math.abs(got[k][0] - v) < 1e-9, `${k}: ${got[k][0]} != ${v}`);
    assert.equal(got[k][1], src, `${k} source`);
  };
  near('london 2026-09-20', 22.0, 'venue_reading');
  near('london 2026-09-21', 22.0, 'winning_bucket');      // the settlement is the authority
  near('nyc 2026-09-20', (73 - 32) * 5 / 9, 'venue_reading');
  near('nyc 2026-09-21', (80.5 - 32) * 5 / 9, 'winning_bucket');
  near('rome 2026-09-20', 18.0, 'venue_reading');
  assert.equal(got['paris 2026-09-20'], undefined, 'a tail names no value');
  assert.equal(got['oslo 2026-09-20'], undefined, 'two winners give no bucket label');
  assert.equal(rows.length, 5);

  const can = async (role) => (await db.query(
    `select has_table_privilege($1, 'public.v_venue_truth', 'select') as ok`, [role])).rows[0].ok;
  assert.equal(await can('service_role'), true);
  assert.equal(await can('anon'), false, 'the browser does not read the training truth');
  assert.equal(await can('authenticated'), false);

  console.log('PASS: venue-truth: one label a city-day, the venue reading only inside the settled bucket, '
    + 'the bucket midpoint otherwise, no label from a tail or two winners, service role only, re-runnable');
})().catch(e => { console.error(e); process.exit(1); });
