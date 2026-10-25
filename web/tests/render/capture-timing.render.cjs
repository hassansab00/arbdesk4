// Real browser acceptance for the reporting-only timing filter.
// Run from web/: node tests/render/capture-timing.render.cjs
const assert = require('node:assert/strict');
const {execFileSync,spawn} = require('node:child_process');
const path = require('node:path');
const {fakePostgrest} = require('../fakePostgrest.cjs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const WEB = path.join(__dirname,'../..');
const city = {city_key:'tokyo',display_name:'Tokyo',timezone:'Asia/Tokyo',unit:'C',icao:'RJTT',
  latitude:35.55,longitude:139.75,status:'active'};
const score = {n:2,hits:1,hit_rate:.5,interval95:[.1,.9],dates:2,pending:0,verified:2,legacy:0,
  calibration_gap:.2,metrics:{log_loss:1,brier:.6},baseline:{n:0,hit:null,log_loss:null},raw:{n:0,hit:null,log_loss:null},
  versions:{'fixture':2},recent_dates:[]};
const call = {date:'2026-10-24',checkpoint:'noon',hit:0,scheduled_local:'2026-10-24T12:00:00+09:00',
  received_at:'2026-10-24T03:37:00Z',frozen_at:'2026-10-24T03:37:00Z',delay_minutes:37,
  cutoff_local:'2026-10-24T14:00:00+09:00',before_expected_peak:true,reason:'eligible'};
const report = {...score,city:'tokyo',display_name:'Tokyo',timezone:'Asia/Tokyo',scope:'served',horizon:'noon',
  period_start:'2026-10-01',period_end:'2026-10-25',learning:[],why:{missed_dates:1,reviewed_dates:0,weather:{},
    mean_peak_shift_h:null,public_forecast_mae_c:null,public_forecast_bias_c:null},
  capture_timing:{policy:'before-expected-peak-v1',calls:2,eligible_calls:1,excluded_calls:1,
    exclusions:{at_or_after_expected_peak:1},strict:{...score,n:1,hits:0,hit_rate:0,dates:1},
    recent_calls:[call,{...call,date:'2026-10-23',hit:1,before_expected_peak:false,reason:'at_or_after_expected_peak'}]}};

(async()=>{
  const fake=await fakePostgrest({port:54333,tables:{cities:[city],v_city_prediction_validation:[
    {city_key:'tokyo',checkpoint:'noon',scope:'served',as_of:new Date().toISOString(),report}]}});
  const env={...process.env,NEXT_PUBLIC_SUPABASE_URL:fake.url,NEXT_PUBLIC_SUPABASE_ANON_KEY:'sb_publishable_fixture',NEXT_TELEMETRY_DISABLED:'1'};
  let server,browser;
  try {
    if (!process.env.SKIP_BUILD) execFileSync(path.join(WEB,'node_modules/.bin/next'),['build'],{cwd:WEB,env,stdio:'inherit'});
    server=spawn(path.join(WEB,'node_modules/.bin/next'),['start','-p','3111','-H','127.0.0.1'],{cwd:WEB,env,stdio:'ignore'});
    browser=await chromium.launch({headless:true,
      ...(process.env.BROWSER_EXECUTABLE_PATH ? {executablePath:process.env.BROWSER_EXECUTABLE_PATH} : {})});
    const end=Date.now()+60000;
    while(Date.now()<end) {
      try {if((await fetch('http://127.0.0.1:3111/predictive')).ok) break;} catch {}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    const page=await browser.newPage({timezoneId:'America/New_York'});
    const errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.goto('http://127.0.0.1:3111/predictive',{waitUntil:'networkidle'});
    await page.getByLabel('Accuracy horizon').selectOption('noon');
    const panel=page.getByRole('heading',{name:'City accuracy · original frozen calls'}).locator('xpath=ancestor::section[1]');
    await panel.getByText('1/2 correct · 50.0%',{exact:false}).waitFor();
    await page.getByLabel('Capture timing').selectOption('strict');
    await panel.getByText('0/1 correct · 0.0%',{exact:false}).waitFor();
    await panel.getByText('Freeze, learning and validation',{exact:true}).click();
    await panel.getByText('37.0 min',{exact:true}).first().waitFor();
    const text=await panel.innerText();
    assert.match(text,/1\/2 calls qualify · 1 excluded/);
    assert.match(text,/At \/ after expected peak · hit/,'the excluded hit remains in the audit');
    assert.match(text,/24 Oct,? 12:37/,'capture is Tokyo local even with a New York browser');
    assert.match(text,/Expected peak is an estimate/);
    assert.deepEqual(errors,[]);
    await page.getByLabel('Capture timing').selectOption('all');
    await panel.getByText('1/2 correct · 50.0%',{exact:false}).waitFor();
    console.log('PASS: timing selector changes the scored cohort, preserves excluded calls, and displays server freeze and delay on the city clock.');
  } finally {
    if(browser) await browser.close();
    if(server) server.kill();
    await fake.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
