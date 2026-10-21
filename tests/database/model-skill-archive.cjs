const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

(async () => {
  const db = new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
    create table public.proprietary_data_manifests(dataset text);`);
  const shape = read('sql/ad4_49_model_skill.sql').match(/create table if not exists derived_forecast_skill_model \([\s\S]*?\n\);/)[0];
  await db.exec(shape);
  await db.exec(`alter table public.derived_forecast_skill_model enable row level security;
    grant select on public.derived_forecast_skill_model to service_role;`);
  const sql = read('sql/ad4_model_skill_archive.sql');
  await db.exec(sql); await db.exec(sql);
  for (const [city, model, lead, age, scope, mae] of [
    ['a','wx',1,20,'verified',1], ['a','wx',1,10,'verified',2],
    ['a','wx',1,30,'legacy',3], ['a','wx',1,25,'legacy',4],
    ['a','wx',1,35,null,5], ['a','wx',1,28,null,6],
    ['b','wx',1,100,'verified',7], ['a','wx',2,8,'verified',8],
    ['a','wx',2,1,'verified',9], ['a','gfs',1,99,'verified',10]
  ]) await db.query(`insert into public.derived_forecast_skill_model
    (city_key,model,lead_days,computed_at,evidence_scope,mae_c)
    values($1,$2,$3,now()-make_interval(days=>$4),$5,$6)`, [city,model,lead,age,scope,mae]);
  const one = async q => (await db.query(q)).rows[0];
  const prune = async args => (await one(`select public.prune_model_skill(${args}) r`)).r;
  const all = async () => (await db.query('select to_jsonb(s) r from derived_forecast_skill_model s order by city_key,model,lead_days,computed_at')).rows;
  const latest = async () => (await db.query(`select to_jsonb(s) r from(
    select distinct on(city_key,model,lead_days,evidence_scope) * from derived_forecast_skill_model
    order by city_key,model,lead_days,evidence_scope,computed_at desc) s`)).rows;
  const before = await all(), newest = await latest();
  assert.equal((await prune('6,true')).ok, false);
  assert.equal((await prune('7,null')).ok, false);
  assert.equal((await prune('7,false')).ok, false);
  assert.equal((await prune('7,true')).would_delete, 4);
  assert.equal((await prune('7,false,null,5')).ok, false);
  await db.exec("begin; insert into proprietary_data_manifests values('derived_forecast_skill_model');");
  assert.equal((await prune('7,false,null,4')).ok, false);
  await db.exec('rollback;');
  await db.exec(`create function mutate_latest() returns trigger language plpgsql as $$ begin
    update derived_forecast_skill_model set mae_c=999 where city_key='b'; return null; end $$;
    create trigger mutate_latest after delete on derived_forecast_skill_model
      for each statement execute function mutate_latest();`);
  await assert.rejects(prune('7,false,null,4'), /latest full rows changed/);
  assert.deepEqual(await all(), before);
  await db.exec('drop trigger mutate_latest on derived_forecast_skill_model;');
  const offered = (await db.query('select skill_key from v_prunable_model_skill order by skill_key')).rows;
  assert.equal(new Set(offered.map(x=>x.skill_key)).size, offered.length);
  for (const role of ['anon','authenticated']) {
    await db.exec(`set role ${role}`);
    await assert.rejects(db.query('select * from v_prunable_model_skill'));
    await assert.rejects(db.query('select prune_model_skill(7,true)'));
    await db.exec('reset role');
  }
  await db.exec('set role service_role;');
  assert.equal((await one('select count(*)::int n from v_prunable_model_skill')).n, 4);
  await db.exec('reset role;');
  assert.equal((await prune('7,false,null,4')).deleted, 4);
  assert.deepEqual(await latest(), newest);
  assert.equal((await prune('7,true')).would_delete, 0);
  assert.equal((await all()).length, 6);
  await db.close();
  console.log('model-skill-archive: exact count, signed-dataset refusal, latest full rows per scope, rollback and role contracts passed');
})().catch(e => { console.error(e); process.exit(1); });
