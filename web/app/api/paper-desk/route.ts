import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function serverClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) throw new Error('Single paper desk is not configured on the server.');
  return createClient(url,key,{auth:{persistSession:false,autoRefreshToken:false}});
}

async function edgeGateway(body:Record<string,unknown>) {
  const url=process.env.NEXT_PUBLIC_SUPABASE_URL?.replace(/\/$/,'');
  const key=process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if(!url||!key) throw new Error('Single paper desk is not configured on the server.');
  const response=await fetch(`${url}/functions/v1/paper-desk`,{
    method:'POST',headers:{apikey:key,Authorization:`Bearer ${key}`,'Content-Type':'application/json'},
    body:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(15_000),
  });
  const payload=await response.json().catch(()=>({data:null,error:{message:'Paper gateway returned an invalid response.'}}));
  return {payload,status:response.status};
}

function sameOrigin(request:Request) {
  const origin=request.headers.get('origin');
  return !!origin && origin===new URL(request.url).origin;
}

async function sharedAccount(client:ReturnType<typeof serverClient>,account:string) {
  const {data}=await client.from('paper_accounts').select('account_id')
    .eq('account_id',account).eq('access_mode','single_desk').is('owner_id',null).maybeSingle();
  return !!data;
}

export async function GET(request:Request) {
  try {
    if(!process.env.SUPABASE_SERVICE_KEY) {
      const url=new URL(request.url);
      const result=await edgeGateway({method:'read',resource:url.searchParams.get('resource'),account:url.searchParams.get('account')||''});
      return NextResponse.json(result.payload,{status:result.status,headers:{'Cache-Control':'no-store'}});
    }
    const client=serverClient();
    const url=new URL(request.url);const resource=url.searchParams.get('resource');
    const account=url.searchParams.get('account')||'';
    if(resource==='accounts') {
      // Every desk, not the first one. This page has always rendered a
      // switcher over an array and auto-selected accounts.data[0]; .limit(1)
      // was what kept that array one long. Archived desks are left out -
      // they are kept for their history and cannot trade.
      // REVERTED TO paper_accounts, DELIBERATELY.
      //
      // This briefly read v_paper_desk_activity to get per-desk trade and
      // position counts. The desk list went blank immediately afterwards, and
      // I could not see the deployment to find out why - Vercel Deployment
      // Protection, blocked Vercel logs, and a sandbox that cannot reach the
      // edge function either. Three ways blind is not a place to theorise
      // from. The view is the only thing that changed, so the view goes back.
      //
      // Nothing is lost: the page picks the live desk from mode and
      // entries_paused, which paper_accounts has always carried. The counts
      // were a nicety; a desk list that loads is not.
      const result=await client.from('paper_accounts').select('*').eq('access_mode','single_desk').is('owner_id',null).is('archived_at',null).order('created_at').limit(50);
      return NextResponse.json(result,{headers:{'Cache-Control':'no-store'}});
    }
    if(!account||!await sharedAccount(client,account)) return NextResponse.json({data:null,error:{message:'Single paper desk unavailable.'}},{status:404});
    const queries:Record<string,()=>PromiseLike<{data:unknown;error:unknown}>>={
      orders:()=>client.from('paper_orders').select('*').eq('account_id',account).order('requested_at',{ascending:false}).limit(100),
      positions:()=>client.from('paper_positions').select('*').eq('account_id',account).order('band_id').limit(500),
      activity:()=>client.from('paper_activity').select('*').eq('account_id',account).order('event_id',{ascending:false}).limit(100),
      plans:()=>client.from('paper_trade_plans').select('*').eq('account_id',account).order('created_at',{ascending:false}).limit(100),
    };
    if(!resource||!queries[resource]) return NextResponse.json({data:null,error:{message:'Unknown paper resource.'}},{status:400});
    return NextResponse.json(await queries[resource](),{headers:{'Cache-Control':'no-store'}});
  } catch(e) {
    return NextResponse.json({data:null,error:{message:e instanceof Error?e.message:'Paper desk request failed.'}},{status:503});
  }
}

export async function POST(request:Request) {
  if(!sameOrigin(request)) return NextResponse.json({data:null,error:{message:'Cross-origin paper command rejected.'}},{status:403});
  const size=Number(request.headers.get('content-length')||0);
  if(size>16_384) return NextResponse.json({data:null,error:{message:'Paper command is too large.'}},{status:413});
  try {
    const body=await request.json() as {action?:string;payload?:Record<string,unknown>};
    const commands:Record<string,string>={
      create_account:'create_single_paper_account',
      // Managing desks, as opposed to trading one. create_account is the
      // bootstrap and returns the OLDEST desk when one exists; create_desk
      // makes an additional one.
      create_desk:'paper_desk_create',update_desk:'paper_desk_update',
      reset_desk:'paper_desk_reset',archive_desk:'paper_desk_archive',
      submit_order:'submit_single_paper_order',
      cancel_order:'cancel_single_paper_order',set_policy:'set_single_paper_policy',
      approve_plan:'approve_single_paper_plan',submit_exit:'submit_single_paper_exit',
      set_exit_policy:'set_single_paper_exit_policy',
    };
    const rpc=body.action&&commands[body.action];
    if(!rpc) return NextResponse.json({data:null,error:{message:'Unknown paper command.'}},{status:400});
    if(!process.env.SUPABASE_SERVICE_KEY) {
      const result=await edgeGateway({method:'action',action:body.action,payload:body.payload||{}});
      return NextResponse.json(result.payload,{status:result.status,headers:{'Cache-Control':'no-store'}});
    }
    const result=await serverClient().rpc(rpc,body.payload||{});
    return NextResponse.json(result,{status:result.error?400:200,headers:{'Cache-Control':'no-store'}});
  } catch(e) {
    return NextResponse.json({data:null,error:{message:e instanceof Error?e.message:'Paper command failed.'}},{status:400});
  }
}
