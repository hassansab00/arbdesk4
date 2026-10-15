"""Unit translation, station floors and malformed research predictions."""
import sys
from pathlib import Path
import pytest
np=pytest.importorskip("numpy")
pd=pytest.importorskip("pandas")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from wxpredict import forecast_error as fe


def frame(unit="C"):
    return pd.DataFrame([dict(unit=unit,fc_bm_tmax_c=20.,fch_day_max_c=21.,fc_models_mean_c=22.,
        label_unit=21. if unit=="C" else 70.,rmax_unit=18. if unit=="C" else 64.,
        rmax_c=18.,local_hour=9,day_offset=0)])


def test_anchor_priority_and_venue_step_target():
    df=frame()
    assert fe.anchor(df)[0].tolist()==[20] and fe.target(df).tolist()==[fe.RADIUS+1]
    assert fe.eligible(df).tolist()==[True]
    df.loc[0,"fc_bm_tmax_c"]=np.nan
    assert fe.anchor(df)[0].tolist()==[21]
    df.loc[0,"fch_day_max_c"]=np.inf
    assert fe.anchor(df)[0].tolist()==[22]
    df.loc[0,"fc_models_mean_c"]=np.nan
    assert not fe.eligible(df)[0]
    with pytest.raises(ValueError,match="anchors"): fe.target(df)
    assert fe.target(frame("F")).tolist()==[fe.RADIUS+1]


@pytest.mark.parametrize("unit,edges",[("C",[-np.inf,19.,20.,21.,np.inf]),("F",[-np.inf,66.,68.,70.,np.inf])])
def test_probability_translation_and_floor(unit,edges):
    df=frame(unit); lo,hi=np.array([edges[:-1]]),np.array([edges[1:]]); mask=np.ones_like(lo,dtype=bool)
    p=np.zeros((1,fe.CLASSES)); p[0,fe.RADIUS]=1
    out=fe.bucket_probs(p,df,lo,hi,mask)
    assert out.argmax(1).tolist()==[2] and out.sum()==pytest.approx(1)
    changed=df.copy(); changed["label_unit"]=-999
    np.testing.assert_array_equal(out,fe.bucket_probs(p,changed,lo,hi,mask))
    p[:]=0; p[0,0]=1
    assert fe.bucket_probs(p,df,lo,hi,mask).argmax(1).tolist()==[0]
    p[:]=0; p[0,-1]=1
    assert fe.bucket_probs(p,df,lo,hi,mask).argmax(1).tolist()==[3]


def test_invalid_output_and_missing_ladder_coverage():
    df=frame(); p=np.zeros((1,fe.CLASSES)); p[0,fe.RADIUS]=1
    lo,hi,mask=np.array([[0.]]),np.array([[1.]]),np.array([[True]])
    with pytest.raises(ValueError,match="cover"): fe.bucket_probs(p,df,lo,hi,mask)
    p[0,0]=np.nan
    with pytest.raises(ValueError,match="probabilities"): fe.bucket_probs(p,df,lo,hi,mask)
