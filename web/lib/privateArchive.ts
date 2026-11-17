import {createHash} from 'node:crypto';
import {gunzipSync} from 'node:zlib';
import {decodePayload} from './archiveFormat';

export const PRIVATE_ARCHIVE_REPO='hassansab00/Arbdesk4-archive-001';
const INDEX='web/public/archive/index.json',RECEIPT='data/private_archive_snapshot.json';
type Proof={sha256:string;bytes:number;rows:number|null};
type Asset={asset:string;rows:number;from:string;to:string};
type Manifest={datasets:Record<string,{table:string;assets:Asset[]}>};
type Fetcher=typeof fetch;
const times:Record<string,string>={model_skill:'computed_at',observations:'valid_at',forecasts:'for_date',trades:'traded_at',research:'captured_at',resolution:'captured_at',edges:'computed_at',book_evidence:'captured_at',forecast_features:'for_date',forecast_models:'for_date',probabilities:'resolution_date',correlation:'computed_at',live_capture:'captured_at',forecast_variables:'for_date',model_payloads:'for_date',signal_inputs:'fired_at',decisions:'decided_at',ladders:'observed_at',books:'observed_at'};

function verified(data:Buffer,proof:Proof|undefined){
 if(!proof||!Number.isSafeInteger(proof.bytes)||proof.bytes!==data.length
   ||!/^[a-f0-9]{64}$/.test(proof.sha256)||createHash('sha256').update(data).digest('hex')!==proof.sha256)
  throw new Error('Private archive bytes do not match their committed receipt.');
}

/** Strict RFC 4180 parsing, including quoted newlines and doubled quotes. */
export function privateCsv(text:string):Record<string,string>[]{
 const records:string[][]=[];let row:string[]=[],field='',quoted=false,closed=false;
 const end=()=>{row.push(field);if(row.length!==1||row[0]!=='')records.push(row);row=[];field='';closed=false;};
 for(let i=0;i<text.length;i++){
  const c=text[i];
  if(quoted){if(c==='"'){if(text[i+1]==='"'){field+='"';i++;}else{quoted=false;closed=true;}}else field+=c;continue;}
  if(c==='"'){if(field||closed)throw new Error('Malformed private CSV quote.');quoted=true;}
  else if(c===','){row.push(field);field='';closed=false;}
  else if(c==='\n')end();
  else if(c!=='\r'){if(closed)throw new Error('Malformed private CSV field.');field+=c;}
 }
 if(quoted)throw new Error('Truncated private CSV.');
 if(field||row.length||closed)end();
 if(!records.length)return[];
 const head=records.shift()!;
 if(head.some(x=>!x)||new Set(head).size!==head.length)throw new Error('Invalid private CSV header.');
 return records.map(values=>{if(values.length!==head.length)throw new Error('Private CSV column count differs.');return Object.fromEntries(head.map((key,i)=>[key,values[i]]));});
}

export async function readPrivateArchive(query:URLSearchParams,token:string,fetcher:Fetcher=fetch){
 if(!token)throw new Error('AD4_PRIVATE_ARCHIVE_READ_TOKEN is not configured on this deployment.');
 const api=`https://api.github.com/repos/${PRIVATE_ARCHIVE_REPO}`;
 const request=async(path:string,raw=false)=>{
  const response=await fetcher(api+path,{headers:{Authorization:`Bearer ${token}`,Accept:raw?'application/vnd.github.raw+json':'application/vnd.github+json'},cache:'no-store',redirect:'error',signal:AbortSignal.timeout(10_000)});
  if(!response.ok)throw new Error(`Private archive request failed (${response.status}).`);
  return response;
 };
 const meta=await(await request('')).json();
 if(meta.full_name!==PRIVATE_ARCHIVE_REPO||meta.private!==true||meta.visibility!=='private'||meta.archived||meta.disabled)
  throw new Error('Archive destination is not the approved active private repository.');
 const ref=await(await request('/git/ref/heads/main')).json(),commit=ref.object?.sha;
 if(typeof commit!=='string'||!/^[a-f0-9]{40}$/.test(commit))throw new Error('Private archive commit is unavailable.');
 const raw=async(path:string)=>Buffer.from(await(await request(`/contents/${path}?ref=${commit}`,true)).arrayBuffer());
 const receipt=JSON.parse((await raw(RECEIPT)).toString('utf8'));
 if(receipt.destination?.repo!==PRIVATE_ARCHIVE_REPO||receipt.destination?.private!==true||!receipt.files)
  throw new Error('Private archive receipt identifies another destination.');
 const indexBytes=await raw(INDEX);verified(indexBytes,receipt.files[INDEX]);
 const index=JSON.parse(indexBytes.toString('utf8')) as Manifest;
 const dataset=query.get('dataset');
 if(!dataset)return {...index,repo:PRIVATE_ARCHIVE_REPO,private_commit:commit,manifest_source:'verified private commit',can_fetch_rows:true};
 if(!/^[a-z][a-z0-9_]*$/.test(dataset)||!times[dataset]||!index.datasets?.[dataset])throw new Error('Unknown private archive dataset.');
 const ds=index.datasets[dataset],from=query.get('from'),to=query.get('to');
 const date=(s:string|null):s is string=>!!s&&/^\d{4}-\d{2}-\d{2}$/.test(s)&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;
 if(!date(from)||!date(to)||to<from||Date.parse(to)-Date.parse(from)>31*86400000)throw new Error('Choose a private archive range of at most 32 dates.');
 const asset=query.get('asset'),limit=Number(query.get('limit')??500),offset=Number(query.get('offset')??0);
 if(!Number.isSafeInteger(limit)||limit<1||limit>5000||!Number.isSafeInteger(offset)||offset<0||offset>100000)throw new Error('Invalid private archive page.');
 const wanted=ds.assets.filter(a=>a.to>=from&&a.from<=to&&(!asset||a.asset===asset));
 if(asset&&!wanted.length)throw new Error('Private asset is not registered for this date range.');
 if(wanted.some(a=>!/^[A-Za-z0-9_.-]+\.csv\.gz$/.test(a.asset)))throw new Error('Invalid private archive asset name.');
 if(asset&&query.get('redirect')==='1')return{download:`https://github.com/${PRIVATE_ARCHIVE_REPO}/raw/${commit}/data/archive/${dataset}/${asset}`,private_commit:commit,note:'The complete private file requires GitHub access.'};
 const rows:Record<string,unknown>[]=[];let matched=0;
 for(const a of wanted){
  const path=`data/archive/${dataset}/${a.asset}`,proof=receipt.files[path] as Proof|undefined;
  if(!proof||proof.bytes>16*1024*1024)throw new Error('Use the complete-file download for this large private asset.');
  const bytes=await raw(path);verified(bytes,proof);
  const parsed=privateCsv(gunzipSync(bytes,{maxOutputLength:128*1024*1024}).toString('utf8'));
  if(parsed.length!==a.rows||parsed.length!==proof.rows)throw new Error('Private row count differs from its verified receipt.');
  for(const row of parsed){const stamp=row[times[dataset]]?.slice(0,10);if(!stamp)throw new Error('Private row date is missing.');if(stamp<from||stamp>to)continue;if(matched>=offset&&rows.length<limit)rows.push(row.payload?{...row,payload:decodePayload(row.payload)}:row);matched++;}
 }
 return{dataset,table:ds.table,private_commit:commit,matched,returned:rows.length,offset,limit,assets_read:wanted.map(x=>x.asset),rows};
}
