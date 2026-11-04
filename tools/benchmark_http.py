"""Measured HTTP latency with ten authorized synthetic sessions, never production."""
import argparse,concurrent.futures,ctypes,hashlib,json,math,statistics,sys,threading,time,subprocess
from pathlib import Path
from uuid import uuid4
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.settings import load_settings
from app.db import make_engine
from sqlalchemy import text


def bench_id(table,n):return str(__import__('uuid').UUID(hashlib.md5(('bench-'+table+'-'+str(n)).encode()).hexdigest()))


def memory(pid):
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(name,ctypes.c_size_t) for name in ['peak_ws','ws','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile']]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.restype=ctypes.c_void_p;kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x1000|0x10,False,pid)
    if not handle:return None
    value=Counters();value.cb=ctypes.sizeof(value)
    psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
    ok=psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),value.cb);kernel.CloseHandle(handle)
    return value.ws if ok else None


def process_pairs():
    """Cheap native process snapshot, including Windows virtualenv children."""
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),('name',wintypes.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype=ctypes.c_void_p
    kernel.Process32FirstW.argtypes=[ctypes.c_void_p,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[ctypes.c_void_p,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0);entry=Entry();entry.size=ctypes.sizeof(entry);result=[]
    try:
        success=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while success:
            result.append({'ProcessId':entry.pid,'ParentProcessId':entry.parent});success=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--tickets',type=int,required=True);p.add_argument('--base',default='http://127.0.0.1:8017');p.add_argument('--label',default='initial');p.add_argument('--round-offset',type=int,default=0);p.add_argument('--read-only',action='store_true',help='Only the existing 50-row ticket list/search workload; no review or bank mutations');a=p.parse_args()
    settings=load_settings();root=settings.private_root.parent;engine=make_engine(settings.database_url)
    with engine.connect() as db:
        if not db.scalar(text('select current_schema()')).startswith('test_bench_'):raise ValueError('Fresh owned benchmark schema only')
        if db.scalar(text("select count(*) from tickets where receipt like 'BENCH-%'"))!=a.tickets:raise ValueError('Population mismatch')
    from urllib.parse import urlparse
    ids=json.loads((root/'seed-ids.json').read_text());pid=int((ROOT/('private/benchmarks/server'+str(urlparse(a.base).port)+'.pid')).read_text());samples=[];stop=threading.Event()
    process_rows=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command','Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress'],encoding='utf-8'))
    def descendants(root_pid,rows):
        found={root_pid}
        for _ in range(5):found.update(r['ProcessId'] for r in rows if r['ParentProcessId'] in found)
        return found
    pids=descendants(pid,process_rows)
    pg_pid=int((ROOT/'private/postgres17/postmaster.pid').read_text().splitlines()[0])
    pg_pids=descendants(pg_pid,process_rows);pg_samples=[]
    files=sorted((ROOT/'backend/app').rglob('*.py'))+sorted((ROOT/'backend/migrations/versions').glob('*.py'))+[Path(__file__)]
    source_hashes={str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    source_digest=hashlib.sha256(json.dumps(source_hashes,sort_keys=True).encode()).hexdigest()
    def sample():
        while not stop.wait(.05):
            values=[memory(pid) for pid in pids]
            if any(v is not None for v in values):samples.append(sum(v or 0 for v in values))
            pg_values=[memory(pid) for pid in pg_pids]
            if any(v is not None for v in pg_values):pg_samples.append(sum(v or 0 for v in pg_values))
    monitor=threading.Thread(target=sample);monitor.start();results=[];authenticated=threading.Barrier(10)
    def session(index):
        client=httpx.Client(base_url=a.base,timeout=60);login=client.post('/api/auth/login',json={'username':'bench'+str(index),'password':'synthetic-benchmark-password'})
        assert login.status_code==200,'synthetic authentication failed'
        headers={'X-CSRF-Token':login.json()['csrf_token']};stats=[]
        # This workload is explicitly ten already-authorized sessions. Keep
        # login's Argon2 CPU/memory wave outside timed business requests.
        authenticated.wait(timeout=60)
        def timed(kind,method,path,body=None,key=None):
            start=time.perf_counter()
            try:
                response=client.request(method,path,json=body,headers=headers|{'Idempotency-Key':key or uuid4().hex})
                stats.append({'kind':kind,'seconds':time.perf_counter()-start,'status':response.status_code,'bytes':len(response.content),'error':response.json().get('error',{}).get('code') if response.status_code>=400 else None})
                return response
            except httpx.HTTPError:
                stats.append({'kind':kind,'seconds':time.perf_counter()-start,'status':0,'bytes':0});return None
        for iteration in range(6):
            timed('ticket_list_50','GET','/api/tickets?limit=50&offset='+str(iteration*50))
            timed('ticket_search_50','GET','/api/tickets?limit=50&receipt=BENCH-001')
            if a.read_only:continue
            timed('bank_search_50','GET','/api/workspace/bank-entries?account_id='+ids['account']+'&query=BENCH-BANK-1&limit=50')
            ticket=bench_id('tickets',index*100+iteration+1);current=client.get('/api/tickets/'+ticket).json();key=uuid4().hex
            body={'expected_version':current['version'],'decision':'approved'}
            first=timed('ticket_review_submit','POST','/api/tickets/'+ticket+'/review',body,key)
            replay=timed('ticket_review_idempotent','POST','/api/tickets/'+ticket+'/review',body,key)
            if first is not None and replay is not None:assert first.status_code==replay.status_code and first.json()==replay.json(),'idempotency result mismatch'
            case=bench_id('evidence_cases',index*100+a.round_offset+iteration+1)
            case_record=client.get('/api/evidence/'+case).json()
            timed('bank_match_50_candidates','POST','/api/bank/cases/'+case+'/match',{'expected_version':case_record['version']})
            # Exact human-selected original among legitimate equal-value rows;
            # match remains ambiguous, never silently promoted to unique cash.
            entry=bench_id('bank_entries',index*100+a.round_offset+iteration+1)
            body={'expected_version':case_record['version'],'entry_id':entry,'entry_version':1,'action':'confirm_manual','reason':'Synthetic reviewed original source selects this distinct transaction'}
            key=uuid4().hex
            first=timed('bank_review_submit','POST','/api/bank/cases/'+case+'/review',body,key)
            replay=timed('bank_review_idempotent','POST','/api/bank/cases/'+case+'/review',body,key)
            if first is not None and replay is not None:assert first.status_code==replay.status_code and first.json()==replay.json(),'bank idempotency mismatch'
        client.close();return stats
    started=time.perf_counter()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            for result in pool.map(session,range(10)):results.extend(result)
    finally:stop.set();monitor.join()
    groups={}
    for kind in sorted({r['kind'] for r in results}):
        selected=[r for r in results if r['kind']==kind];times=sorted(r['seconds'] for r in selected)
        groups[kind]={'requests':len(times),'p50_seconds':statistics.median(times),'p95_seconds':times[math.ceil(.95*len(times))-1],'max_seconds':max(times),'errors':sum(r['status']!=200 for r in selected),'error_codes':sorted({r.get('error') or 'transport' for r in selected if r['status']!=200}),'response_bytes_max':max(r['bytes'] for r in selected)}
    with engine.connect() as db:
        invariants={'stock_events':db.scalar(text("select count(*) from stock_events where receipt_id in (select id from receipts where series='BENCH')")),
            'bank_entries':db.scalar(text("select count(*) from bank_entries where reference like 'BENCH-BANK-%'")),
            'duplicate_active_claims':db.scalar(text("select count(*) from (select entry_id from bank_claims where state<>'released' group by entry_id having count(*)>1) s")),
            'review_facts':db.scalar(text("select count(*) from ticket_reviews where ticket_id in (select id from tickets where receipt like 'BENCH-%')"))}
        notification_population={'members':db.scalar(text('select count(*) from notification_members')),'active_loading_copies':db.scalar(text("select count(*) from notification_copy_events c join notifications n on n.id=c.notification_id where c.cancelled_at is null and n.kind='loading'"))}
    report={'tickets':a.tickets,'sessions':10,'population':json.loads((root/('population-'+str(a.tickets)+'.json')).read_text()),'label':a.label,'elapsed_seconds':time.perf_counter()-started,'api_peak_sampled_working_set_bytes':max(samples) if samples else None,'memory_sample_count':len(samples),'results':groups,'invariants':invariants,'external_calls':0}
    report.update(source_digest=source_digest,source_hashes=source_hashes,notification_population=notification_population,warmup_requests=0,authentication_barrier=True,round_offset=a.round_offset,read_only=a.read_only,request_count=len(results),total_errors=sum(r['status']!=200 for r in results),api_pids=sorted(pids),postgres_pids=sorted(pg_pids),postgres_peak_sampled_working_set_bytes=max(pg_samples) if pg_samples else None,memory_definition='50ms sampled process-tree working-set sum; PostgreSQL shared pages may be counted per process; not unique resident memory or whole-machine peak')
    destination=root/('http-'+str(a.tickets)+'-'+a.label+'.json');destination.write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='source_hashes'}));engine.dispose()


if __name__=='__main__':main()
