"""Publish only selected non-sensitive statistics, never raw payloads/configs."""
import json,hashlib,re
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads(path.read_text('utf-8-sig'))

def test_receipt(name):
    path=ROOT/'private/ui-validation'/name
    if not path.exists():return {'state':'not_recorded'}
    content=path.read_text('utf-8-sig',errors='replace')
    summaries=[line.strip() for line in content.splitlines() if re.match(r'^(?:=+\s*)?\d+ (?:passed|failed|warnings?|errors?)\b',line.strip())]
    text='; '.join(summaries)
    counts={key:int(found[-1]) if (found:=re.findall(r'(\d+) '+key+r's?\b',text)) else 0 for key in ['passed','failed','error','warning']}
    complete=bool(re.search(r'\d+ passed.*(?:in |\()',text))
    return {'state':'passed' if complete and not counts['failed'] and not counts['error'] else 'failed_or_incomplete','summary':text or 'run_not_complete','counts':counts,'log_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
def main():
    profile=Path((ROOT/'private/benchmarks/latest-profile.txt').read_text('utf-8').strip());root=profile.parent
    groups=[]
    for filename in ['http-25000-authorized-sessions.json','http-125000-indexed.json']:
        data=read(root/filename)
        safe={key:value for key,value in data.items() if key in {'tickets','sessions','population','label','elapsed_seconds','api_peak_sampled_working_set_bytes','memory_sample_count','results','source_digest','warmup_requests','login_barrier','pg_peak_sampled_working_set_bytes','memory_definition','request_count','total_errors'}}
        safe['receipt_sha256']=hashlib.sha256((root/filename).read_bytes()).hexdigest();safe['version_note']='Source digest recorded at run start' if data.get('source_digest') else 'Complete source digest was not captured at run start; no retrospective hash invented'
        groups.append(safe)
    recovery=[]
    for filename in ['recovery-125000.json','recovery-125000-streaming.json']:
        data=read(root/filename);recovery.append({'label':filename,'tickets':data['tickets'],'backup':{k:v for k,v in data['backup'].items() if k!='pids'},'restore':{k:v for k,v in data['restore'].items() if k!='pids'},'record_bytes':data['record_bytes'],'verified':data['verification']['verified'],'runtime_fence':data['verification']['runtime_fence'],'schema_revision':data['verification']['revision'],'receipt_sha256':hashlib.sha256((root/filename).read_bytes()).hexdigest()})
    exported=read(root/'export-125000-tree.json');exported['worker'].pop('pids',None)
    snapshot=read(Path((ROOT/'private/snapshot-delivery/latest.txt').read_text('utf-8'))/'receipt.json')
    runtime=read(ROOT/'private/ui-validation/snapshot-browser.json')
    http=read(ROOT/'private/ui-validation/http-acceptance.json')
    scan=read(ROOT/'private/ui-validation/public-content-scan.json')
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'Local synthetic performance and supplied package snapshot; not production/external acceptance','benchmark':groups,'recovery':recovery,'queued_export':exported,'supplied_snapshot':{key:snapshot[key] for key in ['schema_head','counts','backup_seconds','restore_seconds','records_sha256','external_calls']},'snapshot_restore_verified':snapshot['verification']['verified'],'snapshot_restore_frozen':snapshot['verification']['runtime_fence'],'normal_runtime_browser':runtime,
        'full_application_tests':{'backend_first':test_receipt('backend-final.log'),'backend_metadata_fixed':test_receipt('backend-final-fixed.log'),'backend_connection_same_config_retry':test_receipt('backend-connection-retry.log'),'backend_final_isolated':test_receipt('backend-final-isolated.log'),'browser_first':test_receipt('browser-final.log'),'browser_same_timeout_retry':test_receipt('browser-final-retry.log'),'browser_final_stable':test_receipt('browser-final-stable.log'),'http':{k:v for k,v in http.items() if k!='synthetic_ids'},'build':{'status':'passed','javascript_bytes':311250,'javascript_gzip_bytes':99510,'css_bytes':11040,'css_gzip_bytes':3240,'sizes':'Vite rounded report, not exact file lengths','log_sha256':hashlib.sha256((ROOT/'private/ui-validation/build-final.log').read_bytes()).hexdigest()},'upstream_warning':'Starlette TestClient references deprecated AnyIO BlockingPortal alias; retained, not silenced'},
        'source_freeze':'source-freeze.json','ui_finish':'UI实现审阅记录.md','known_secret_scan':{k:v for k,v in scan.items() if k not in {'files_with_match','redacted_match_review'}},'diagnostic_boundary':'验证边界.md',
        'package_reconstruction':{'receipt':'MCSTAR-110-ALL-validation.json distributed alongside the exact ZIP','binding':'Companion receipt contains exact package SHA256, fresh-directory setup/build, isolated restore and HTTP guards. No self-referential package hash is embedded here.'},
        'memory_limitations':'Sampled process-tree Working Set; shared database pages may be counted per process. Not machine-wide or unique physical RSS.'}
    (ROOT/'docs/acceptance/运行验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print('Published sanitized local runtime and performance statistics.')
if __name__=='__main__':main()
