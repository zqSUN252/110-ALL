import json
import pytest
from app.migration.bundle_stream import tables,ArrayHash,canonical_hash,file_hash
from app.migration.source import digest


def test_existing_format_unicode_rows_and_hashes_across_every_small_chunk(tmp_path):
    value={'empty':[],'rows':[{'id':str(i),'raw':['中文\n,]}"\\',None,False,i],'nested':{'b':'1.000000','a':'2026-11-05'}} for i in range(1200)]}
    path=tmp_path/'records.json';path.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    for size in (1,7,1024):
        actual={name:list(rows) for name,rows in tables(path,size)}
        assert actual==value
    assert canonical_hash(value)==digest(value)
    for rows in value.values():
        accumulated=ArrayHash()
        for row in rows:accumulated.add(row)
        assert accumulated.count==len(rows) and accumulated.hexdigest()==digest(rows)
    assert file_hash(path)==__import__('hashlib').sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('body',['{"a":[{}]','{"a":[],"a":[]}','{"a":[null]}','{"a":[]} garbage','{"a":[{},]}','{"a":[]}{}'])
def test_truncated_duplicate_or_invalid_frames_are_rejected(tmp_path,body):
    path=tmp_path/'records.json';path.write_text(body)
    with pytest.raises(ValueError):{name:list(rows) for name,rows in tables(path,3)}
