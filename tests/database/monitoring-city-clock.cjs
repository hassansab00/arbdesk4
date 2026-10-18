const {PGlite}=require('@electric-sql/pglite');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'../..');
const migration=fs.readFileSync(path.join(root,'supabase/migrations/20261018171559_monitoring_city_clocks_and_daily_maximum.sql'),'utf8');
(async()=>{
 const db=new PGlite(), q=async sql=>(await db.query(sql)).rows;
 await db.exec(`create role anon;create role authenticated;create role service_role;
 create table cities(city_key text primary key,timezone text);
 insert into cities values('east','Pacific/Kiritimati'),('west','Pacific/Honolulu');
 create table weather_forecasts(forecast_id bigint,city_key text,for_date date,forecast_max_c numeric,model text,run_at timestamptz,lead_days int);
 create table derived_city_climate(city_key text,local_today date);
 insert into derived_city_climate select city_key,(now() at time zone timezone)::date-1 from cities;
 insert into weather_forecasts select 1,c.city_key,(now() at time zone c.timezone)::date-1,99,'yesterday',now(),0 from cities c;
 insert into weather_forecasts select 2,c.city_key,(now() at time zone c.timezone)::date,23,'short-lead',now()-interval '1 hour',0 from cities c;
 insert into weather_forecasts select 3,c.city_key,(now() at time zone c.timezone)::date,40,'long-lead',now(),7 from cities c;
 grant select on cities,weather_forecasts to anon,authenticated,service_role;
 create table v_city_temp_trend(city_key text,latest_temp_c numeric,latest_at timestamptz,latest_local_hour numeric,running_max_c numeric,
 slope_3_c_per_h numeric,slope_6_c_per_h numeric,direction text,rolling_over boolean,below_running_max_c numeric,reading_age_min numeric,n_readings int);
 insert into v_city_temp_trend values('east',20,now(),18,30.123456,0,0,'FALLING',true,10,0,30),('west',20,now(),10,21,0,0,'RISING',false,1,0,30);
 create table v_city_climb_profile(city_key text,local_hour int,typical_climb_left_c numeric,climb_left_sd_c numeric,
 climb_left_p10_c numeric,climb_left_p90_c numeric,pct_already_peaked numeric,n_days int);
 insert into v_city_climb_profile values('east',18,0.1,0.2,0,0.4,95,30),('west',10,5,1,2,8,10,30);
 `);
 // Preserve the production view's complete result descriptor when replacing it.
 await db.exec(`
 alter table cities add column display_name text,add column icao text,add column unit text,add column latitude numeric,add column longitude numeric;
 alter table derived_city_climate add column baseline text,add column baseline_days int,add column normal_max_c numeric,add column sd_max_c numeric;
 create table weather_observations(city_key text,temp_c numeric,valid_at timestamptz);
 create table derived_forecast_skill(city_key text,mae_c numeric,bias_c numeric,n_days int,lead_days int,computed_at timestamptz);
 create table derived_capacity(city_key text,usd_at_5c numeric,usd_full numeric,live_bands int,computed_at timestamptz);
 create table v_forecast_divergence_current(city_key text,spread_c numeric,n_models int,sigma_multiplier numeric(6,4),for_date date);
 create table v_opportunities(city_key text,tradeable boolean,edge_net_pp numeric);
 create table v_city_volume(city_key text,volume_usd numeric,n_trades int);
 create table derived_weather_peak(city_key text,month int,peak_hour_local numeric,window_width_h int);
 create table live_weather(city_key text,temp_c numeric,running_max_c numeric,peak_window_state text,day_decided boolean,observed_at timestamptz);
 `);
 const shape=[
 ['city_key','text'],['display_name','text'],['icao','text'],['timezone','text'],['unit','text'],['latitude','numeric'],['longitude','numeric'],
 ['baseline','text'],['baseline_days','integer'],['normal_max_c','numeric'],['volatility_c','numeric'],['now_c','numeric'],['running_max_c','numeric'],
 ['forecast_max_c','numeric'],['forecast_model','text'],['anomaly_c','numeric(6,2)'],['hotness_sigma','numeric'],['mae_c','numeric'],['bias_c','numeric'],
 ['skill_days','integer'],['model_spread_c','numeric'],['n_models','integer'],['sigma_multiplier','numeric(6,4)'],['volume_24h','numeric'],
 ['n_trades_24h','integer'],['depth_5c','numeric'],['live_bands','integer'],['n_tradeable','integer'],['best_edge_pp','numeric'],['avg_edge_pp','numeric(8,5)'],
 ['peak_hour_local','numeric'],['window_width_h','integer'],['peak_window_state','text'],['day_decided','boolean'],['observed_at','timestamp with time zone'],
 ['forecast_lead_days','integer'],['forecast_at','timestamp with time zone'],['observed_max_3d_c','numeric'],['forecast_suspect','boolean']];
 await db.exec('create view v_city_stats as select '+shape.map(([name,type])=>`null::${type} as ${name}`).join(',')+' where false;grant select on v_city_stats to anon');
 const descriptor=()=>q("select attname,format_type(atttypid,atttypmod) type from pg_attribute where attrelid='v_city_stats'::regclass and attnum>0 order by attnum");
 const before=await descriptor();
 const source=fs.readFileSync(path.join(root,'sql/ad4_28_feature_cache.sql'),'utf8');
 const a=source.indexOf('create or replace view v_city_peak_approach as');
 await db.exec(source.slice(a,source.indexOf(';',a)+1));
 await db.exec(migration);await db.exec(migration);
 assert.deepEqual(await descriptor(),before);
 assert.equal((await q("select forecast_max_c from v_city_stats where city_key='east'"))[0].forecast_max_c,'23');
 const rows=await q(`select f.*, f.for_date::text target_date, (now() at time zone c.timezone)::date::text expected from v_city_forecast_today f join cities c using(city_key) order by city_key`);
 assert.equal(rows.length,2);assert.notEqual(String(rows[0].for_date),String(rows[1].for_date));
 for(const r of rows){assert.equal(r.target_date,r.expected);assert.equal(r.model,'short-lead');assert.equal(r.forecast_max_c,'23');}
 await db.exec("set timezone='America/New_York'");
 assert.equal((await q("select model from v_city_forecast_today where city_key='east'"))[0].model,'short-lead');
 const peak=await q('select city_key,implied_max_c,implied_max_low_c,implied_max_high_c from v_city_peak_approach order by city_key');
 assert.deepEqual(peak,[{city_key:'east',implied_max_c:'30.123456',implied_max_low_c:'30.123456',implied_max_high_c:'30.123456'},
 {city_key:'west',implied_max_c:'25.00',implied_max_low_c:'22.00',implied_max_high_c:'28.00'}]);
 await db.exec('set role anon');assert.equal((await q('select * from v_city_forecast_today')).length,2);
 await assert.rejects(q("insert into weather_forecasts(forecast_id) values(9)"),/permission denied/);
 await db.close();console.log('PASS: city-local forecast selection ignores stale climate dates, chooses shortest lead, preserves future warming and observed daily floors');
})().catch(e=>{console.error(e);process.exit(1)});
