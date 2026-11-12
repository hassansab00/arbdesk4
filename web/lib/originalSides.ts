/** Compact server projection; money/shares stay decimal strings end to end. */
export type TradeSide='YES'|'NO';
export type SideMode='recorded'|'hypothetical';
export type SideEvaluation={requested_stake:string;shares?:string;notional?:string;fee?:string;
 filled_cost?:string;unspent_allocation?:string;profit_if_win?:string;loss_if_lose?:string;limit_price?:string;reason?:string};
export type SideOrder={order_id:string;account_id:string;policy_id:string;decision_id:string;allocation_id:string;
 status:string;reason:string|null;requested_at:string;requested_stake:string;shares:string|null;notional:string|null;fee:string|null;
 recorded_fill_at:string|null;fill_snapshot_id:string|null;paper_outcome:string;paper_net_pnl:string|null;
 source_kind?:string;evidence_note?:string;settlement_proof_id?:string|null};
export type SideResult={side:TradeSide;token_id:string;quote_kind:'direct'|'derived_scenario'|'missing';quote_id:string|null;
 quote_selection?:string;quote_attempts?:{attempt_id:string;status:string;reason:string|null;received_at:string}[];attempts_truncated?:boolean;
 quote_status:string;reason:string|null;quote_requested_at:string|null;quote_received_at:string|null;book_observed_at:string|null;
 snapshot_id:string|null;side_outcome:string;comparison_stake:string;calculation_version:string;evaluation:SideEvaluation|null;
 paper_orders:SideOrder[];hypothetical_net_pnl:string|null};
export type OriginalSideCall={call_id:string;city_key:string;target_date:string;checkpoint:string;source_kind:string;
 predicted_band_id:string;predicted_band_label:string;frozen_at:string;called_at:string;scheduled_local:string;timezone:string;
 station:string;engine_version:string;rule_receipt_id:string|null;rules_sha256:string|null;ladder_sha256:string;input_as_of:string|null;
 input_sha256:string|null;eligibility:string;prediction_outcome:string;settlement_state:string;settlement_receipt_id:string|null;
 actual_winning_band_id?:string|null;actual_winning_band_label?:string|null;
 sides:Record<TradeSide,SideResult>};
export type SideChoice={city_key:string;checkpoint:string;side:TradeSide};
export type SidePolicyConfig={version:'original-bucket-sides-v2.1';starting_cash:string;funding:'fixed'|'settled_equal_compound';
 fixed_stake:string;derived_no_execution:false;slots:SideChoice[]};
export type SidePolicy={policy_id:string;config:SidePolicyConfig;created_at:string;state:string;
 account:{account_id:string;starting_cash:string;cash:string;reserved_cash:string;activated_at:string;pending_positions:number;recorded_pnl:string|null}|null};
export type SideCapacity={capture_allowed:boolean;bytes:number;limit_bytes:number};
export type SideDesk={policies:SidePolicy[];paper_only:true;capacity?:SideCapacity};
export type SideHistory={rows:OriginalSideCall[];total_calls:number;offset:number;limit:number;truncated:boolean;comparison_stake:string;capacity?:SideCapacity;
 page_summary?:{scope:'returned_page_only';calls:number;quote_covered_calls:number;both_side_quote_calls:number;attempted_orders:number;filled_positions:number;resolved_positions:number;pending_positions:number;recorded_net_pnl:string|null}};
export type HistoryIdentity={row_key:string;city_key:string;target_date:string;checkpoint:string;called_at:string|null;predicted_band_label:string|null;engine_version?:string|null};
export const SIDE_TIMES:Record<string,string>={day_ahead:'Day ahead · D−1 09:00 local',d1_eve:'Evening before · D−1 18:00 local',
 morning:'Morning · 09:00 local',noon:'Noon · 12:00 local',prepeak_2h:'2 h before saved peak',prepeak_1h:'1 h before saved peak'};
export const MIXED_SIDE_PRESET:SidePolicyConfig={version:'original-bucket-sides-v2.1',starting_cash:'100',
 funding:'settled_equal_compound',fixed_stake:'10',derived_no_execution:false,slots:[
 ...['amsterdam','miami','san_francisco','shanghai'].map(city_key=>({city_key,checkpoint:'d1_eve',side:'YES' as const})),
 ...['austin','buenos_aires','houston','warsaw'].map(city_key=>({city_key,checkpoint:'d1_eve',side:'NO' as const}))]};
export function sideMoney(value:string|null|undefined):string{
 if(value==null||value==='')return 'Not recorded';const n=Number(value);
 return Number.isFinite(n)?`${n<0?'−':n>0?'+':''}$${Math.abs(n).toFixed(2)}`:'Unavailable';
}
export function sideIdentity(city:string,date:string,checkpoint:string,at:string|null,label:string|null,version?:string|null):HistoryIdentity{
 return {row_key:JSON.stringify([city,date,checkpoint,at,label,version??null]),city_key:city,target_date:date,checkpoint,
 called_at:at,predicted_band_label:label,engine_version:version};
}
export function predictionLabel(outcome:string):string{
 return ({hit:'Hit',miss:'Miss',pending:'Pending',unverified:'Unverified',verified_void:'Void',verified_split:'Split payout'} as Record<string,string>)[outcome]??'Unverified';
}
export function sideEvidenceLabel(result:SideResult):string{
 if(result.quote_kind==='derived_scenario')return 'Derived NO-price scenario — not a recorded NO ask or verified fill';
 return result.quote_kind==='direct'?'Direct own-token book · quote-only comparison':result.reason??'Not recorded';
}
