// ===========================================================================
// THE OLD STRATEGIES RETIRE (plan v2 P8.2 step 5; Hassan, 29 Sep: "Retire all
// of s1, s3-s9"; 20260929080000_the_old_strategies_retire.sql).
//
// On the real lifecycle migration (strategy_state, its guard and the triggers
// that keep strategies.enabled in step):
//   - s1 and s3-s9 end `retired` and disabled, with the reason in
//     strategy_state_history; s2 and the engine strategies are untouched;
//   - s5 and s7 get the board's retirement stamp; a stamp already there (s1's
//     from 22 Sep) is kept as it was;
//   - a second run changes nothing (no second history row);
//   - retired is terminal: neither set_strategy_state nor flipping `enabled`
//     brings one back.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATIONS = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const read = (f) => fs.readFileSync(path.join(MIGRATIONS, f), 'utf-8');
const RETIRED = ['s1_buy_low_sell_signal', 's3_concentration', 's4_tail_fade', 's5_running_max_lock',
  's6_anchor_insurance', 's7_pre_peak_gradient', 's8_two_bucket_cover', 's9_ladder_basket'];

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create table public.strategies (strategy_id text primary key, name text, enabled boolean not null default false,
                                    extra jsonb);
    insert into public.strategies (strategy_id, name, enabled, extra) values
      ('s1_buy_low_sell_signal', 's1', true, '{"retired_on": "2026-09-22", "retired_record": "738 marked"}'),
      ('s2_combination_arb', 's2', true, null),
      ('s3_concentration', 's3', true, null), ('s4_tail_fade', 's4', true, null),
      ('s5_running_max_lock', 's5', true, '{"min_observation_trust": 0.80}'),
      ('s6_anchor_insurance', 's6', true, null), ('s7_pre_peak_gradient', 's7', true, null),
      ('s8_two_bucket_cover', 's8', true, null), ('s9_ladder_basket', 's9', true, null),
      ('s10_lock', 's10_lock', true, '{"origin": "engine"}'),
      ('system', 'system', false, null);`);
  await db.exec(read('20260924100000_strategy_lifecycle.sql'));
  const mig = read('20260929080000_the_old_strategies_retire.sql');
  await db.exec(mig);

  const rows = async () => Object.fromEntries((await db.query(
    `select s.strategy_id, s.enabled, st.state, s.extra from public.strategies s
       left join public.strategy_state st using (strategy_id)`)).rows.map((r) => [r.strategy_id, r]));
  let r = await rows();
  for (const id of RETIRED) {
    assert.equal(r[id].state, 'retired', id);
    assert.equal(r[id].enabled, false, id);
  }
  assert.equal(r.s2_combination_arb.state, 'shadow');
  assert.equal(r.s2_combination_arb.enabled, true);
  assert.equal(r.s10_lock.state, 'shadow');
  assert.equal(r.s10_lock.enabled, true);

  // the board's stamp: new for s5 and s7, kept for s1
  assert.equal(r.s5_running_max_lock.extra.retired_on, '2026-09-29');
  assert.equal(r.s5_running_max_lock.extra.min_observation_trust, 0.8, 's5 lost its own settings');
  assert.match(r.s7_pre_peak_gradient.extra.retired_record, /under 30/);
  assert.deepEqual(r.s1_buy_low_sell_signal.extra, { retired_on: '2026-09-22', retired_record: '738 marked' });
  assert.equal(r.s3_concentration.extra, null, 'a strategy without a stamp from this migration gained one');

  // every retirement is on the record, with Hassan's words
  const hist = (await db.query(
    `select strategy_id, from_state, to_state, reason from public.strategy_state_history where to_state = 'retired'`)).rows;
  assert.equal(hist.length, 8);
  assert.ok(hist.every((h) => h.from_state === 'shadow' && h.reason.includes('Retire all of s1, s3-s9')));

  // a second run changes nothing
  await db.exec(mig);
  const hist2 = (await db.query(`select count(*)::int as n from public.strategy_state_history`)).rows[0].n;
  const hist1 = (await db.query(`select count(*)::int as n from public.strategy_state_history where to_state <> 'retired'`)).rows[0].n + 8;
  assert.equal(hist2, hist1, 'a re-run wrote history');
  assert.deepEqual(await rows(), r);

  // retired is terminal
  await assert.rejects(db.query(`select public.set_strategy_state('s1_buy_low_sell_signal', 'shadow', 'undo')`),
    /retired -> shadow is not an allowed transition/);
  await assert.rejects(db.query(`update public.strategies set enabled = true where strategy_id = 's4_tail_fade'`),
    /retired -> shadow is not an allowed transition/);
  r = await rows();
  assert.equal(r.s4_tail_fade.enabled, false);

  console.log('PASS: old-strategies-retire: s1 and s3-s9 end retired and disabled with the reason on the record, s2 and the engine strategies untouched, s5/s7 stamped and s1\'s 22 Sep stamp kept, a re-run changes nothing, and neither set_strategy_state nor the switch brings one back');
})().catch((e) => { console.error(e); process.exit(1); });
