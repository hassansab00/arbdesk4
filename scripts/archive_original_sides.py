"""Service-only private archive, exact-membership paging, no retention action.

Never run via a public artifact upload or mirror. Default output is gitignored,
mode 0600. The independent PGlite restore must pass before the bundle is usable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from common import rpc

ROOT=Path(__file__).resolve().parents[1]


def export(path, *, call=rpc):
    snapshot=call('begin_original_side_archive')
    raw=snapshot['payload']
    if hashlib.sha256(raw.encode()).hexdigest()!=snapshot['sha256']:
        raise ValueError('private archive membership hash mismatch')
    manifest=json.loads(raw)
    packets=[]
    for entity in manifest['entities']:
        rows=entity.get('mutable_snapshot',[])
        ids=entity.get('immutable_ids',[])
        for i in range(0,len(ids),25):
            rows+=call('read_original_side_archive_rows',{'p_table':entity['table'],'p_ids':ids[i:i+25]})
        if len(rows)!=entity['rows']:
            raise ValueError('private archive row count mismatch')
        packets.append(dict(table=entity['table'],rows=rows))
    body=json.dumps(dict(manifest=snapshot,packets=packets),separators=(',',':'))
    document=json.dumps(dict(payload=body,sha256=hashlib.sha256(body.encode()).hexdigest()),separators=(',',':'))
    path=Path(path).resolve()
    if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT/'.private'):
        raise ValueError('private evidence cannot be written into tracked/public repository paths')
    path.parent.mkdir(parents=True,exist_ok=True)
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as fh:
        fh.write(document)
    subprocess.run(['node',str(ROOT/'tools/restore_original_sides.cjs'),str(path)],cwd=ROOT,check=True)
    return dict(verified=True,tables=len(packets),rows=sum(len(x['rows']) for x in packets),sha256=hashlib.sha256(body.encode()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'.private/original-sides.json'))
    print(export(parser.parse_args().output))
