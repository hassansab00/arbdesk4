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
import tempfile

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
    # A final filename is evidence that offline verification completed. Never
    # leave an apparently usable bundle when the verifier fails or is killed.
    if path.exists():
        raise FileExistsError(path)
    fd,temporary=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.unverified',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as fh:
            fh.write(document)
            fh.flush()
            os.fsync(fh.fileno())
        subprocess.run(['node',str(ROOT/'tools/restore_original_sides.cjs'),temporary],cwd=ROOT,check=True)
        # Hard-link publication is atomic and refuses a concurrently created
        # target; os.replace would silently overwrite a different archive.
        os.link(temporary,path)
    finally:
        os.unlink(temporary)
    return dict(verified=True,tables=len(packets),rows=sum(len(x['rows']) for x in packets),sha256=hashlib.sha256(body.encode()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'.private/original-sides.json'))
    print(export(parser.parse_args().output))
