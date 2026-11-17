const {test}=require('node:test'),assert=require('node:assert/strict');
const {createHash}=require('node:crypto'),{gzipSync}=require('node:zlib');
const lib=require('../.route-test/lib/privateArchive.js');
const auth=require('../.route-test/lib/operatorAuth.js');
const route=require('../.route-test/app/api/private-archive/route.js');
const commit='a'.repeat(40),token='fixture-private-credential',cookie='b'.repeat(64);
const query=()=>new URLSearchParams('dataset=forecasts&from=2026-10-01&to=2026-10-01');
function fixture(){
 const asset='data/archive/forecasts/forecasts-2026-10-01-to-2026-10-01.csv.gz';
 const csv=gzipSync('city_key,for_date,payload\namsterdam,2026-10-01,"{""label"":""retained""}"\n');
 const index=Buffer.from(JSON.stringify({datasets:{forecasts:{table:'weather_forecasts',assets:[{asset:asset.split('/').pop(),rows:1,from:'2026-10-01',to:'2026-10-01'}]}}}));
 const proof=(bytes,rows)=>({bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),rows});
 const receipt={destination:{repo:lib.PRIVATE_ARCHIVE_REPO,private:true},files:{[asset]:proof(csv,1),'web/public/archive/index.json':proof(index,null)}};
 const data=new Map([[asset,csv],['web/public/archive/index.json',index]]),calls=[];
 let visible=true;
 const fetcher=async(url,options)=>{
  calls.push([url,options]);assert.equal(options.headers.Authorization,'Bearer '+token);assert.equal(options.redirect,'error');
  const u=new URL(url),base='/repos/'+lib.PRIVATE_ARCHIVE_REPO;
  if(u.pathname===base)return Response.json({full_name:lib.PRIVATE_ARCHIVE_REPO,private:visible,visibility:visible?'private':'public',archived:false,disabled:false});
  if(u.pathname===base+'/git/ref/heads/main')return Response.json({object:{sha:commit}});
  assert.equal(u.searchParams.get('ref'),commit);
  const path=u.pathname.slice((base+'/contents/').length);
  if(path==='data/private_archive_snapshot.json')return Response.json(receipt);
  if(data.has(path))return new Response(data.get(path));
  throw new Error('Unexpected fixture request');
 };
 return{fetcher,calls,data,receipt,asset,setPublic(){visible=false;}};
}
test('strict CSV retains quoted newlines and refuses truncated or short records',()=>{
 assert.deepEqual(lib.privateCsv('a,b\n1,"two\nlines"\n'),[{a:'1',b:'two\nlines'}]);
 for(const s of ['a,b\n1\n','a,b\n1,"unfinished','a,a\n1,2\n'])assert.throws(()=>lib.privateCsv(s));
});
test('all files are pinned to one private commit and returned only after digest and row checks',async()=>{
 const f=fixture(),result=await lib.readPrivateArchive(query(),token,f.fetcher);
 assert.equal(result.private_commit,commit);assert.equal(result.matched,1);assert.equal(result.rows[0].city_key,'amsterdam');
 assert.deepEqual(result.rows[0].payload,{label:'retained'});assert.equal(JSON.stringify(result).includes(token),false);
});
test('changed payloads, wrong row counts and public visibility refuse without fallback',async()=>{
 let f=fixture();f.data.set(f.asset,Buffer.from('changed'));await assert.rejects(lib.readPrivateArchive(query(),token,f.fetcher),/committed receipt/);
 f=fixture();f.receipt.files[f.asset].rows=2;await assert.rejects(lib.readPrivateArchive(query(),token,f.fetcher),/row count/);
 f=fixture();f.setPublic();await assert.rejects(lib.readPrivateArchive(query(),token,f.fetcher),/active private/);assert.equal(f.calls.length,1);
});
test('anonymous and revoked sessions never reach GitHub even in legacy single-user mode',async()=>{
 const prior=auth.serviceClient,fetchBefore=global.fetch,calls=[];
 auth.serviceClient=()=>({rpc:async name=>{calls.push(name);return{data:null,error:null};}});
 global.fetch=async()=>{throw new Error('GitHub must not be called');};
 try{delete process.env.OPERATOR_SIGN_IN;
  let r=await route.GET(new Request('https://desk.example/api/private-archive'));assert.equal(r.status,401);assert.deepEqual(calls,[]);
  r=await route.GET(new Request('https://desk.example/api/private-archive',{headers:{cookie:'ad4-original-side-owner='+cookie}}));assert.equal(r.status,401);assert.deepEqual(calls,['verify_original_side_session']);
 }finally{auth.serviceClient=prior;global.fetch=fetchBefore;}
});
test('verified owner session reads private evidence with no-store headers',async()=>{
 const prior=auth.serviceClient,fetchBefore=global.fetch,tokenBefore=process.env.AD4_PRIVATE_ARCHIVE_READ_TOKEN,f=fixture();
 auth.serviceClient=()=>({rpc:async()=>({data:'fixture-owner',error:null})});global.fetch=f.fetcher;process.env.AD4_PRIVATE_ARCHIVE_READ_TOKEN=token;
 try{const r=await route.GET(new Request('https://desk.example/api/private-archive?'+query(),{headers:{cookie:'ad4-original-side-owner='+cookie}}));assert.equal(r.status,200);assert.match(r.headers.get('cache-control'),/private, no-store/);assert.match(r.headers.get('vary'),/Authorization/);assert.equal((await r.json()).returned,1);
 }finally{auth.serviceClient=prior;global.fetch=fetchBefore;if(tokenBefore===undefined)delete process.env.AD4_PRIVATE_ARCHIVE_READ_TOKEN;else process.env.AD4_PRIVATE_ARCHIVE_READ_TOKEN=tokenBefore;}
});
