import type { SupabaseClient } from '@supabase/supabase-js';
import { readAllRows } from './readAll';

/** Original served calls only; live ladders and other horizons never fill a gap. */
export interface FrozenCall {
  city_key: string; target_date: string; checkpoint: string;
  checkpoint_id: string | null; top_label: string | null; top_prob: number | null;
  decided_at: string;
}

export const FROZEN_MOMENTS = ["day_ahead", "d1_eve", "morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h"];

type LineupCall = {city_key:string;target_date:string;checkpoint:string;as_of:string;
  top_label:string|null;top_prob:number|string|null};
type DayAheadCall = {city_key:string;for_date:string;called_at:string;
  model_call:string|null;model_call_prob:number|null};

/** Adapt only already-public original-call views; no privileged table reads. */
export function lineupCalls(rows: LineupCall[]): FrozenCall[] {
  return rows.map(r=>({city_key:r.city_key,target_date:r.target_date,checkpoint:r.checkpoint,
    checkpoint_id:null,top_label:r.top_label,top_prob:r.top_prob===null?null:Number(r.top_prob),decided_at:r.as_of}));
}

export function dayAheadCalls(rows: DayAheadCall[]): FrozenCall[] {
  return rows.filter(r=>r.called_at).map(r=>({city_key:r.city_key,target_date:r.for_date,checkpoint:'day_ahead',
    checkpoint_id:null,top_label:r.model_call,top_prob:r.model_call_prob,decided_at:r.called_at}));
}

export async function readFrozenCalls(client: SupabaseClient, moment: string, dates: string[]):
  Promise<{data:FrozenCall[]|null;error:unknown}> {
  if (!FROZEN_MOMENTS.includes(moment)) return {data:null,error:'Unknown frozen moment.'};
  if (!dates.length) return {data:[],error:null};
  if (moment === 'day_ahead') {
    const result=await readAllRows<DayAheadCall>((from,to)=>client.from('v_city_hit_history')
      .select('city_key,for_date,called_at,model_call,model_call_prob').in('for_date',dates)
      .order('city_key').order('for_date').range(from,to),10000);
    if(result.error || (result.data?.length ?? 0)>=10000)
      return {data:null,error:result.error ?? 'Day-ahead read exceeded its row limit.'};
    return {data:dayAheadCalls(result.data ?? []),error:null};
  }
  const result=await readAllRows<LineupCall>((from,to)=>client.from('v_prediction_lineup')
    .select('city_key,target_date,checkpoint,as_of,top_label,top_prob')
    .eq('model_family','engine').eq('checkpoint',moment).eq('blind',false).eq('withheld',false)
    .in('target_date',dates).order('city_key').order('target_date').range(from,to),10000);
  if(result.error || (result.data?.length ?? 0)>=10000)
    return {data:null,error:result.error ?? 'Frozen call read exceeded its row limit.'};
  return {data:lineupCalls(result.data ?? []),error:null};
}

export function selectedFrozenCall(rows: FrozenCall[], city: string, date: string, moment: string): FrozenCall | null {
  return rows.filter(r => r.city_key === city && r.target_date === date && r.checkpoint === moment)
    .sort((a,b) => Date.parse(a.decided_at)-Date.parse(b.decided_at)
      || (a.checkpoint_id ?? "").localeCompare(b.checkpoint_id ?? ""))[0] ?? null;
}
