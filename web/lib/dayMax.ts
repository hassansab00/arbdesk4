export const DAY_MAX_STRATEGY='s14_day_max_prediction';
export const DAY_MAX_TIMES:Record<string,string>={d1_eve:'Evening before',morning:'Morning',noon:'Noon',prepeak_2h:'2h before expected peak',prepeak_1h:'1h before expected peak'};
export type DayMaxChoice={city_key:string;checkpoint:string;stake_usd:number;hits?:number;calls?:number;dates?:number};
export type DayMaxData={enabled:boolean;activated_at:string|null;cities:DayMaxChoice[];
  names:Record<string,{display_name:string;timezone:string}>;
  account:{account_id:string;name:string;mode:string;entries_paused:boolean;cash:number}|null;
  last_run:{logged_at:string;status:string;detail:{allocated?:number;skipped?:Record<string,number>;waiting?:string}}|null;
  entries:{city_key:string;target_date:string;checkpoint_id:string;plan_id:string|null;created_at:string;
    pick:{top_band_id:string;top_prob:number;frozen_at:string;checkpoint:string;priced_from:string|null};
    label:string|null;status:string;reason:string|null;spent:number}[]};
export function dayMaxCap(cities:DayMaxChoice[]):number {
  if(new Set(cities.map(c=>c.city_key)).size!==cities.length||cities.some(c=>c.stake_usd!==10||!DAY_MAX_TIMES[c.checkpoint]))
    throw new Error('Invalid Day Max city, timing or stake configuration');
  return cities.reduce((sum,c)=>sum+c.stake_usd,0);
}
