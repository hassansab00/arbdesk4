import {NextResponse} from 'next/server';
import {privateOperatorOwner,requireOperator,serviceClient} from '../../../lib/operatorAuth';
import {AUDIT_HORIZONS} from '../../../lib/regressionAudit';

export const runtime='nodejs';
export const dynamic='force-dynamic';
const COOKIE='ad4-original-side-owner';
const headers={'Cache-Control':'private, no-store, max-age=0','Vary':'Cookie, Authorization'};
const answer=(data:unknown,status=200)=>NextResponse.json(data,{status,headers});
const fail=(message:string,status:number)=>answer({data:null,error:{message}},status);

/** Always private, even when legacy single-user sign-in is off. */
async function owner(request:Request,client:NonNullable<ReturnType<typeof serviceClient>>):Promise<string|null>{
 const raw=request.headers.get('cookie')?.split(';').map(x=>x.trim()).find(x=>x.startsWith(COOKIE+'='))?.slice(COOKIE.length+1);
 if(raw&&/^[a-f0-9]{64}$/.test(raw)){
  const {data,error}=await client.rpc('verify_original_side_session',{p_token:raw});
  if(!error&&typeof data==='string')return data;
 }
 return privateOperatorOwner(request);
}
function validDate(value:string|null):value is string{return !!value&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(Date.parse(value+'T00:00:00Z'));}

async function read(request:Request){
 const client=serviceClient();if(!client)return fail('Private paper evidence is not configured.',503);
 const id=await owner(request,client);if(!id)return fail('Private owner session required for paper evidence.',401);
 const url=new URL(request.url),op=url.searchParams.get('op')??'history';
 if(op==='regression-audit'){
  const p=url.searchParams,city=p.get('city')||null,checkpoint=p.get('checkpoint')||null;
  const afterCity=p.get('after_city'),afterCheckpoint=p.get('after_checkpoint');
  const limit=p.get('limit')??'20',flagged=p.get('flagged')??'true';
  const validCity=(value:string)=>/^[a-z0-9_]{1,100}$/.test(value);
  const validCheckpoint=(value:string)=>AUDIT_HORIZONS.some(([key])=>key===value);
  if((city&&!validCity(city))||(checkpoint&&!validCheckpoint(checkpoint))
   ||!/^\d{1,2}$/.test(limit)||Number(limit)<1||Number(limit)>25||!['true','false'].includes(flagged)
   ||(afterCity===null)!==(afterCheckpoint===null)
   ||(afterCity!==null&&(!validCity(afterCity)||!validCheckpoint(afterCheckpoint!))))return fail('Invalid audit scope or complete city/checkpoint cursor. Use a page size of 1–25.',400);
  const {data,error}=await client.rpc('predictive_regression_audit',{p_city_key:city,p_checkpoint:checkpoint,
   p_limit:Number(limit),p_after_city:afterCity,p_after_checkpoint:afterCheckpoint,p_flagged_only:flagged==='true'});
  return error?fail(error.message,503):data==null?fail('Private audit returned no report; no result was assumed.',503):answer({data,error:null});
 }
 if(op==='desk'){
  const {data,error}=await client.rpc('read_original_side_desk',{p_owner:id});
  return error?fail(error.message,503):answer({data,error:null});
 }
 if(op==='call'){
  const call=url.searchParams.get('call');if(!call||!/^[a-f0-9-]{36}$/i.test(call))return fail('An exact original call is required.',400);
  const stake=url.searchParams.get('stake')??'10';
  if(!/^\d{1,7}(\.\d{1,6})?$/.test(stake)||Number(stake)<=0||Number(stake)>1000000)return fail('Invalid comparison stake.',400);
  const {data,error}=await client.rpc('read_original_side_call',{p_owner:id,p_call:call,p_account:url.searchParams.get('account')||null,p_stake:stake});
  return error?fail(error.message,503):answer({data,error:null});
 }
 if(op==='ledger'){
  const account=url.searchParams.get('account');if(!account)return fail('Choose a private account.',400);
  const before=url.searchParams.get('before');if(before&&!/^\d{1,18}$/.test(before))return fail('Invalid ledger cursor.',400);
  const {data,error}=await client.rpc('read_original_side_ledger',{p_owner:id,p_account:account,p_before:before||null});
  return error?fail(error.message,403):answer({data,error:null});
 }
 if(op!=='history'&&op!=='export')return fail('Unknown private paper read.',400);
 const from=url.searchParams.get('from'),to=url.searchParams.get('to');
 const offset=Number(url.searchParams.get('offset')??0),stake=url.searchParams.get('stake')??'10';
 if(!validDate(from)||!validDate(to)||Date.parse(to)-Date.parse(from)>31*86400000||Date.parse(to)<Date.parse(from)
  ||!Number.isInteger(offset)||offset<0||offset>10000||!/^\d{1,7}(\.\d{1,6})?$/.test(stake)||Number(stake)<=0||Number(stake)>1000000)return fail('Choose a bounded date range and valid stake.',400);
 const cities=url.searchParams.get('cities')?.split(',').filter(Boolean)??null;
 const checkpoints=url.searchParams.get('checkpoints')?.split(',').filter(Boolean)??null;
 if(cities&&(cities.length>60||cities.some(x=>!/^[a-z0-9_]+$/.test(x))))return fail('Invalid city scope.',400);
 const {data,error}=await client.rpc('read_original_side_history',{p_owner:id,p_from:from,p_to:to,p_cities:cities,
  p_checkpoints:checkpoints,p_account:url.searchParams.get('account')||null,p_stake:stake,p_offset:offset,p_limit:100});
 if(error)return fail(error.message,503);
 const response=answer({data,error:null});
 if(op==='export')response.headers.set('Content-Disposition','attachment; filename="original-bucket-sides.json"');
 return response;
}

