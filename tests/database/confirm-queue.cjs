// ===========================================================================
// THE LADDERS ARE ASKED MARKET BY MARKET (plan v2.2 P4.7, 30 Sep).
//
// record_market_confirmation_attempts keeps what the queue's backoff and the
// lag report need across runs: attempts counted, the unresolved streak reset
// by an answer, the FIRST time a whole ladder was seen resolved kept, the
// latest unresolved poll and the venue's latest closedTime. v_outcome_pipeline
// keeps pending markets in the denominator and names each state.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260930100000_the_ladders_are_asked_market_by_market.sql');

const M = (n) => `30000000-0000-0000-0000-0000000000${String(n).padStart(2, '0')}`;
const B = (n) => `20000000-0000-0000-0000-0000000000${String(n).padStart(2, '0')}`;

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
    create table public.cities(city_key text primary key, unit text, timezone text);
    create table public.markets(market_id uuid primary key, city_key text, resolution_date date,
      resolution_verified_at timestamptz);
    create table public.bands(band_id uuid primary key, market_id uuid);
    create table public.fact_band_outcome(band_id uuid primary key, city_key text, for_date date,
      settled_yes boolean, captured_at timestamptz default now());
    create table public.ingest_log(job text, status text, started_at timestamptz default now());
    create table public.clock_schedule(file text primary key);
    insert into public.clock_schedule values ('tick.yml'), ('pipeline_intraday.yml');
    create table public.clock_expected_jobs(file text references public.clock_schedule(file), job text,
      within_minutes int, note text, primary key (file, job));
    insert into public.cities values ('nyc', 'F', 'America/New_York'), ('tokyo', 'C', 'Asia/Tokyo'),
      ('la', 'F', 'America/Los_Angeles'), ('paris', 'C', 'Europe/Paris');
  `);
  const sql = fs.readFileSync(MIGRATION, 'utf-8');
  await db.exec(sql);
  await db.exec(sql);   // idempotent

  const rec = async (rows) => (await db.query(
    'select public.record_market_confirmation_attempts($1::jsonb) n', [JSON.stringify(rows)])).rows[0].n;
  const row = async (m) => (await db.query(
    'select * from public.market_confirmation_attempts where market_id = $1', [m])).rows[0];
  const base = { market_id: M(1), city_key: 'nyc', resolution_date: '2026-09-29',
    local_day_end: '2026-09-30T04:00:00Z', bands_total: 11, trigger: 'tick' };

  // 1. an unresolved answer starts the streak and marks the latest unresolved poll
  assert.equal(await rec([{ ...base, asked_at: '2026-09-30T04:36:00Z', outcome: 'not_closed', bands_proven: 0 }]), 1);
  let r = await row(M(1));
  assert.equal(r.attempts, 1);
  assert.equal(r.unresolved_streak, 1);
  assert.equal(r.first_all_resolved_seen_at, null);
  assert.equal(new Date(r.last_unresolved_at).toISOString(), '2026-09-30T04:36:00.000Z');

  // 2. a second unresolved answer grows the streak; the venue's closedTime is kept
  await rec([{ ...base, asked_at: '2026-09-30T05:36:00Z', outcome: 'partial', bands_proven: 4,
    venue_closed_at: '2026-09-30T05:15:00Z' }]);
  r = await row(M(1));
  assert.equal(r.attempts, 2);
  assert.equal(r.unresolved_streak, 2);
  assert.equal(r.bands_proven, 4);
  assert.equal(new Date(r.first_asked_at).toISOString(), '2026-09-30T04:36:00.000Z');

  // 3. the whole ladder resolved: the streak resets and the first sighting is stamped
  await rec([{ ...base, asked_at: '2026-09-30T06:36:00Z', outcome: 'resolved', bands_proven: 11,
    venue_closed_at: '2026-09-30T05:21:00Z' }]);
  r = await row(M(1));
  assert.equal(r.unresolved_streak, 0);
  assert.equal(new Date(r.first_all_resolved_seen_at).toISOString(), '2026-09-30T06:36:00.000Z');
  assert.equal(new Date(r.last_unresolved_at).toISOString(), '2026-09-30T05:36:00.000Z');
  assert.equal(new Date(r.venue_closed_at).toISOString(), '2026-09-30T05:21:00.000Z');

  // 4. a later answer never moves the first sighting, nor pulls closedTime or bands proven back
  await rec([{ ...base, asked_at: '2026-09-30T07:36:00Z', outcome: 'proven', bands_proven: 3,
    venue_closed_at: '2026-09-30T05:00:00Z' }]);
  r = await row(M(1));
  assert.equal(r.attempts, 4);
  assert.equal(new Date(r.first_all_resolved_seen_at).toISOString(), '2026-09-30T06:36:00.000Z');
  assert.equal(new Date(r.venue_closed_at).toISOString(), '2026-09-30T05:21:00.000Z');
  assert.equal(r.bands_proven, 11);

  // 5. a failure counts as unresolved for the backoff; a bad outcome is refused
  await rec([{ ...base, market_id: M(2), asked_at: '2026-09-30T08:36:00Z', outcome: 'failed', error: 'Timeout' }]);
  assert.equal((await row(M(2))).unresolved_streak, 1);
  await assert.rejects(rec([{ ...base, market_id: M(3), asked_at: '2026-09-30T08:36:00Z', outcome: 'made_up' }]));
  await assert.rejects(db.query("select public.record_market_confirmation_attempts('{}'::jsonb)"));

  // 6. only service_role may write or read: no access is widened
  const grants = (await db.query(`select has_function_privilege('anon', 'public.record_market_confirmation_attempts(jsonb)', 'execute') anon_x,
      has_function_privilege('service_role', 'public.record_market_confirmation_attempts(jsonb)', 'execute') svc_x,
      has_table_privilege('anon', 'public.market_confirmation_attempts', 'insert') anon_ins,
      has_table_privilege('anon', 'public.market_confirmation_attempts', 'select') anon_sel,
      has_table_privilege('anon', 'public.v_outcome_pipeline', 'select') anon_view,
      has_table_privilege('service_role', 'public.v_outcome_pipeline', 'select') svc_view`)).rows[0];
  assert.deepEqual(grants, { anon_x: false, svc_x: true, anon_ins: false, anon_sel: false, anon_view: false, svc_view: true });

  // 7. the pipeline view: every market of the window, pending ones included
  await db.exec(`
    insert into public.markets values
      ('${M(1)}', 'nyc',   current_date - 1, now() - interval '2 hours'),   -- proof complete, banked
      ('${M(2)}', 'la',    current_date - 2, null),                         -- asked, failed: awaiting venue
                                                                                -- (two days back: yesterday has not ended in LA before 07:00Z)
      ('${M(4)}', 'tokyo', current_date - 1, now() - interval '1 hour'),    -- proof complete, not banked
      ('${M(5)}', 'paris', current_date + 0, null),                         -- day not ended (for most of the day)
      ('${M(6)}', 'paris', current_date - 30, null);                        -- outside the window
    insert into public.bands values ('${B(1)}', '${M(1)}'), ('${B(2)}', '${M(1)}'), ('${B(4)}', '${M(4)}');
    insert into public.fact_band_outcome values ('${B(1)}', 'nyc', current_date - 1, true, now() - interval '30 minutes'),
      ('${B(2)}', 'nyc', current_date - 1, false, now() - interval '20 minutes');
    insert into public.ingest_log values ('refresh_page_cache', 'ok', now() - interval '10 minutes'),
      ('refresh_page_cache', 'ok', now() - interval '40 minutes');
  `);
  const v = Object.fromEntries((await db.query(
    'select market_id, state, bands_banked, banked_at is not null banked, page_refreshed_at, on_page, hours_day_end_to_banked_or_now h from public.v_outcome_pipeline'))
    .rows.map((x) => [x.market_id, x]));
  assert.equal(Object.keys(v).length, 4, 'the 30-day-old market is outside the window; the rest stay, pending too');
  assert.equal(v[M(1)].state, 'banked_not_on_page');     // no v_city_hit_history in this fixture
  assert.equal(v[M(1)].bands_banked, 2);
  assert.ok(v[M(1)].page_refreshed_at, 'the first page refresh after banking is found');
  assert.equal(v[M(2)].state, 'awaiting_venue');
  assert.ok(Number(v[M(2)].h) > 0, 'a pending market carries its hours so far');
  assert.equal(v[M(4)].state, 'confirmed_not_banked');
  assert.ok(['day_not_ended', 'awaiting_venue'].includes(v[M(5)].state));

  // 8. no expected-job rows yet: added a day after the code runs, or the
  // watchdog would count the dispatches before it as misses
  assert.equal((await db.query('select count(*)::int n from public.clock_expected_jobs')).rows[0].n, 0);

  console.log('PASS: confirmation attempts keep first sightings and count the backoff; the pipeline view keeps pending markets');
})().catch((e) => { console.error(e); process.exit(1); });
