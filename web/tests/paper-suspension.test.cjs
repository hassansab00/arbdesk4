// Entirely synthetic fixtures: no captured trades or operational measurements.
const assert=require('node:assert/strict');
const path=require('node:path');
const {fakePostgrest}=require('./fakePostgrest.cjs');
const out=path.join(__dirname,'../.route-test');
const lib=require(path.join(out,'lib/paperDesks.js'));
const route=require(path.join(out,'app/api/paper-desk/route.js'));

const account={account_id:'fixture-desk',name:'Fixture desk',strategy_id:'fixture-strategy',
  access_mode:'single_desk',owner_id:null,mode:'automatic',entries_paused:false,status:'active',
  archived_at:null,retired_at:null,created_at:'2001-01-01T00:00:00Z',
  policy:{cities:['ALL'],strategies:['fixture-strategy']}};
const strategy={strategy_id:'fixture-strategy',name:'Fixture strategy',enabled:false};
const state={strategy_id:strategy.strategy_id,state:'suspended',since:'2001-01-02T00:00:00Z',
  reason:'Synthetic loss-guard test reason'};
const trade={trade_id:'fixture-trade',account_id:account.account_id,
  opened_at:'2001-01-01T00:00:00Z',closed_at:'2001-01-02T00:00:00Z',net_pnl:0};
const decision={strategy_id:strategy.strategy_id,action:'BUY',reason_code:'enter'};

(async()=>{
  const activity=lib.deskActivity([account],[trade],[],[strategy],[decision],[state])[0];
  assert.equal(activity.strategy_state,'suspended');
  assert.equal(activity.strategy_state_since,state.since);
  assert.equal(activity.strategy_state_reason,state.reason);
  assert.equal(activity.decisions_24h.buys,1,'blocked BUY decisions remain visible');
  assert.equal(activity.trade_count,1,'a proposal never manufactures a fill');
  assert.equal(lib.strategyPhrase(activity),'suspended 2 Jan 2001');
  const view=lib.deskState({...account,activity},1);
  assert.equal(view.key,'PAUSED');
  assert.match(view.line,/Synthetic loss-guard test reason/);
  assert.match(view.line,/BUY decisions are proposals, not filled trades/);
  assert.doesNotMatch(view.line,/Running|switch.*back on/);
  const states=new Map([[state.strategy_id,state]]);
  assert.equal(lib.strategyState(account,new Map([[strategy.strategy_id,{...strategy,enabled:true}]]),states).state,
    'on','an older state label does not override the execution enabled flag');
  assert.equal(lib.strategyState({...account,status:'retired'},new Map([[strategy.strategy_id,strategy]]),states).state,
    'retired','retirement remains authoritative');

  for(const key of Object.keys(process.env)) if(/SUPABASE/i.test(key)) delete process.env[key];
  const fs=require('node:fs'),os=require('node:os');
  process.env.PAPER_ARCHIVE_DIR=fs.mkdtempSync(path.join(os.tmpdir(),'synthetic-suspension-'));
  process.env.SUPABASE_SERVICE_KEY='synthetic-test-key';
  const tables={paper_accounts:[account],paper_trades:[trade],paper_positions:[],
    strategies:[strategy],decisions:[],strategy_state:[state]};
  for(const broken of [false,true]){
    const db=await fakePostgrest({tables,fail:new Set(broken?['strategy_state']:[])});
    try{
      process.env.NEXT_PUBLIC_SUPABASE_URL=db.url;
      const response=await route.GET(new Request('https://fixture.invalid/api/paper-desk?resource=accounts'));
      const body=await response.json();
      assert.equal(response.status,200);
      assert.equal(body.data.length,1,'state-read failures preserve the account list');
      if(broken){
        assert.equal(body.desks,null);
        assert.match(body.activity_error,/strategy_state failed/);
        assert.equal(body.data[0].activity,undefined,'no partial or invented activity');
      }else{
        assert.equal(body.activity_error,null);
        assert.equal(body.data[0].activity.strategy_state,'suspended');
        assert.equal(body.data[0].activity.strategy_state_reason,state.reason);
        assert.equal(body.data[0].activity.strategy_state_since,state.since);
      }
    }finally{await db.close();}
  }
  console.log('PASS: synthetic paper suspension fixtures preserve fills, explain blocked proposals, read provenance and retain accounts on failure');
})().catch(error=>{console.error(error);process.exit(1);});

assert.equal(lib.strategyRetestAt('2001-01-02T00:00:00Z'),'2001-01-16T00:00:00.000Z');
assert.equal(lib.strategyRetestAt(null),null);
assert.equal(lib.strategyRetestAt('invalid'),null);
assert.match(lib.paperExecutionText(null),/No execution result recorded/);
assert.equal(lib.paperExecutionText({skipped:{'strategy not switched on':4}}),'Skipped: 4 strategy not switched on');
assert.doesNotMatch(lib.paperExecutionText({skipped:{'strategy not switched on':4}}),/4.*filled/);
assert.equal(lib.paperExecutionText({published:2,queued:1,fills:{filled:1,rejected:1},blocked:{rail:1}}),
  '2 plans published · 1 plans queued · Fill results: 1 filled, 1 rejected · Blocked: 1 rail');
assert.match(lib.paperExecutionText({error:'fixture failure'}),/Error: fixture failure/);
