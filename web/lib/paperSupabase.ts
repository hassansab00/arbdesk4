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

/**
 * Wake the paper worker without exposing any token to the browser.
 *
 * TWO SHAPES, because there are two workers and only one of them exists.
 * A deployed worker service answers in milliseconds with orders_completed - a
 * real count of what it filled. Nothing is deployed, so the route dispatches
 * GitHub Actions instead and answers `started`: the run is queued, the fill
 * happens about a minute later, and no count can be known yet.
 *
 * Both are returned as they are, and describeWorker turns either into one
 * sentence, so no caller has to guess which it got - guessing is how the old
 * version reported "nothing was claimable" for a fill that had not run.
 */
export type WorkerResult = { orders_completed?: number; started?: boolean; note?: string };

export async function runPaperWorker(): Promise<WorkerResult> {
  const response = await fetch('/api/paper-cycle', { method: 'POST' });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload?.error === 'string'
      ? payload.error
      : 'Paper worker could not be reached. The order remains queued.');
  }
  return payload as WorkerResult;
}

/** What actually happened, in one sentence, for whichever worker answered. */
export function describeWorker(r: WorkerResult): string {
  if (r.started) {
    return r.note ?? 'Worker started in GitHub Actions. It takes about a minute; refresh Orders then.';
  }
  return r.orders_completed
    ? `Filled ${r.orders_completed} queued order(s).`
    : 'Nothing was claimable — no order is queued, or what was has expired. '
      + 'A queued order lives five minutes.';
}
