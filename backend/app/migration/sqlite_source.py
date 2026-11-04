"""Read-only SQLite acquisition. A snapshot is not a production classification."""
from pathlib import Path
import hashlib
import sqlite3
import time
import tempfile
import json
import base64
import stat
from app.private_storage import protect_private_root


def checked_path(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('source_output_reparse_point')
    return path.resolve()


def validate_layout(source, snapshot, output=None, manifest=None):
    """Validate before mkdir/ACL/write; recursive protection must never reach originals."""
    source, snapshot = checked_path(source), checked_path(snapshot)
    manifest = manifest or {}
    roots = [source.parent]
    for key in ('bundle_root', 'attachment_root'):
        if manifest.get(key):
            roots.append(checked_path(manifest[key]))
    targets = [snapshot.parent]
    if output is not None:
        targets.append(checked_path(output))
    for target in targets:
        if any(target.is_relative_to(root) or root.is_relative_to(target) for root in roots):
            raise ValueError('source_output_overlap')
    if output is not None and (targets[0].is_relative_to(targets[1]) or targets[1].is_relative_to(targets[0])):
        raise ValueError('snapshot_export_overlap')
    if snapshot.exists() or snapshot.parent.exists() or (output is not None and targets[1].exists()):
        raise ValueError('Snapshot and export directories must be new')


def consistent_snapshot(source, destination, timeout=15, frozen_manifest=None):
    source, destination = checked_path(source), checked_path(destination)
    if source == destination or destination.exists():
        raise ValueError('Snapshot destination must be new and distinct')
    companions = [p for p in (source, Path(str(source)+'-wal'), Path(str(source)+'-shm')) if p.exists()]
    observed = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in companions}
    if not frozen_manifest or frozen_manifest.get('assertion') not in ('owner_frozen_bundle','owner_consistent_backup') or not frozen_manifest.get('source'):
        raise ValueError('source_snapshot_required')
    if frozen_manifest.get('files') != observed:
        raise ValueError('source_bundle_changed')
    if frozen_manifest['assertion']=='owner_consistent_backup' and len(companions)!=1:
        raise ValueError('source_snapshot_required')
    # No SQLite handle is ever opened on original source files. Only an explicitly
    # owner-frozen complete bundle may be copied, then recovered via real backup.
    validate_layout(source, destination, manifest=frozen_manifest)
    destination.parent.mkdir(parents=True, exist_ok=False)
    protect_private_root(destination.parent)
    working = Path(tempfile.mkdtemp(prefix='sqlite-acquisition-',dir=destination.parent))
    for original in companions:
        target=working/original.name
        with original.open('rb') as src, target.open('xb') as dst:
            while chunk:=src.read(1024*1024):dst.write(chunk)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=observed[original.name]:raise ValueError('source_bundle_changed')
    if {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in companions} != observed:raise ValueError('source_bundle_changed')
    source=working/source.name
    deadline = time.monotonic() + timeout
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('SQLite snapshot acquisition timed out')
    # mode=ro uses SQLite's WAL-aware backup, never immutable/base-file copying.
    with destination.open('xb'):
        pass
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=min(timeout, 3)) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity check failed')
            tables = {}
            for (name,) in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                quoted = '"' + name.replace('"', '""') + '"'
                tables[name] = {'count': dst.execute(f'SELECT count(*) FROM {quoted}').fetchone()[0],
                    'columns': [r[1:3] for r in dst.execute(f'PRAGMA table_info({quoted})')], 'disposition': 'pending_mapping'}
    return {'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'tables': tables, 'consistent': True}


def export_snapshot(snapshot,destination,classification,source,attachment_manifest=None,attachment_root=None):
    """Export every table/column from an acquired snapshot, including unknown ones.

    This is a pending source export, not a claim that bank ownership is confirmed.
    No old SQLite session/queue becomes a native executable authorization.
    """
    from app.migration.source import scrub,digest
    if classification not in ('synthetic','production_supplied') or not source:raise ValueError('source_classification_required')
    snapshot=checked_path(snapshot);destination=checked_path(destination)
    if destination.exists():raise ValueError('Export destination must be new')
    if Path(str(snapshot)+'-wal').exists():raise ValueError('consistent_acquired_snapshot_required')
    if snapshot.is_relative_to(destination) or (attachment_root and (checked_path(attachment_root).is_relative_to(destination) or destination.is_relative_to(checked_path(attachment_root)))):
        raise ValueError('source_output_overlap')
    destination.mkdir(parents=True,exist_ok=False)
    protect_private_root(destination)
    rows={};maps=[];issues=[]
    def field_maps(value,pointer):
        maps.append({'pointer':pointer,'target':'pending_110_source','value_hash':digest(value)})
        if isinstance(value,dict):
            for key,child in value.items():field_maps(child,pointer+'/'+str(key).replace('~','~0').replace('/','~1'))
        elif isinstance(value,list):
            for i,child in enumerate(value):field_maps(child,pointer+'/'+str(i))
    def value(v):return {'binary_base64':base64.b64encode(v).decode()} if isinstance(v,bytes) else v
    with sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            quoted='"'+name.replace('"','""')+'"'
            columns=list(db.execute(f'PRAGMA table_info({quoted})'))
            primary=[c['name'] for c in sorted(columns,key=lambda c:c['pk']) if c['pk']]
            observed=[]
            for index,row in enumerate(db.execute(f'SELECT * FROM {quoted}')):
                raw={k:value(row[k]) for k in row.keys()};identity=json.dumps([raw[k] for k in primary],sort_keys=True) if primary else f'observation:{index}'
                pointer=f'/tables/{name}/{index}';safe=scrub(raw)
                # Known messages.session is a routing label, never a login token.
                if name=='messages' and isinstance(raw.get('session'),str):safe['session']=raw['session']
                authorization=any(part in name.lower() for part in ('session','token','login','otp'))
                if authorization:safe={'excluded_authority':True,'column_names':list(raw)}
                state='historical_nonexecutable' if any(part in name for part in ('outbox','job','queue','operation','followup')) else 'pending_mapping'
                observed.append({'identity':identity,'pointer':pointer,'raw':safe,'state':state})
                field_maps(safe,pointer)
            rows[name]=observed
            issues.append({'table':name,'code':'native_ownership_mapping_required','count':len(observed)})
    files=[]
    for entry in attachment_manifest or []:
        if attachment_root is None:raise ValueError('Attachment root required')
        root=Path(attachment_root).resolve();path=(root/entry['relative_path']).resolve()
        if not path.is_relative_to(root) or path.is_symlink():raise ValueError('Attachment outside controlled root')
        body=path.read_bytes();sha=hashlib.sha256(body).hexdigest()
        if sha!=entry['sha256']:raise ValueError('Source attachment changed')
        target=destination/'objects'/sha[:2]/sha;target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():target.write_bytes(body)
        files.append(dict(entry,object_path=str(target.relative_to(destination))))
    result={'source':source,'classification':classification,'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),'rows':rows,'maps':maps,'issues':issues,'files':files,'external_work':'paused','state':'pending_native_mapping'}
    (destination/'source.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return {'tables':{k:len(v) for k,v in rows.items()},'files':len(files),'state':result['state']}
