import type { SupabaseClient } from '@supabase/supabase-js';
import type { CityCard, StatusRow } from './cityCards';
import type { CityReport } from './cityValidation';
import type { HindsightRow } from './focus';
import { cityDate } from './time';

export const GUIDE_CHECKPOINTS = ['day_ahead','d1_eve','morning','noon','prepeak_2h','prepeak_1h'] as const;
// Display evidence floors, not model parameters or permission to trade.
// Ten dates matches the existing selector's minimum validation-date floor.
export const MIN_REFERENCE_DATES = 10;
export const MAX_REPORT_AGE_H = 36; // Existing validation-report freshness rule.
export const MAX_MARK_AGE_H = 3; // Existing book freshness SLA; marks still are not executable asks.
export type GuideReportRow = {city_key:string;checkpoint:string;scope:string;as_of:string;report:CityReport};
export type ReferenceRow = {checkpoint:string;hits:number;n:number;rate:number;verifiedBeforePeak:number};

export async function readGuideReports(client: SupabaseClient, city: string) {
  const result=await client.from('v_city_prediction_validation').select('city_key,checkpoint,scope,as_of,report')
    .eq('city_key',city).eq('scope','served').in('checkpoint',[...GUIDE_CHECKPOINTS])
    .order('checkpoint').limit(GUIDE_CHECKPOINTS.length+1);
  if(result.error) return {data:null,error:result.error};
  if((result.data?.length ?? 0)>GUIDE_CHECKPOINTS.length)
    return {data:null,error:'The city history contains an ambiguous checkpoint read.'};
  return {data:(result.data ?? []) as GuideReportRow[],error:null};
}

function number(value: unknown): number|null {
  if(value===null || value===undefined || value==='') return null;
  const n=Number(value);return Number.isFinite(n)?n:null;
}
const fresh = (at:string|null|undefined, maxH:number, now:number) => {
  const age=now-Date.parse(at ?? '');return Number.isFinite(age)&&age>=0&&age<=maxH*3600000;
};

/** Descriptive timing on matched past dates. Never a fitted or validated trading rule. */
export function checkpointReference(rows: GuideReportRow[], city:string, target:string, now:number) {
  const unique=new Map<string,GuideReportRow|null>();
  for(const row of rows) {
    if(row.city_key!==city || row.scope!=='served' || row.report.city!==city || row.report.scope!=='served'
      || row.report.horizon!==row.checkpoint || !GUIDE_CHECKPOINTS.includes(row.checkpoint as typeof GUIDE_CHECKPOINTS[number])
      || !fresh(row.as_of,MAX_REPORT_AGE_H,now)) continue;
    unique.set(row.checkpoint,unique.has(row.checkpoint)?null:row);
  }
  const candidates=[...unique.values()].filter((r):r is GuideReportRow=>!!r).map(row=>{
    const dates=new Map<string,number|null>();
    for(const d of row.report.recent_dates ?? []) {
      if(d.date>=target || d.n!==1 || ![0,1].includes(d.hits)) continue;
      dates.set(d.date,dates.has(d.date)?null:d.hits);
    }
    const scores=new Map([...dates].filter((entry):entry is [string,number]=>entry[1]!==null));
    return {row,scores};
  }).filter(c=>c.scores.size>=MIN_REFERENCE_DATES);
  const common=candidates.length>=2?[...candidates[0].scores.keys()]
    .filter(date=>candidates.every(c=>c.scores.has(date))).sort():[];
  const comparable=common.length>=MIN_REFERENCE_DATES;
  const table:ReferenceRow[]=comparable?candidates.map(({row,scores})=>({
    checkpoint:row.checkpoint,n:common.length,hits:common.reduce((n,d)=>n+scores.get(d)!,0),
    rate:common.reduce((n,d)=>n+scores.get(d)!,0)/common.length,
    // Only settled, receipted, timing-eligible calls among those same dates.
    verifiedBeforePeak:new Set((row.report.capture_timing?.policy==='before-expected-peak-v1'
      ?row.report.capture_timing.recent_calls:[])
      .filter(c=>common.includes(c.date)&&c.checkpoint===row.checkpoint&&c.before_expected_peak
        && c.reason==='eligible'&&c.frozen_at&&[0,1].includes(c.hit as number)
        && Date.parse(c.frozen_at)<=now).map(c=>c.date)).size,
  })).sort((a,b)=>b.rate-a.rate || GUIDE_CHECKPOINTS.indexOf(a.checkpoint as typeof GUIDE_CHECKPOINTS[number])
    -GUIDE_CHECKPOINTS.indexOf(b.checkpoint as typeof GUIDE_CHECKPOINTS[number])):[];
  return {dates:common,table,leaders:table.filter(r=>r.hits===table[0].hits).map(r=>r.checkpoint),
    compared:candidates.length,comparable};
}

