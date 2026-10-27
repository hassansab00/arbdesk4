const assert=require('node:assert/strict');
const {test}=require('node:test');
const {createClient}=require('@supabase/supabase-js');
const {createReadFetch,MAX_CONCURRENT_DB_READS}=require('../.route-test/lib/readFetch.js');
const turn=()=>new Promise(resolve=>setImmediate(resolve));

test('FIFO read queue caps concurrency, keeps every response and releases failed slots',async()=>{
  const started=[],pending=[];let active=0,peak=0;
  const read=createReadFetch((input,init)=>{
    started.push({input,init});peak=Math.max(peak,++active);
    return new Promise((resolve,reject)=>pending.push({done:(fail=false)=>{
      active--;if(fail)reject(new Error('Synthetic network failure'));else resolve(new Response(String(input)));
    }}));
  });
  const init={headers:{'x-test':'unchanged'}};
  const calls=Array.from({length:6},(_,i)=>read('https://synthetic.test/'+i,init));
  const outcomes=Promise.allSettled(calls);
  await turn();assert.equal(started.length,MAX_CONCURRENT_DB_READS);assert.equal(peak,2);
  for(let i=0;i<6;i++){
    assert.equal(started[i].input,'https://synthetic.test/'+i,'FIFO order');
    assert.equal(started[i].init,init,'request options are not rewritten');
    pending[i].done(i===1);await turn();assert(active<=2);
  }
  const results=await outcomes;
  assert.equal(results[1].status,'rejected');assert.match(results[1].reason.message,/Synthetic network/);
  for(const [i,r] of results.entries())if(i!==1)assert.equal(await r.value.text(),'https://synthetic.test/'+i);
  assert.equal(started.length,6,'a failed request is never retried');assert.equal(peak,2);
});

test('queued and already-aborted reads never reach the transport',async()=>{
  let release;const sent=[];
  const read=createReadFetch(input=>{sent.push(input);return new Promise(resolve=>{release=()=>resolve(new Response('ok'));});},1);
  const running=read('https://synthetic.test/running');await turn();
  const queuedController=new AbortController();
  const queued=read('https://synthetic.test/queued',{signal:queuedController.signal});
  const rejected=assert.rejects(queued,e=>e.name==='AbortError');queuedController.abort();await rejected;
  const prior=new AbortController();prior.abort();
  await assert.rejects(read(new Request('https://synthetic.test/prior',{signal:prior.signal})),e=>e.name==='AbortError');
  release();await running;await turn();
  assert.deepEqual(sent,['https://synthetic.test/running']);
  const next=read('https://synthetic.test/next');await turn();release();await next;
  assert.equal(sent.length,2,'cancelled jobs do not leak a slot');
});

test('active abort and synchronous fetch errors release the queue',async()=>{
  const controller=new AbortController(),started=[];
  const read=createReadFetch((input,init)=>{
    started.push(String(input));
    if(String(input).endsWith('/abort'))return new Promise((_,reject)=>init.signal.addEventListener('abort',()=>reject(init.signal.reason),{once:true}));
    if(String(input).endsWith('/throw'))throw new Error('Synthetic synchronous throw');
    return Promise.resolve(new Response('next'));
  },1);
  const first=read('https://synthetic.test/abort',{signal:controller.signal});
  const failed=assert.rejects(first,e=>e.name==='AbortError');
  const thrown=assert.rejects(read('https://synthetic.test/throw'),/Synthetic synchronous throw/);
  const last=read('https://synthetic.test/next');await turn();controller.abort();
  await failed;await thrown;assert.equal(await (await last).text(),'next');assert.equal(started.length,3);
});

test('HEAD uses the read budget; writes bypass it exactly once',async()=>{
  const pending=[],sent=[];
  const read=createReadFetch((input,init)=>{
    sent.push([input,init]);return new Promise(resolve=>pending.push(()=>resolve(new Response())));
  },1);
  const first=read(new Request('https://synthetic.test/head',{method:'HEAD'}));
  const second=read('https://synthetic.test/get');await turn();assert.equal(sent.length,1);
  const request=new Request('https://synthetic.test/rpc',{method:'POST',body:'synthetic'});
  const write=read(request);assert.equal(sent.length,2);assert.equal(sent[1][0],request);
  pending[1]();await write;pending[0]();await first;await turn();pending[2]();await second;
  assert.equal(sent.length,3);
  for(const limit of [0,-1,1.5,NaN])assert.throws(()=>createReadFetch(fetch,limit));
});

test('real Supabase HTTP reads preserve all cities and surface 57014 without retrying',{timeout:10000},async()=>{
  const http=require('node:http');
  const replies=[],arrivals=[],waiters=[];let active=0,peak=0;
  const server=http.createServer((req,res)=>{
    const url=new URL(req.url,'http://synthetic');arrivals.push(url);peak=Math.max(peak,++active);
    res.on('finish',()=>active--);
    const city=url.searchParams.get('city_key').replace('eq.','');
    replies.push(()=>{
      res.writeHead(city==='synthetic1'?500:200,{'content-type':'application/json'});
      res.end(JSON.stringify(city==='synthetic1'?{message:'Synthetic timeout',code:'57014',details:null,hint:null}:[{city_key:city}]));
    });
    for(const wake of waiters.splice(0))wake();
  });
  const reached=async n=>{while(arrivals.length<n)await new Promise(resolve=>waiters.push(resolve));};
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    const client=createClient('http://127.0.0.1:'+server.address().port,'synthetic-anon-key',{
      auth:{persistSession:false,autoRefreshToken:false},global:{fetch:createReadFetch(fetch)},
    });
    const results=Promise.all(Array.from({length:6},(_,i)=>client.from(i%2?'v_current_prediction':'v_model_disagreement')
      .select('city_key').eq('city_key','synthetic'+i).then(r=>r)));
    await reached(2);assert.equal(arrivals.length,2);
    for(let i=0;i<6;i++){
      await reached(Math.min(i+2,6));replies[i]();
    }
    const rows=await results;assert.equal(peak,2);assert.equal(arrivals.length,6);
    assert.equal(rows[1].error.code,'57014');assert.equal(rows[1].data,null);
    for(let i=0;i<6;i++)if(i!==1)assert.deepEqual(rows[i].data,[{city_key:'synthetic'+i}]);
    assert(arrivals.every(url=>url.searchParams.get('select')==='city_key'));
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
