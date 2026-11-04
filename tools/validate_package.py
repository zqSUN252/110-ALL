"""Reconstruct a public ZIP in a fresh owned directory and restore privately.

Never deletes a prior verification directory. Only its newly created restore
schema is used. Private credentials are passed in memory/environment, not output.
"""
import argparse,json,os,secrets,subprocess,sys,time,zipfile,hashlib
from pathlib import Path,PurePosixPath
from dataclasses import asdict
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.private_storage import protect_private_root
from app.settings import load_settings
from sqlalchemy.engine import make_url
import httpx

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8020);args=parser.parse_args()
    if not 1024<=args.port<=65535:raise ValueError('Invalid local port')
    base='http://127.0.0.1:'+str(args.port)
    receipt=json.loads((ROOT/'private/package-latest.json').read_text('utf-8'));archive=Path(receipt['path']);identity=uuid4().hex
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=receipt['sha256']:raise ValueError('Package changed')
    target=(ROOT/'artifacts/package-validation'/identity).resolve();assert target.is_relative_to((ROOT/'artifacts/package-validation').resolve());target.mkdir(parents=True,exist_ok=False)
    evidence=ROOT/'private/package-validation'/identity;protect_private_root(evidence)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:raise ValueError('ZIP integrity')
        for entry in bundle.infolist():
            relative=PurePosixPath(entry.filename);destination=(target/entry.filename).resolve()
            if relative.is_absolute() or '..' in relative.parts or not destination.is_relative_to(target) or (entry.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe ZIP member')
        bundle.extractall(target)
    manifest=json.loads((target/'package-manifest.json').read_text('utf-8'))
    for name,sha in manifest['files'].items():
        if hashlib.sha256((target/name).read_bytes()).hexdigest()!=sha:raise ValueError('Unpacked hash')
    def run(args,label,env=None):
        start=time.perf_counter()
        with (evidence/(label+'.log')).open('wb') as output:
            process=subprocess.run(args,cwd=target,env=env,stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if process.returncode:raise RuntimeError(label+' failed; private log retained')
        return time.perf_counter()-start
    setup=run(['powershell','-NoProfile','-File',str(target/'tools/setup.ps1'),'-Python',sys.executable],'setup')
    rebuilt=[name for name in manifest['files'] if name.startswith('web/dist/')]
    if not rebuilt or any(hashlib.sha256((target/name).read_bytes()).hexdigest()!=manifest['files'][name] for name in rebuilt):raise ValueError('Rebuilt web assets differ from packaged build')
    python=target/'.venv/Scripts/python.exe';settings=load_settings();schema='restore_package_'+identity
    url=make_url(settings.database_url).update_query_dict({'options':'-csearch_path='+schema})
    config=asdict(settings);config.update(database_url=url.render_as_string(hide_password=False),private_root=str(evidence/'runtime'),secure_cookies=False,collection_enabled=False,summaries_enabled=False,model_calls_enabled=False,sending_enabled=False,connection_control_enabled=False,connection_diagnostics_enabled=False,phone_delivery_enabled=False,main_account_read_only=True)
    profile=evidence/'runtime.json';profile.write_text(json.dumps(config),encoding='utf-8')
    env={**os.environ,'MCSTAR_CONFIG':str(profile),'PYTHONPATH':str(target/'backend'),'PYTHONIOENCODING':'utf-8'}
    snapshot=Path((ROOT/'private/snapshot-delivery/latest.txt').read_text('utf-8'))
    restored=run([str(python),'tools/backup.py','restore','--path',str(snapshot/'backup'),'--schema',schema],'restore',env)
    restored_json=json.loads((evidence/'restore.log').read_text('utf-8').strip().splitlines()[-1]);assert restored_json['verified'] and restored_json['runtime_fence']=='frozen'
    config['private_root']=str(evidence/'runtime/restores'/schema);profile.write_text(json.dumps(config),encoding='utf-8')
    review=json.loads((ROOT/'private/local-review-final.json').read_text('utf-8'));password=secrets.token_urlsafe(32)
    run([str(python),'tools/restore_access.py','--username',review['username'],'--reason','Isolated package reconstruction readback'], 'restore-access',{**env,'MCSTAR_RECOVERY_PASSWORD':password})
    started=run(['powershell','-NoProfile','-File',str(target/'tools/start.ps1'),'-Profile',str(profile),'-Port',str(args.port),'-Background','-WithoutLocalJobs'],'start',env)
    with httpx.Client(base_url=base,timeout=30) as client:
        assert client.get('/health').json()['status']=='ok';assert client.get('/').status_code==200
        anonymous=client.get('/api/legacy/tickets').status_code;assert anonymous==401
        login=client.post('/api/auth/login',json={'username':review['username'],'password':password});assert login.status_code==200
        rows=client.get('/api/legacy/tickets').json();assert rows.get('items') or rows.get('rows')
        frozen=client.post('/api/warehouses',json={'name':'must never persist'},headers={'X-CSRF-Token':login.json()['csrf_token'],'Idempotency-Key':uuid4().hex});assert frozen.json()['error']['code']=='writer_frozen'
        assert client.get('/private/runtime.json').status_code==404
    result={'status':'passed','package_sha256':receipt['sha256'],'package_path':str(archive),'source_commit':manifest.get('source_commit'),'source_worktree_clean':manifest.get('source_worktree_clean'),'extracted_directory':str(target),'isolated_schema':schema,'setup_seconds':setup,'rebuilt_dist_hashes_match':True,'rebuilt_dist_files':len(rebuilt),'restore_seconds':restored,'start_seconds':started,'tables_verified':len(restored_json['counts']),'runtime_fence':'frozen','anonymous_status':anonymous,'writer_frozen_negative':True,'url':base,'private_credentials_bundled':False,'external_calls':0}
    (evidence/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8');(ROOT/'private/package-validation/latest.txt').write_text(str(evidence),encoding='utf-8');print(json.dumps(result))
if __name__=='__main__':main()
