// ===========================================================================
// THE EARLY RECORD, QUARANTINED - NOT DELETED (plan v2 P4.4).
// v_fact_band_outcome_clean is what every learner and scorer reads: a band
// banked before 13 Sep appears only with the venue's confirmed answer and no
// observed maximum; an excluded band is absent; fact_band_outcome itself is
// untouched. The rebuild is idempotent and the two new records append-only.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260924010000_quarantine_the_early_record.sql');

async function refused(db, sql, why) {
  try { await db.query(sql); } catch (e) { return e.message; }
  assert.fail(`accepted, and should not have been: ${why}`);
}

const LATE = '11111111-0000-0000-0000-000000000001';      // banked 14 Sep
const EARLY_OK = '11111111-0000-0000-0000-000000000002';  // banked 30 Aug, venue confirmed NO
const EARLY_OPEN = '11111111-0000-0000-0000-000000000003'; // banked 30 Aug, venue not confirmed
const EXCLUDED = '11111111-0000-0000-0000-000000000004';  // banked 14 Sep, then excluded
const M1 = '22222222-0000-0000-0000-000000000001';
const M2 = '22222222-0000-0000-0000-000000000002';

(async () => {
  const db = new PGlite();
  // The live shapes the migration reads (types from information_schema, 24 Sep).
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.fact_band_outcome (band_id uuid primary key, city_key text, for_date date,
      band_lo numeric, band_hi numeric, open_low boolean, open_high boolean, model_prob numeric,
      sigma_c numeric, confidence numeric, regime_label text, forecast_max_c numeric,
      market_price numeric, edge_net_pp numeric, volume_usd numeric, depth_5c numeric,
      priced_at timestamptz, observed_max_c numeric, settled_yes boolean, captured_at timestamptz,
      obs_source text);
    create table public.bands (band_id uuid primary key, market_id uuid);
    create table public.v_venue_band_resolution (band_id uuid, market_id uuid, settled_yes boolean,
      resolution_state text, confirmed_at timestamptz);
    create table public.v_venue_market_resolution (market_id uuid, resolution_state text);
    create table public.v_band_outcome_coherence (city_key text, for_date date, coherent boolean);
    create table public.signals (signal_id bigint primary key, payload jsonb);
    create table public.fact_signal_outcome (signal_id bigint, strategy_id text, band_id uuid,
      city_key text, for_date date, side text, action text, reason text, fired_at timestamptz,
      severity text, price_at_fire numeric, prob_at_fire numeric, edge_at_fire numeric, status text,
      filled boolean, fill_price numeric, shares numeric, settled_yes boolean, gross_pnl numeric,
      net_pnl numeric, slippage_c numeric, captured_at timestamptz, signal_correct boolean,
      settled_at timestamptz, outcome_source text, forecast_version uuid,
      calibration_version uuid, cost_version uuid);

    insert into public.fact_band_outcome (band_id, city_key, for_date, observed_max_c, settled_yes, captured_at, obs_source) values
      ('${LATE}',       'nyc', '2026-09-13', 24.0, true,  '2026-09-14 06:00+00', 'authority'),
      ('${EARLY_OK}',   'la',  '2026-08-27', 31.0, true,  '2026-08-30 06:00+00', 'desk'),
      ('${EARLY_OPEN}', 'la',  '2026-08-28', 30.0, true,  '2026-08-30 06:00+00', 'desk'),
      ('${EXCLUDED}',   'nyc', '2026-09-13', 24.0, false, '2026-09-14 06:00+00', 'authority');
    insert into public.bands values ('${LATE}', '${M1}'), ('${EARLY_OK}', '${M1}'),
      ('${EARLY_OPEN}', '${M2}'), ('${EXCLUDED}', '${M1}');
    insert into public.v_venue_market_resolution values ('${M1}', 'confirmed'), ('${M2}', 'partial');
    insert into public.v_venue_band_resolution values
      ('${LATE}', '${M1}', true, 'confirmed', now()), ('${EARLY_OK}', '${M1}', false, 'confirmed', now()),
      ('${EARLY_OPEN}', '${M2}', true, 'confirmed', now()), ('${EXCLUDED}', '${M1}', false, 'confirmed', now());
    insert into public.v_band_outcome_coherence values ('nyc', '2026-09-13', true), ('la', '2026-08-27', true);
  `);
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));
  // Idempotent: a second run of the whole migration adds nothing and changes nothing.
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));
  assert.equal((await db.query('select public.rebuild_early_band_outcomes() n')).rows[0].n, 0);

  await db.query(`insert into public.fact_band_outcome_exclusions (band_id, reason)
                  values ('${EXCLUDED}', 'test: known wrong')`);

  const clean = Object.fromEntries((await db.query(
    'select band_id, settled_yes, observed_max_c, obs_source, outcome_provenance from public.v_fact_band_outcome_clean')).rows
    .map((r) => [r.band_id, r]));
  assert.deepEqual(Object.keys(clean).sort(), [LATE, EARLY_OK].sort(),
    'late rows stay, an early row stays only where the venue confirmed band AND market, excluded rows go');
  assert.equal(clean[LATE].settled_yes, true);
  assert.equal(Number(clean[LATE].observed_max_c), 24);
  assert.equal(clean[LATE].outcome_provenance, 'recorded');
  assert.equal(clean[EARLY_OK].settled_yes, false, "the venue's answer, not the desk's");
  assert.equal(clean[EARLY_OK].observed_max_c, null, 'the faulty reading is withheld');
  assert.equal(clean[EARLY_OK].obs_source, null);
  assert.equal(clean[EARLY_OK].outcome_provenance, 'venue_rebuilt');

  // Nothing deleted or edited: the raw record still says what the desk believed.
  const raw = (await db.query(`select count(*)::int n, bool_and(settled_yes) filter (where band_id = '${EARLY_OK}') desk
                               from public.fact_band_outcome`)).rows[0];
  assert.equal(raw.n, 4);
  assert.equal(raw.desk, true);

  // The learners read the clean record.
  const ver = (await db.query('select band_id from public.v_verified_fact_band_outcome order by band_id')).rows
    .map((r) => r.band_id);
  assert.deepEqual(ver, [LATE, EARLY_OK].sort());
  await db.query(`insert into public.fact_signal_outcome (signal_id, band_id, action, side) values
                  (1, '${EARLY_OPEN}', 'ENTER', 'YES'), (2, '${EARLY_OK}', 'ENTER', 'YES')`);
  const sig = Object.fromEntries((await db.query('select signal_id, settled_yes, outcome_source from public.v_signal_outcome')).rows
    .map((r) => [r.signal_id, r]));
  assert.equal(sig[1].settled_yes, null, 'an early band the venue never confirmed does not settle a signal');
  assert.equal(sig[1].outcome_source, 'not settled yet');
  assert.equal(sig[2].settled_yes, false);

  // Append-only, service role only.
  for (const t of ['fact_band_outcome_exclusions', 'fact_band_outcome_venue_rebuilt']) {
    await refused(db, `update public.${t} set band_id = band_id`, `an edit of ${t}`);
    await refused(db, `delete from public.${t}`, `a delete from ${t}`);
    const g = (await db.query(`select has_table_privilege('anon','public.${t}','select') a,
        has_table_privilege('service_role','public.${t}','insert') s,
        has_table_privilege('service_role','public.${t}','update') u`)).rows[0];
    assert.deepEqual([g.a, g.s, g.u], [false, true, false], t);
  }
  const f = (await db.query(`select has_function_privilege('anon','public.rebuild_early_band_outcomes()','execute') a,
      has_function_privilege('service_role','public.rebuild_early_band_outcomes()','execute') s`)).rows[0];
  assert.deepEqual([f.a, f.s], [false, true]);
  assert.equal((await db.query(`select has_table_privilege('anon','public.v_fact_band_outcome_clean','select') a`)).rows[0].a, true);

  console.log('early-record-quarantine: early rows carry the venue answer or nothing, exclusions hold, nothing deleted, rebuild idempotent');
})().catch((e) => { console.error(e); process.exit(1); });
