"""Owned runtime generations and independently verifiable incremental backups."""
from pathlib import Path
import json
import hashlib
from app.migration.backup import read_bundle
from app.migration.source import digest
from app.private_storage import protect_private_root
from sqlalchemy import select,text
from app.models import AppSetting,AuditEvent,utcnow
from app.common import DomainError,require,check_version,audit


class DirectoryArchive:
    """Explicitly configured team/mounted destination; no implicit external target."""
    def __init__(self,root):self.root=Path(root).resolve()
    def path(self,key):
        if not __import__('re').fullmatch('[0-9a-f]{64}\\.json',key):raise ValueError('Invalid archive key')
        return self.root/key
    def write(self,key,content):
        self.root.mkdir(parents=True,exist_ok=True)
        target=self.path(key)
        if target.exists():
            if target.read_bytes()!=content:raise ValueError('Archive object conflict')
        else:
            with target.open('xb') as out:out.write(content)
    def read(self,key):return self.path(key).read_bytes()


def incremental(base,current,destination):
    bm,b=read_bundle(base);cm,c=read_bundle(current)
    if bm['revision']!=cm['revision']:raise ValueError('Incremental schema mismatch')
    destination=Path(destination)
    if destination.exists():raise ValueError('Incremental destination must be new')
    protect_private_root(destination)
    changes={}
    for name,rows in c.items():
        before={r['id']:r for r in b[name]};after={r['id']:r for r in rows}
        changes[name]={'upsert':[r for key,r in after.items() if before.get(key)!=r],'deleted':sorted(set(before)-set(after))}
    body=json.dumps(changes,ensure_ascii=False,separators=(',',':')).encode()
    (destination/'changes.json').write_bytes(body)
    new_files={k:v for k,v in cm['files'].items() if k not in bm['files']}
    for relative in new_files:
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((Path(current)/relative).read_bytes())
    manifest={'base_records_sha256':bm['records_sha256'],'changes_sha256':hashlib.sha256(body).hexdigest(),'target_manifest':cm,'files':new_files}
    (destination/'incremental.json').write_text(json.dumps(manifest),encoding='utf-8')
    return {'changed':sum(len(v['upsert']) for v in changes.values()),'deleted':sum(len(v['deleted']) for v in changes.values()),'files':len(new_files)}


def materialize(base,delta,destination):
    bm,rows=read_bundle(base);dm=json.loads((Path(delta)/'incremental.json').read_text('utf-8'))
    body=(Path(delta)/'changes.json').read_bytes()
    if bm['records_sha256']!=dm['base_records_sha256'] or hashlib.sha256(body).hexdigest()!=dm['changes_sha256']:raise ValueError('Incremental chain mismatch')
    changes=json.loads(body)
    for name,change in changes.items():
        values={r['id']:r for r in rows[name]}
        for identity in change['deleted']:values.pop(identity,None)
        values.update({r['id']:r for r in change['upsert']});rows[name]=sorted(values.values(),key=lambda r:r['id'])
    destination=Path(destination)
    if destination.exists():raise ValueError('Materialization destination must be new')
    protect_private_root(destination)
    data=json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()
    if hashlib.sha256(data).hexdigest()!=dm['target_manifest']['records_sha256']:raise ValueError('Incremental target mismatch')
    (destination/'records.json').write_bytes(data)
    (destination/'manifest.json').write_text(json.dumps(dm['target_manifest']),encoding='utf-8')
    for relative,sha in dm['target_manifest']['files'].items():
        if relative!=f'objects/{sha[:2]}/{sha}':raise ValueError('Invalid incremental object path')
        origin=(Path(delta) if relative in dm['files'] else Path(base))/relative
        blob=origin.read_bytes()
        if hashlib.sha256(blob).hexdigest()!=sha:raise ValueError('Incremental attachment mismatch')
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(blob)
    read_bundle(destination)
    return {'verified':True}


def writer_gate(db):
    settings=db.info.get('settings')
    if settings is None:return
    row=db.scalar(select(AppSetting).where(AppSetting.key=='migration.writer'))
    if row and (row.value.get('state')!='active' or row.value.get('generation')!=settings.writer_generation):
        raise DomainError('writer_frozen','This runtime is not the current authorized writer',409)


def worker_gate(factory,settings):
    """Admission check only; a cutover also requires operators to drain in-flight IO."""
    with factory() as db,db.begin():
        db.info['settings']=settings
        writer_gate(db)


def cutover(db,actor,expected_version,generation,source,active=True):
    require(actor,'*')
    if not generation or not source:raise DomainError('cutover_source','Generation and frozen source handoff evidence required')
    db.execute(text('SELECT pg_advisory_xact_lock(110002)'))
    row=db.scalar(select(AppSetting).where(AppSetting.key=='migration.writer').with_for_update())
    if row:check_version(row,expected_version);row.version+=1
    elif expected_version!=0:raise DomainError('stale_version','Writer generation changed',409)
    else:row=AppSetting(key='migration.writer',value={});db.add(row)
    row.value={'generation':generation,'state':'active' if active else 'frozen','source':source,'cutover_at':utcnow().isoformat()}
    audit(db,actor,'migration.writer_changed','setting',row.id or 'migration.writer',None,row.value)
    db.flush();return {'version':row.version,'generation':generation,'state':row.value['state'],'external_flags_changed':False}


def backout_plan(db,after):
    rows=list(db.scalars(select(AuditEvent).where(AuditEvent.created_at>after,~AuditEvent.action.like('migration.%')).order_by(AuditEvent.created_at)))
    return {'state':'reconciliation_required' if rows else 'no_new_business_events','new_events':len(rows),'events':[{'id':r.id,'action':r.action,'entity_type':r.entity_type,'entity_id':r.entity_id} for r in rows],'external_facts_reversible':False,'overwrite_allowed':not rows}
