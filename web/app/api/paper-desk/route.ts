import { promises as fs } from 'fs';
import * as path from 'path';
import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { requireOperator } from '../../../lib/operatorAuth';
import { deskActivity, mergeTradeRows, type AccountRow, type DecisionRow, type DeskActivity, type PositionRow,
  type StrategyRow, type TradeRow } from '../../../lib/paperDesks';

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

// Every row of a read, a page at a time: PostgREST answers at most 1,000 rows
// per request, and a count taken from the first page would be a wrong count.
// Each read is ordered by a unique key, so paging cannot skip or repeat a row.
async function readAll<T>(page:(from:number,to:number)=>PromiseLike<{data:unknown;error:unknown}>,
                          size=1000,maxPages=50):Promise<T[]> {
  const out:T[]=[];
  for(let i=0;i<maxPages;i++) {
    const {data,error}=await page(i*size,(i+1)*size-1);
    if(error) throw new Error(error&&typeof error==='object'&&'message' in error?String((error as {message:unknown}).message):String(error));
    const rows=(data as T[]|null)??[];
    out.push(...rows);
    if(rows.length<size) return out;
  }
  throw new Error(`more than ${size*maxPages} rows; the counts were not taken`);
}

// THE TRADE ARCHIVE, from this deployment's own files: web/public/paper-trades,
// written and committed by paper_trade_log.yml, the same files the page's trade
// history fetches. next.config.js ships them with this route
// (outputFileTracingIncludes). No index means nothing exported yet, which is
// not an error; PAPER_ARCHIVE_DIR points the route tests at fixtures.
type Archive = { rows: TradeRow[]; generated_at: string | null; months: string[] };
async function readArchive():Promise<Archive> {
  const dir=process.env.PAPER_ARCHIVE_DIR||path.join(process.cwd(),'public','paper-trades');
  let index:{months?:string[];generated_at?:string};
  try { index=JSON.parse(await fs.readFile(path.join(dir,'index.json'),'utf8')); }
  catch(e) {
    if((e as {code?:string}).code==='ENOENT') return {rows:[],generated_at:null,months:[]};
    throw new Error(`the trade archive's index could not be read: ${e instanceof Error?e.message:String(e)}`);
  }
  const months=Array.isArray(index.months)?index.months:[];
  const rows:TradeRow[]=[];
  for(const m of months) {
    const text=await fs.readFile(path.join(dir,`${m}.jsonl`),'utf8');
    for(const line of text.split('\n')) if(line.trim()) {
      const t=JSON.parse(line) as TradeRow;
      rows.push({trade_id:t.trade_id,account_id:t.account_id??null,opened_at:t.opened_at,closed_at:t.closed_at??null,net_pnl:t.net_pnl??null});
    }
  }
  return {rows,generated_at:index.generated_at??null,months};
}

