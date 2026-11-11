"""Publish only the exact artifact that passed its registered forward trial."""
import os
from wxpredict import common,current_calls as calls,current_registry as registry,cycle_registry


def publish(rpc, root=None):
    from predictive_product import release
    from product_policy import qualified,POLICY
    root=root or common.ROOT
    state=registry.state(root)
    pointer=state.get('forward_validated')
    if state.get('paused') or not pointer:return dict(published=0,reason='no validated fixed forward trial')
    active=registry.selected(root)
    if active is None:return dict(published=0,reason='no accepted current artifact')
    meta,available=active
    report=cycle_registry.read(os.path.join(root,pointer['report']))
    if (not report or calls.digest(report)!=pointer['report_sha256']
            or report.get('decision')!='forward validated' or report.get('reasons')
            or report.get('model_sha256')!=meta['model_sha256']
            or not meta['fit_last']<available[:10]<report['forward_first']
            or report['forward_first']!=str(calls.dt.date.fromisoformat(available[:10])+calls.dt.timedelta(days=2))
            or report.get('forward_evaluation_last')!=str(calls.dt.date.fromisoformat(report['forward_first'])+calls.dt.timedelta(days=13))):
        raise ValueError('current production forward proof mismatch')
    development=cycle_registry.read(os.path.join(root,meta['evaluation_report']))
    # selected() already verifies the original development report's registry hash.
    if not development or development.get('decision')!='accept shadow':
        raise ValueError('current development gate missing')
    locals_dev={(r['source_identity'],r['checkpoint']):r for r in development.get('city_horizons',[])}
    result=dict(published=0,held=0)
    for row in report.get('city_horizons',[]):
        cp=row['checkpoint'];identity=row['source_identity']
        dev=locals_dev.get((identity,cp))
        if cp not in ('noon','prepeak_2h','prepeak_1h') or not dev or not dev.get('supported') or not row.get('supported'):
            result['held']+=1;continue
        if (dev.get('training_dates',0)<20 or dev.get('dates',0)<10
                or any(dev['candidate_hits']<=dev[ref+'_hits']
                    or dev['candidate_brier']>=dev[ref+'_brier']
                    or dev['candidate_nll']>=dev[ref+'_nll'] for ref in ('incumbent','market'))):
            result['held']+=1;continue
        proof=dict(policy=POLICY,original_frozen_calls=True,as_of=report['night'],fit_last=meta['fit_last'],
                   validation_first=report['forward_first'],validation_last=report['forward_evaluation_last'],
                   training_dates=meta['training_dates'].get(identity+'|'+cp,0),dates=row['dates'],
                   calls=row['calls'],expected_calls=14,
                   incumbent_gain_lower90=row['log_loss_gain_lower90'],
                   market_gain_lower90=row['market_log_loss_gain_lower90'],
                   development_report_sha256=calls.digest(development),forward_report_sha256=calls.digest(report))
        for name in ('candidate','incumbent','market'):
            for metric in ('hits','brier','nll'):proof[name+'_'+metric]=row[name+'_'+metric]
        if qualified(proof):result['held']+=1;continue
        if calls.dt.date.fromisoformat(proof['as_of'])<calls.dt.datetime.now(common.UTC).date()-calls.dt.timedelta(days=2):
            result['held']+=1;continue
        city,station,_,_=identity.split('|')
        # Use the original training/validation unit, not today's city metadata.
        unit=(meta.get('source_units') or {}).get(identity)
        if unit not in ('C','F'):result['held']+=1;continue
        r=release('current_forecast',city,cp,station,unit,identity,dict(metadata=meta),proof)
        rpc('publish_predictive_product',dict(p_release=r));result['published']+=1
    return result


def finish_trial(result, root=None):
    """Retire the completed research pointer, preserving its immutable files.

    Approved serving artifacts live in the private release ledger independently
    of the next challenger. A completed failed city gate cannot stop subsequent
    chronological research forever either. This runs only after publication
    returns, so a failed publication leaves the candidate available for retry.
    """
    root=root or common.ROOT
    state=registry.state(root)
    if state.get('paused') or not state.get('forward_validated') or not state.get('candidate'):
        return False
    proof=state['forward_validated'];candidate=state['candidate']
    state['history'].append(dict(night=str(calls.dt.datetime.now(common.UTC).date()),
        decision='production gate complete',report=proof['report'],report_sha256=proof['report_sha256'],
        retired_candidate=candidate,production=dict(result)))
    state.pop('candidate');state.pop('forward_validated')
    cycle_registry.write(os.path.join(root,registry.STATE),state)
    cycle_registry.write(os.path.join(root,registry.POINTER),dict(disabled=True,
        reason='fixed trial complete; approved serving release retained independently'))
    return True
