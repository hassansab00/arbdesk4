"""Fixed retrospective development comparison; no serving or promotion."""
import argparse
import hashlib
import json
import os
import sys
import time
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIT_LAST="2026-08-30"
FIRST,LAST="2026-09-01","2026-09-30"
SEED,ROUNDS=20261011,250


def main(argv=None):
    import numpy as np
    import pandas as pd
    from wxpredict import remaining,forecast_error as challenger,walkforward as wf
    ap=argparse.ArgumentParser()
    ap.add_argument("--table",required=True); ap.add_argument("--out",required=True)
    args=ap.parse_args(argv); started=time.monotonic()
    df=pd.read_csv(args.table,low_memory=False)
    usable=challenger.eligible(df)&df.label_unit.notna().values
    train=df[usable&(df.date<=FIT_LAST)].reset_index(drop=True)
    test=df[usable&df.date.between(FIRST,LAST)&(df.source=="venue")&(df.mkt_complete==1)].reset_index(drop=True)
    if train.empty or test.empty: raise ValueError("empty development windows")
    del df
    x=wf.features(train)
    incumbent=remaining.fit(remaining.features(train,x),remaining.target(train),SEED,ROUNDS)
    candidate=challenger.fit(challenger.features(train,x),challenger.target(train),SEED,ROUNDS)
    weather=wf.features(test); lo,hi,q,mask,win=wf.ladders(test)
    ix,cx=remaining.features(test,weather),challenger.features(test,weather)
    base=remaining.bucket_probs(incumbent.predict(ix[incumbent.feature_name()]),test.unit.values,test.rmax_unit.values,lo,hi,mask)
    new=challenger.bucket_probs(candidate.predict(cx[candidate.feature_name()]),test,lo,hi,mask)
    def summary(use):
        out={"n":int(use.sum()),"dates":int(test.loc[use,"date"].nunique())}
        for name,p in {"incumbent":base,"candidate":new,"market":q}.items():
            out[name]={"hits":int((p[use].argmax(1)==win[use]).sum()),"nll":float(wf.nll(p[use],win[use]).mean())}
        out["candidate_nll_gain_ci90_by_date"]=[float(v) for v in wf.boot_ci(wf.nll(base[use],win[use])-wf.nll(new[use],win[use]),test.date.values[use])]
        return out
    early=test.local_hour.between(9,11).values
    result={"recipe":challenger.RECIPE,"evidence":"retrospective development; previously inspected dates",
        "fit_last":FIT_LAST,"evaluation":[FIRST,LAST],"seed":SEED,"rounds":ROUNDS,
        "training_rows":len(train),"all":summary(np.ones(len(test),bool)),"morning":summary(early),
        "by_unit_morning":{u:summary(early&(test.unit.values==u)) for u in sorted(test.unit.unique())},
        "table_sha256":hashlib.sha256(open(args.table,"rb").read()).hexdigest(),
        "candidate_sha256":hashlib.sha256(candidate.model_to_string().encode()).hexdigest(),
        "changes_serving":False,"seconds":round(time.monotonic()-started,2)}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)),exist_ok=True)
    with open(args.out,"w") as fh: json.dump(result,fh,indent=1); fh.write("\n")
    print(json.dumps(result,indent=1))
    return result


if __name__=="__main__": main()
