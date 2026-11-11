"""Service-only, immutable current-model artifacts with bounded transfer."""
import base64
import hashlib
import gzip
import io
import os
from pathlib import Path
import re
import time
from wxpredict import common,model_artifacts

PREFIX='supabase-private://current_forecast/'
MAX_COMPRESSED=512*1024
MAX_RAW=8*1024*1024


def validate(blob,expected):
    if len(blob)>MAX_COMPRESSED:raise ValueError('private artifact exceeds storage/egress budget')
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as fh:raw=fh.read(MAX_RAW+1)
    if len(raw)>MAX_RAW or hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('private artifact hash or raw size mismatch')
    return raw


def publish(path,version,expected):
    blob=Path(path).read_bytes()
    raw=validate(blob,expected)
    if len(blob)>MAX_COMPRESSED or len(raw)>MAX_RAW:
        raise ValueError('current artifact exceeds private storage/egress budget; production unchanged')
    common.ops().rpc('register_current_forecast_artifact',dict(p_sha256=expected,
        p_model=raw.decode(),p_compressed=base64.b64encode(blob).decode()))
    meta=dict(asset_url=PREFIX+expected,model_sha256=expected)
    # Server readback, rather than a local-file hit, precedes registration.
    ensure(meta,force_remote=True,budget_s=15)
    return meta['asset_url']


def ensure(meta,root=None,force_remote=False,budget_s=10):
    expected=meta['model_sha256']
    if not re.fullmatch('[a-f0-9]{64}',expected) or meta.get('asset_url')!=PREFIX+expected or not 0<budget_s<=60:
        raise ValueError('invalid private current artifact reference')
    path=Path(root or common.ROOT)/'.cache/wxpredict-models'/(expected+'.txt.gz')
    if path.exists() and not force_remote:
        blob=path.read_bytes()
        if len(blob)>MAX_COMPRESSED:raise ValueError('private artifact exceeds its bound')
        validate(blob,expected)
        return str(path)
    ops=common.ops()
    with ops.request_deadline(time.monotonic()+budget_s):
        data=ops.rpc('read_current_forecast_artifact',dict(p_sha256=expected))
    if not data:raise ValueError('private current artifact unavailable')
    if data.get('model_sha256')!=expected:raise ValueError('private artifact identity mismatch')
    encoded=data['compressed']
    if not isinstance(encoded,str) or len(encoded)>(MAX_COMPRESSED+2)//3*4:
        raise ValueError('private artifact exceeds transfer bound')
    blob=base64.b64decode(encoded,validate=True)
    raw=validate(blob,expected)
    if len(blob)>MAX_COMPRESSED or len(raw)>MAX_RAW or hashlib.sha256(blob).hexdigest()!=data['compressed_sha256']:
        raise ValueError('private artifact hash or size mismatch')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.part');temporary.write_bytes(blob);os.replace(temporary,path)
    return str(path)
