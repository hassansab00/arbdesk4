// Wholly synthetic records; real anonymous HTTP reads from existing public views.
const assert=require('node:assert/strict');
const {fakePostgrest}=require('./fakePostgrest.cjs');
const {createClient}=require('@supabase/supabase-js');
const {selectedFrozenCall,readFrozenCalls}=require('../.route-test/lib/frozenCalls.js');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const Module=require('module'),oldResolve=Module._resolveFilename;
Module._resolveFilename=function(name,...args){
  if(name.startsWith('@/')) name=require('node:path').join(__dirname,'../.route-test',name.slice(2));
  return oldResolve.call(this,name,...args);
};
const Panel=require('../.route-test/components/FrozenCallPanel.js').default;
const evening={city_key:'fixture',target_date:'2001-06-02',checkpoint:'d1_eve',model_family:'engine',
  blind:false,withheld:false,top_label:'Synthetic warm bucket',top_prob:.6,as_of:'2001-06-02T01:37:00Z'};
const morning={...evening,checkpoint:'morning',top_label:'Synthetic cool bucket',as_of:'2001-06-02T16:37:00Z'};
(async()=>{
  for(const fail of [new Set(),new Set(['v_prediction_lineup']),new Set(['v_city_hit_history'])]){
    const db=await fakePostgrest({fail,tables:{v_prediction_lineup:[morning,evening,
      {...evening,model_family:'engine_variant',top_label:'Shadow must not replace served'},
      {...evening,city_key:'hidden',withheld:true},{...evening,city_key:'blind',blind:true},
      {...evening,city_key:'other'},{...evening,target_date:'2001-06-03'}],
      v_city_hit_history:[{city_key:'fixture',for_date:'2001-06-02',model_call:'Synthetic day-ahead bucket',model_call_prob:.4,called_at:'2001-06-02T06:00:00Z'}]}});
    try{
      const client=createClient(db.url,'synthetic-anon-key',{auth:{persistSession:false,autoRefreshToken:false}});
      for(const moment of ['d1_eve','morning','noon','day_ahead']){
        const result=await readFrozenCalls(client,moment,['2001-06-02']);
        const broken=fail.has(moment==='day_ahead'?'v_city_hit_history':'v_prediction_lineup');
        if(broken){assert(result.error);assert.equal(result.data,null);continue;}
        assert.equal(result.error,null);
        const chosen=selectedFrozenCall(result.data,'fixture','2001-06-02',moment);
        if(moment==='noon') assert.equal(chosen,null,'no fallback to another checkpoint or live price');
        else assert.equal(chosen.top_label,moment==='day_ahead'?'Synthetic day-ahead bucket':
          moment==='d1_eve'?'Synthetic warm bucket':'Synthetic cool bucket');
        for(const city of ['missing','blind','hidden']) assert.equal(selectedFrozenCall(result.data,city,'2001-06-02',moment),null);
        assert.equal(selectedFrozenCall(result.data,'fixture','2001-05-31',moment),null);
        const html=renderToStaticMarkup(React.createElement(Panel,{call:chosen,moment,
          timezone:'America/Los_Angeles',loading:false,error:null}));
        if(moment==='d1_eve'){
          assert.match(html,/Frozen pick/);assert.match(html,/18:37/);assert.match(html,/America\/Los_Angeles/);
          assert.doesNotMatch(html,/Synthetic cool bucket/);assert.doesNotMatch(html,/Server freeze:/);
        }
        if(moment==='day_ahead') assert.match(html,/Recorded day-ahead pick/);
        if(moment==='noon') assert.match(html,/No saved call/);
      }
      assert(db.requests.every(r=>r.method==='GET'),'never capture, reprice or overwrite');
      assert(db.requests.every(r=>['/rest/v1/v_prediction_lineup','/rest/v1/v_city_hit_history'].includes(r.path)),
        'only existing public views; no private tables or new privileged route');
      const count=db.requests.length;
      assert((await readFrozenCalls(client,'unknown',['2001-06-02'])).error);
      assert.deepEqual(await readFrozenCalls(client,'noon',[]),{data:[],error:null});
      assert.equal(db.requests.length,count);
    }finally{await db.close();}
  }
  const rows=Array.from({length:1001},(_,i)=>({...evening,city_key:`fixture${i}`}));
  const db=await fakePostgrest({tables:{v_prediction_lineup:rows}});
  try{
    const client=createClient(db.url,'synthetic-anon-key',{auth:{persistSession:false,autoRefreshToken:false}});
    const result=await readFrozenCalls(client,'d1_eve',['2001-06-02']);
    assert.equal(result.error,null);assert.equal(result.data.length,1001,'all pages are read');
    assert.equal(db.requests.length,2);
  }finally{await db.close();}
  const original={city_key:'fixture',target_date:'2001-06-02',checkpoint:'noon',checkpoint_id:'a',
    top_label:'Original miss',top_prob:.8,decided_at:'2001-06-02T12:01:00Z'};
  assert.equal(selectedFrozenCall([{...original,checkpoint_id:'later',top_label:'Later correct call',
    decided_at:'2001-06-02T12:30:00Z'},original],'fixture','2001-06-02','noon').top_label,'Original miss');
  console.log('PASS: public original-call views, horizon/date isolation, local recorded times, failures and pagination');
})().catch(e=>{console.error(e);process.exit(1);});