async function write(request:Request){
 if(request.headers.get('origin')!==new URL(request.url).origin)return fail('Same-origin private request required.',403);
 const client=serviceClient();if(!client)return fail('Private paper evidence is not configured.',503);
 if(Number(request.headers.get('content-length')??0)>150000)return fail('Private request is too large.',413);
 let body:Record<string,unknown>;try{const raw=await request.text();if(raw.length>150000)return fail('Private request is too large.',413);body=JSON.parse(raw);}catch{return fail('Invalid JSON.',400);}
 if(body.op==='session'){
  if(typeof body.token!=='string'||!/^[a-f0-9]{64}$/.test(body.token))return fail('Invalid private session receipt.',401);
  const {data,error}=await client.rpc('verify_original_side_session',{p_token:body.token});
  if(error||!data)return fail('Private session receipt expired or invalid.',401);
  const response=answer({data:{authorized:true},error:null});
  response.cookies.set(COOKIE,body.token,{httpOnly:true,secure:true,sameSite:'strict',path:'/',maxAge:7*86400});return response;
 }
 const id=await owner(request,client);if(!id)return fail('Private owner session required for paper evidence.',401);
 if(body.op==='resolve'){
  if(!Array.isArray(body.rows)||body.rows.length>300)return fail('At most 300 exact source rows per read.',400);
  const {data,error}=await client.rpc('resolve_original_side_history_rows',{p_owner:id,p_rows:body.rows,p_account:body.account||null,p_stake:body.stake??'10'});
  return error?fail(error.message,503):answer({data,error:null});
 }
 // Every browser write still passes the repository's write gate. The private
 // owner check above is additional and cannot be disabled by single-user mode.
 const gate=await requireOperator(request);if(!gate.ok)return fail(gate.message,gate.status);
 if(body.op==='save'){
  const {data,error}=await client.rpc('save_original_side_policy',{p_owner:id,p_config:body.config});
  return error?fail(error.message,400):answer({data:{policy_id:data,state:'inactive'},error:null});
 }
 if(body.op==='state'&&typeof body.policy_id==='string'&&['activate','pause','resume'].includes(String(body.action))){
  const {data,error}=await client.rpc('set_original_side_policy_state',{p_owner:id,p_policy:body.policy_id,p_action:body.action});
  return error?fail(error.message,400):answer({data,error:null});
 }
 return fail('Unknown private paper command.',400);
}
export async function GET(request:Request){try{return await read(request);}catch{return fail('Private evidence could not be read; no result was assumed.',503);}}
export async function POST(request:Request){try{return await write(request);}catch{return fail('Private paper request could not complete; check its retained stage before retrying.',503);}}
