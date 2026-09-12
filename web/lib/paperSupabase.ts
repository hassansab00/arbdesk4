type PaperResult<T>={data:T|null;error:unknown};

export async function paperRead<T>(resource:string,account?:string):Promise<PaperResult<T>> {
  const query=new URLSearchParams({resource});if(account)query.set('account',account);
  const response=await fetch(`/api/paper-desk?${query}`,{cache:'no-store'});
  const payload=await response.json().catch(()=>({data:null,error:{message:'Paper desk returned an invalid response.'}}));
  return payload as PaperResult<T>;
}

export async function paperAction<T=unknown>(action:string,payload:Record<string,unknown>):Promise<PaperResult<T>> {
  const response=await fetch('/api/paper-desk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,payload})});
  return await response.json().catch(()=>({data:null,error:{message:'Paper desk returned an invalid response.'}})) as PaperResult<T>;
}

/** Wake the server-side paper worker without exposing its bearer token. */
export async function runPaperWorker(): Promise<{ orders_completed: number }> {
  const response = await fetch('/api/paper-cycle', { method: 'POST' });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload?.error === 'string'
      ? payload.error
      : 'Paper worker could not be reached. The order remains queued.');
  }
  return payload as { orders_completed: number };
}
