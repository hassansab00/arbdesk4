"""Final archive paths are published only after the independent restore passes."""
import hashlib
import json
import subprocess

import pytest

import archive_original_sides as archive


def source():
    raw=json.dumps({'entities':[{'table':'synthetic','rows':1,'immutable_ids':['one']}]})
    def call(name,params=None):
        if name=='begin_original_side_archive':
            return dict(payload=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())
        return [json.dumps({'id':'one'})]
    return call


def test_failed_restore_leaves_no_final_or_temporary_bundle(tmp_path,monkeypatch):
    target=tmp_path/'private.json'
    def fail(*args,**kwargs):raise subprocess.CalledProcessError(1,args[0])
    monkeypatch.setattr(archive.subprocess,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):archive.export(target,call=source())
    assert list(tmp_path.iterdir())==[]


def test_final_name_is_absent_during_verification_and_bundle_is_private(tmp_path,monkeypatch):
    target=tmp_path/'private.json'
    def verify(args,**kwargs):
        temporary=archive.Path(args[-1])
        assert not target.exists()
        assert temporary.stat().st_mode&0o777==0o600
        document=json.loads(temporary.read_text())
        assert hashlib.sha256(document['payload'].encode()).hexdigest()==document['sha256']
    monkeypatch.setattr(archive.subprocess,'run',verify)
    result=archive.export(target,call=source())
    assert result['verified'] and result['rows']==1
    assert target.stat().st_mode&0o777==0o600
    assert list(tmp_path.iterdir())==[target]


def test_concurrent_target_is_never_overwritten(tmp_path,monkeypatch):
    target=tmp_path/'private.json'
    monkeypatch.setattr(archive.subprocess,'run',lambda *a,**k:target.write_text('existing archive'))
    with pytest.raises(FileExistsError):archive.export(target,call=source())
    assert target.read_text()=='existing archive'
    assert list(tmp_path.iterdir())==[target]


def test_existing_bundle_is_never_overwritten(tmp_path,monkeypatch):
    target=tmp_path/'private.json';target.write_text('existing archive')
    def unexpected(*a,**k):pytest.fail('existing output must not invoke verifier')
    monkeypatch.setattr(archive.subprocess,'run',unexpected)
    with pytest.raises(FileExistsError):archive.export(target,call=source())
    assert target.read_text()=='existing archive'
