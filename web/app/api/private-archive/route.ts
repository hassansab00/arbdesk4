import {NextResponse} from 'next/server';
import {privateOperatorOwner,serviceClient} from '../../../lib/operatorAuth';
import {readPrivateArchive} from '../../../lib/privateArchive';

export const runtime='nodejs';
export const dynamic='force-dynamic';
const headers={'Cache-Control':'private, no-store, max-age=0',Vary:'Cookie, Authorization'};
export async function GET(request:Request){
 const client=serviceClient();let owner:string|null=null;
 const cookie=request.headers.get('cookie')?.split(';').map(x=>x.trim()).find(x=>x.startsWith('ad4-original-side-owner='))?.slice('ad4-original-side-owner='.length);
 try{
  if(client&&cookie&&/^[a-f0-9]{64}$/.test(cookie)){
   const {data,error}=await client.rpc('verify_original_side_session',{p_token:cookie});
   if(!error&&typeof data==='string')owner=data;
  }
  if(!owner)owner=await privateOperatorOwner(request);
 }catch{owner=null;}
 if(!owner)return NextResponse.json({error:'Private owner session required for archived evidence.'},{status:401,headers});
 try{
  const data=await readPrivateArchive(new URL(request.url).searchParams,process.env.AD4_PRIVATE_ARCHIVE_READ_TOKEN||'');
  return NextResponse.json(data,{headers});
 }catch(error){
  const message=error instanceof Error?error.message:'Private archive verification failed.';
  // Only fixed validation/status errors are returned; never remote bodies or credentials.
  const safe=/^(Private |Archive destination|AD4_PRIVATE_|Unknown private|Choose a private|Invalid private|Use the complete-file)/.test(message)?message:'Private archive could not be verified. No public fallback was used.';
  return NextResponse.json({error:safe},{status:503,headers});
 }
}
