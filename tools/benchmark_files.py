"""Measure real queued exports and isolated backup/restore on owned synthetic data."""
import argparse,json,os,subprocess,sys,time,hashlib
from pathlib import Path
from uuid import uuid4
from io import BytesIO
import httpx
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.settings import load_settings
from app.db import make_engine
from sqlalchemy import text
from benchmark_http import memory,process_pairs


def child(args,root,label):
    started=time.perf_counter();samples=[]
    with (root/(label+'.out.log')).open('wb') as out,(root/(label+'.error.log')).open('wb') as error:
        process=subprocess.Popen([sys.executable,*args],cwd=ROOT,stdout=out,stderr=error,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        pids={process.pid}
        while process.poll() is None:
            pairs=process_pairs()
            for _ in range(3):pids.update(r['ProcessId'] for r in pairs if r['ParentProcessId'] in pids)
            values=[memory(pid) for pid in pids]
            if any(v is not None for v in values):samples.append(sum(v or 0 for v in values))
            time.sleep(.1)
    result={'exit_code':process.returncode,'elapsed_seconds':time.perf_counter()-started,'peak_working_set_bytes':max(samples) if samples else None,'samples':len(samples),'pids':sorted(pids),'memory_definition':'100ms native process-tree working-set sum, including virtualenv child'}
    (root/(label+'-measurement.json')).write_text(json.dumps(result,indent=2))
    if process.returncode:raise RuntimeError(label+' failed; private error log retained')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['export','recovery']);p.add_argument('--base',default='http://127.0.0.1:8017');p.add_argument('--label',default='tree');a=p.parse_args()
    settings=load_settings();engine=make_engine(settings.database_url);root=settings.private_root.parent;ids=json.loads((root/'seed-ids.json').read_text())
    with engine.connect() as db:
        if not db.scalar(text('select current_schema()')).startswith('test_bench_'):raise ValueError('Owned synthetic benchmark only')
        count=db.scalar(text("select count(*) from tickets where receipt like 'BENCH-%'"))
        warehouse=db.scalar(text('select warehouse_id from tickets where id=:id'),{'id':ids['ticket']})
        receipts=db.scalar(text('select count(*) from receipts where warehouse_id=:id'),{'id':warehouse})
    if a.action=='export':
        with httpx.Client(base_url=a.base,timeout=120) as client:
            login=client.post('/api/auth/login',json={'username':'bench0','password':'synthetic-benchmark-password'});login.raise_for_status()
            started=time.perf_counter();response=client.post('/api/cargo/exports',json={'warehouse_id':warehouse,'mode':'all'},headers={'X-CSRF-Token':login.json()['csrf_token'],'Idempotency-Key':uuid4().hex});response.raise_for_status();job=response.json()
            queued=time.perf_counter()-started
            worker=child(['tools/local_jobs.py','--once'],root,'export-'+str(count)+'-'+a.label)
            ready=client.get('/api/cargo/export-jobs/'+job['id']);ready.raise_for_status();result=ready.json();assert result['status']=='complete'
            artifact=client.get('/api/cargo/exports/'+result['payload']['attachment_id']);artifact.raise_for_status()
            book=load_workbook(BytesIO(artifact.content),read_only=True,data_only=True);actual=sum(1 for _ in book.active.iter_rows())-1;book.close();assert actual==receipts
            report={'tickets':count,'queue_seconds':queued,'worker':worker,'rows':actual,'bytes':len(artifact.content),'sha256':hashlib.sha256(artifact.content).hexdigest(),'total_seconds':time.perf_counter()-started,'external_calls':0}
        (root/('export-'+str(count)+'-'+a.label+'.json')).write_text(json.dumps(report,indent=2));print(json.dumps(report))
    else:
        identity=uuid4().hex;destination=root/('backup-'+str(count)+'-'+identity)
        backed=child(['tools/backup.py','backup','--path',str(destination)],root,'backup-'+str(count)+'-'+a.label)
        schema='restore_scale_'+identity
        restored=child(['tools/backup.py','restore','--path',str(destination),'--schema',schema],root,'restore-'+str(count)+'-'+a.label)
        receipt=json.loads((root/('restore-'+str(count)+'-'+a.label+'.out.log')).read_text().strip().splitlines()[-1]);assert receipt['verified'] and receipt['runtime_fence']=='frozen'
        report={'tickets':count,'backup':backed,'restore':restored,'schema':schema,'backup_path':str(destination),'record_bytes':(destination/'records.json').stat().st_size,'verification':receipt}
        (root/('recovery-'+str(count)+'-'+a.label+'.json')).write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='verification'}))
    engine.dispose()


if __name__=='__main__':main()