/** Errors describe original selected calls, excluding today/future and duplicate dates. */
export function selectedTrend(history:HindsightRow[],city:string,moment:string,target:string) {
  const from=new Date(`${target}T00:00:00Z`);from.setUTCDate(from.getUTCDate()-30);
  const cutoff=from.toISOString().slice(0,10), dates=new Map<string,HindsightRow|null>();
  for(const r of history) if(r.city_key===city&&r.called_when===moment&&r.for_date>=cutoff
    && r.for_date<target&&typeof r.hit==='boolean') dates.set(r.for_date,dates.has(r.for_date)?null:r);
  const errors=[...dates.values()].flatMap(r=>r&&number(r.forecast_error_c)!==null?[number(r.forecast_error_c)!]:[]);
  return {n:errors.length,biasC:errors.length?errors.reduce((a,b)=>a+b,0)/errors.length:null,
    maeC:errors.length?errors.reduce((a,b)=>a+Math.abs(b),0)/errors.length:null};
}

export function guideReviewTime(checkpoint:string, peak:number|null):{dayOffset:number;hour:number}|null {
  if(checkpoint==='d1_eve') return {dayOffset:-1,hour:18};
  if(checkpoint==='morning') return {dayOffset:0,hour:9};
  if(checkpoint==='noon') return {dayOffset:0,hour:12};
  if(peak!==null&&Number.isFinite(peak)&&peak>=0&&peak<24) {
    if(checkpoint==='prepeak_2h') return {dayOffset:0,hour:peak-2};
    if(checkpoint==='prepeak_1h') return {dayOffset:0,hour:peak-1};
  }
  return null;
}

export function buildManualGuide({card,moment,rows,history,status,now}: {
  card:CityCard;moment:string;rows:GuideReportRow[];history:HindsightRow[];status:StatusRow|null;now:number;
}) {
  const reference=checkpointReference(rows,card.city_key,card.for_date,now);
  const trend=selectedTrend(history,card.city_key,moment,card.for_date);
  const blockers:string[]=[],cautions:string[]=[];
  let localDate:string|null=null;
  try {if(card.timezone) localDate=cityDate(new Date(now),card.timezone);} catch { /* unknown stays unknown */ }
  if(!localDate) blockers.push('The city clock is unavailable.');
  else if(localDate>card.for_date) blockers.push('The observation day has ended; this is no longer a new daily-maximum forecast.');
  if(!reference.comparable) blockers.push('Too few matched settled dates for a timing reference.');
  else if(reference.table.filter(r=>reference.leaders.includes(r.checkpoint))
    .some(r=>r.verifiedBeforePeak<MIN_REFERENCE_DATES))
    blockers.push('The leading timing reference has too few receipt-verified calls before the saved expected peak to support an entry rule.');
  if(!status) blockers.push('Current city readiness has not been verified.');
  else if(status.city_key!==card.city_key||status.target_date!==card.for_date||status.checkpoint!==moment)
    blockers.push('The readiness check does not match this city, date and selected moment.');
  else if(status.status==='unavailable') blockers.push(status.reason);
  else if(status.status!=='candidate') cautions.push(status.reason);
  if(!fresh(card.priced_at,MAX_MARK_AGE_H,now)||!fresh(card.edges_at,MAX_MARK_AGE_H,now))
    blockers.push('Forecast pricing or market marks are missing, future-dated or older than the book freshness limit.');
  if(card.stale) blockers.push('The live pick is flagged out of date after subsequent station readings.');
  if(!card.best||!Number.isFinite(card.best.edge)||card.best.edge<=0)
    blockers.push('No eligible positive model edge is recorded on this screen.');
  else if(number(card.best.price)===null||Number(card.best.price)<=0||Number(card.best.price)>=1
    ||number(card.best.depth)===null||Number(card.best.depth)<=0)
    blockers.push('A usable market mark and recorded depth are not both available.');
  if(card.repriced) cautions.push('The latest forecast and stored market edges come from different updates; recheck them together.');
  if(card.disagrees) cautions.push('The model and market favourites differ; seek independent station and forecast confirmation.');
  if(!card.live||card.live.source_kind==='model') cautions.push('This popup has no current observed station reading. A model temperature is not an observation.');
  const selected=rows.filter(r=>r.city_key===card.city_key&&r.checkpoint===moment&&r.scope==='served'
    &&r.report.city===card.city_key&&r.report.horizon===moment&&r.report.period_end<=card.for_date
    &&fresh(r.as_of,MAX_REPORT_AGE_H,now));
  const why=selected.length===1?selected[0].report.why:null;
  const weather=why?Object.entries(why.weather).filter(([,n])=>Number.isFinite(n)&&n>0)
    .sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,2):[];
  return {reference,trend,blockers,cautions,weather,reviewed:why?.reviewed_dates ?? 0,
    missed:why?.missed_dates ?? 0,state:blockers.length?'Wait':'Review only',
    asOf:rows.filter(r=>r.city_key===card.city_key&&fresh(r.as_of,MAX_REPORT_AGE_H,now))
      .map(r=>r.as_of).sort()[0] ?? null};
}
export type ManualGuide = ReturnType<typeof buildManualGuide>;
