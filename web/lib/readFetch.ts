// The public pages mount many independent panels at once. Bound anonymous
// database reads so they do not all compete for the same database resources.
// This is transport scheduling only: no row cache, retry, timeout or data change.
export const MAX_CONCURRENT_DB_READS = 2;

export function createReadFetch(fetcher:typeof fetch,limit=MAX_CONCURRENT_DB_READS):typeof fetch {
  if(!Number.isInteger(limit)||limit<1) throw new Error('The read concurrency limit must be a positive integer.');
  let active=0;
  const queue:Array<{start:()=>void}>=[];
  const drain=()=>{while(active<limit&&queue.length) queue.shift()!.start();};
  return (input,init)=>{
    const request=typeof Request!=='undefined'&&input instanceof Request?input:null;
    const method=(init?.method ?? request?.method ?? 'GET').toUpperCase();
    // Writes/RPCs keep their existing transport and are never retried here.
    if(method!=='GET'&&method!=='HEAD') return fetcher(input,init);
    const signal=init?.signal ?? request?.signal;
    const aborted=()=>signal?.reason ?? new DOMException('The request was aborted.','AbortError');
    return new Promise<Response>((resolve,reject)=>{
      if(signal?.aborted){reject(aborted());return;}
      const cancel=()=>{
        const at=queue.indexOf(job);
        if(at>=0){queue.splice(at,1);signal?.removeEventListener('abort',cancel);reject(aborted());}
      };
      const job={start:()=>{
        signal?.removeEventListener('abort',cancel);
        if(signal?.aborted){reject(aborted());return;}
        active++;
        // Promise chaining also releases the slot if a custom fetch throws.
        Promise.resolve().then(()=>fetcher(input,init)).then(resolve,reject)
          .finally(()=>{active--;drain();});
      }};
      signal?.addEventListener('abort',cancel,{once:true});
      queue.push(job);drain();
    });
  };
}