// WHAT EACH DESK HAS DONE (WXPredict build P.3, P.4), computed here from the
// tables with the service key: trades and net P&L from the archive merged with
// paper_trades (pruned 30 days after export, so the table alone loses history), open
// positions from paper_positions, the strategy's state from strategies, and
// its last 24 h of decisions from decisions. Every desk, archived and retired
// included, for the page's "All desks" summary.
async function readActivity(client:ReturnType<typeof serverClient>,archived:TradeRow[]):Promise<DeskActivity[]> {
  const since=new Date(Date.now()-24*3600*1000).toISOString();
  const [accounts,trades,positions,strategies,decisions]=await Promise.all([
    readAll<AccountRow>((f,t)=>client.from('paper_accounts')
      .select('account_id,name,mode,entries_paused,status,archived_at,retired_at,strategy_id')
      .eq('access_mode','single_desk').is('owner_id',null).order('account_id').range(f,t)),
    readAll<TradeRow>((f,t)=>client.from('paper_trades')
      .select('trade_id,account_id,opened_at,closed_at,net_pnl').order('trade_id').range(f,t)),
    readAll<PositionRow>((f,t)=>client.from('paper_positions')
      .select('account_id,band_id,side,shares').gt('shares',0)
      .order('account_id').order('band_id').order('side').range(f,t)),
    readAll<StrategyRow>((f,t)=>client.from('strategies')
      .select('strategy_id,name,enabled').order('strategy_id').range(f,t)),
    readAll<DecisionRow>((f,t)=>client.from('decisions')
      .select('decision_id,strategy_id,action,reason_code').gte('decided_at',since)
      .order('decision_id').range(f,t)),
  ]);
  return deskActivity(accounts,mergeTradeRows(archived,trades),positions,strategies,decisions);
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
      //
      // THE LIST IS paper_accounts; THE COUNTS ARE READ BESIDE IT AND CAN
      // NEVER EMPTY IT (WXPredict build P.3).
      //
      // On 16 Sep this list briefly read v_paper_desk_activity, the list went
      // blank, and the change was reverted unseen. Measured on 6 Oct, the view
      // was not the cause: it works as the service role (8 desks, the same 8
      // paper_accounts returns) and only anon is refused. Production had no
      // SUPABASE_SERVICE_KEY until 24 Sep 15:57Z (Vercel's record of the
      // variable), so on 16 Sep every read here went to the edge gateway
      // above, whose version 1 (12 Sep until 17:50Z that day) returned only
      // the oldest desk. The view's query never ran in production; what blanked
      // the page then cannot be recovered now.
      //
      // So the counts come from the tables, not a view, and a failing count
      // read leaves the list exactly as it was: no counts, and activity_error
      // naming what failed. The page falls back to mode and entries_paused.
      const result=await client.from('paper_accounts').select('*').eq('access_mode','single_desk').is('owner_id',null).is('archived_at',null).order('created_at').limit(50);
      if(result.error||!result.data) return NextResponse.json(result,{headers:{'Cache-Control':'no-store'}});
      let desks:DeskActivity[]|null=null;let activity_error:string|null=null;
      // An unreadable archive is reported, not hidden: the counts then cover
      // Postgres only and archive_error says so.
      let archive:Archive={rows:[],generated_at:null,months:[]};let archive_error:string|null=null;
      try { archive=await readArchive(); }
      catch(e) { archive_error=e instanceof Error?e.message:String(e); }
      try { desks=await readActivity(client,archive.rows); }
      catch(e) { activity_error=e instanceof Error?e.message:String(e); }
      const byId=new Map((desks??[]).map(d=>[d.account_id,d]));
      const data=(result.data as Record<string,unknown>[]).map(a=>{
        const d=byId.get(String(a.account_id));
        return d?{...a,trade_count:d.trade_count,open_positions:d.open_positions,last_trade_at:d.last_trade_at,activity:d}:a;
      });
      return NextResponse.json({...result,data,desks,activity_error,archive_error,
        archive:{trades:archive.rows.length,generated_at:archive.generated_at,months:archive.months}},
        {headers:{'Cache-Control':'no-store'}});
    }
    if(!account||!await sharedAccount(client,account)) return NextResponse.json({data:null,error:{message:'Single paper desk unavailable.'}},{status:404});
    const queries:Record<string,()=>PromiseLike<{data:unknown;error:unknown}>>={
      orders:()=>client.from('paper_orders').select('*').eq('account_id',account).order('requested_at',{ascending:false}).limit(100),
      positions:()=>client.from('paper_positions').select('*').eq('account_id',account).order('band_id').limit(500),
      activity:()=>client.from('paper_activity').select('*').eq('account_id',account).order('event_id',{ascending:false}).limit(100),
      plans:()=>client.from('paper_trade_plans').select('*').eq('account_id',account).order('created_at',{ascending:false}).limit(100),
      // Per-desk strategy state. v_strategy_board answers "has this ever
      // worked anywhere", which is the wrong grain when each desk runs its
      // own policy - and it is served here rather than read from the browser
      // because the view sits on paper_accounts and runs as its owner.
      strategies:()=>client.from('v_strategy_desk_board').select('*').eq('account_id',account).order('strategy_id').limit(50),
    };
    if(!resource||!queries[resource]) return NextResponse.json({data:null,error:{message:'Unknown paper resource.'}},{status:400});
    return NextResponse.json(await queries[resource](),{headers:{'Cache-Control':'no-store'}});
  } catch(e) {
    return NextResponse.json({data:null,error:{message:e instanceof Error?e.message:'Paper desk request failed.'}},{status:503});
  }
}

export async function POST(request:Request) {
  if(!sameOrigin(request)) return NextResponse.json({data:null,error:{message:'Cross-origin paper command rejected.'}},{status:403});
  // The Origin header is set by whoever sends the request, so it proves
  // nothing on its own. A signed-in operator is required (plan v2 P1.2).
  const verdict=await requireOperator(request);
  if(!verdict.ok) return NextResponse.json({data:null,error:{message:verdict.message}},{status:verdict.status});
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
