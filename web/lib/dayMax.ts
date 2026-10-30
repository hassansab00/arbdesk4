import {cityInstant} from './time';
export const DAY_MAX_STRATEGY='s14_day_max_prediction';
export const DAY_MAX_TIMES:Record<string,string>={day_ahead:'Day-ahead (last forecast before local midnight)',d1_eve:'Evening before',morning:'Morning',noon:'Noon',prepeak_2h:'2h before expected peak',prepeak_1h:'1h before expected peak'};
export type DayMaxChoice={city_key:string;checkpoint:string;stake_usd:number;hits?:number;calls?:number;dates?:number;verified?:number};
export type DayMaxData={enabled:boolean;activated_at:string|null;selection_changed_at?:string|null;cities:DayMaxChoice[];
  names:Record<string,{display_name:string;timezone:string}>;
  account:{account_id:string;name:string;mode:string;entries_paused:boolean;cash:number}|null;
  last_run:{logged_at:string;status:string;detail:{allocated?:number;skipped?:Record<string,number>;waiting?:string}}|null;
  calls?:{city_key:string;target_date:string;checkpoint:string;frozen_at:string|null;inputs_ok:boolean;block_reason:string|null}[];
  entries:{city_key:string;target_date:string;checkpoint_id:string;plan_id:string|null;created_at:string;
    pick:{top_band_id:string;top_prob:number;frozen_at:string;checkpoint:string;priced_from:string|null};
    label:string|null;status:string;reason:string|null;spent:number}[]};
export function dayMaxCap(cities:DayMaxChoice[]):number {
  if(new Set(cities.map(c=>c.city_key)).size!==cities.length||cities.some(c=>c.stake_usd!==10||!DAY_MAX_TIMES[c.checkpoint]))
    throw new Error('Invalid Day Max city, timing or stake configuration');
  return cities.reduce((sum,c)=>sum+c.stake_usd,0);
}

export function dayMaxStatus(d:DayMaxData,c:DayMaxChoice,date:string,now=new Date()):{status:string;scheduled:string|null} {
  const previous=new Date(`${date}T00:00:00Z`);
  if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(previous.getTime())) return {status:'Choose a valid settlement date',scheduled:null};
  previous.setUTCDate(previous.getUTCDate()-1);
  const zone=d.names[c.city_key]?.timezone;
  const at=zone?cityInstant(c.checkpoint==='d1_eve'?previous.toISOString().slice(0,10):date,c.checkpoint==='d1_eve'?18:0,zone):null;
  const entry=d.entries.find(e=>e.city_key===c.city_key&&e.target_date===date);
  if(entry) return {status:entry.status+(entry.reason?` · ${entry.reason}`:''),scheduled:entry.pick.frozen_at};
  if(!at||!['day_ahead','d1_eve'].includes(c.checkpoint)) return {status:'Entry clock unavailable',scheduled:null};
  const changed=d.selection_changed_at??d.activated_at;
  if(changed&&at.getTime()+75*60000<new Date(changed).getTime()) return {status:'Before activation / timing change; no backfill',scheduled:at.toISOString()};
  if(now<at) return {status:'Waiting for city-local entry window',scheduled:at.toISOString()};
  const call=d.calls?.find(p=>p.city_key===c.city_key&&p.target_date===date&&p.checkpoint===c.checkpoint);
  if(call?.frozen_at&&changed&&new Date(call.frozen_at)<new Date(changed)) return {status:'Original call predates timing change; no backfill',scheduled:at.toISOString()};
  if(call&&!call.inputs_ok) return {status:`Forecast blocked · ${call.block_reason??'inputs unavailable'}`,scheduled:at.toISOString()};
  if(call?.frozen_at&&now.getTime()<=new Date(call.frozen_at).getTime()+90*60000) return {status:'Original call frozen; awaiting execution / worker refusals',scheduled:at.toISOString()};
  if(now.getTime()>at.getTime()+75*60000) return {status:call?'Frozen call retained; no allocation — see worker refusals':'Capture window passed; no original call',scheduled:at.toISOString()};
  return {status:call?'Original call frozen; awaiting execution / worker refusals':'Entry window open; awaiting original capture',scheduled:at.toISOString()};
}
