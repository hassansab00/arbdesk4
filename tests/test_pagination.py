import pytest
import common

def test_server_cap_does_not_truncate_archive(monkeypatch):
    rows=[{'id':i} for i in range(7)]
    def read(path,params):
        p=dict(params);start=int(p['offset']);return rows[start:start+2]
    monkeypatch.setattr(common,'rest',read)
    assert common.rest_all('archive',order='id',page_size=500)==rows

def test_ignored_offset_is_an_error(monkeypatch):
    monkeypatch.setattr(common,'rest',lambda *args:[{'id':1}])
    with pytest.raises(RuntimeError,match='did not advance'):
        common.rest_all('archive',order='id')
