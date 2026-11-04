"""Publish explicit, aggregate Task6 fix1 receipts; never copy payloads or secrets."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from publish_acceptance_evidence import test_receipt

ROOT=Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(path.read_text('utf-8-sig'))

def main():
    backend=test_receipt('fix1-backend-full.log')
    browser=test_receipt('fix1-browser-final.log')
    assert backend['state']=='passed' and browser['state']=='passed','Final whole-suite receipts required'
    freeze=read(ROOT/'docs/acceptance/source-freeze-fix1-final.json')
    for name,digest in freeze['files'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,'Freeze changed: '+name
    profile=Path((ROOT/'private/benchmarks/latest-profile.txt').read_text('utf-8').strip())
    bench=read(profile.parent/'http-125000-fix1-readonly.json')
    assert bench['read_only'] and bench['total_errors']==0
    benchmark={k:bench[k] for k in ['tickets','sessions','population','notification_population','label','elapsed_seconds','results','source_digest','warmup_requests','authentication_barrier','read_only','request_count','total_errors','api_peak_sampled_working_set_bytes','postgres_peak_sampled_working_set_bytes','memory_definition']}
    runtime=read(ROOT/'private/ui-validation/snapshot-browser-fix1.json')
    assert runtime['status']=='passed' and runtime['source_business_mutations']==0
    scan=read(ROOT/'private/ui-validation/public-content-scan-fix1.json')
    assert scan['unreviewed_matches']==0
    logs=['fix1-backend-red.log','fix1-options-d26-red.log','fix1-photo-red.log','fix1-export-red-corrected.log','fix1-marks-browser-red.log','fix1-backend-focused-5.log','fix1-browser-functional-1.log','fix1-browser-full-1.log','fix1-browser-full-2.log','fix1-browser-full-3.log','fix1-manual-refresh-red.log','fix1-manual-refresh-green.log']
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'task':6,'review_fix_round':1,'status':'LOCAL_CHECKS_COMPLETE_INDEPENDENT_TASK_REVIEW_PENDING','source_commit_binding':'Exact final commit is in package-manifest.json and the companion package verification receipt; runtime/test files are separately hash-frozen.','runtime_test_freeze':{'path':'source-freeze-fix1-final.json','sha256':freeze['sha256'],'files':len(freeze['files'])},'full_backend':backend,'full_browser':browser,'retained_iterations':{name:test_receipt(name) for name in logs},'iteration_classification':{'fix1-backend-red.log':'I03 nonexistent fixture function was not business RED; corrected exact-base options route reproduction is separately retained. Grants/version and marks projection were genuine RED.','fix1-browser-full-1.log':'24 passed; two old helpers assumed target in first50, corrected to UI search.','fix1-browser-full-2.log':'25 passed; same assumption in English helper, corrected to UI search.','fix1-browser-full-3.log':'25 passed; genuine existing5/10min customer IP rate limit429 after repeated runs. No threshold or attempts changed; final run after natural window expiry.','fix1-manual-refresh-red.log':'Delayed manual job refresh could replace newer task result; real current-build browser reproduction before epoch/job/source guard.'},'affected_list_search_benchmark':benchmark,'benchmark_boundary':'Only affected125k ticket list/search repeated after marker projection. Ten authenticated sessions,60 requests each kind,0warmup,50rows. Historical25k/125k bank/write/export/backup figures remain in运行验证.json with original source versions, not newly measured values.','normal_runtime':runtime,'current_snapshot_counts':read(ROOT/'private/ui-validation/fix1-snapshot-counts.json'),'benchmark_first_error':'Initial read workload completed but added population metadata query used nonexistent copy_events table; no result receipt was published. Corrected to notification_copy_events and repeated the bounded workload. Original failure log retained.','known_secret_scan':{k:v for k,v in scan.items() if k not in {'files_with_match','redacted_match_review'}},'schema_and_backup_core':'No schema/migration or backup-core change; original g2 backup remains compatible. New ZIP reconstructed and isolated restore recorded in companion receipt.','ui_evidence':'UI实现审阅记录.md; originalc97 is historical and three old contact screenshot paths were overwritten; new functional screenshots independently versioned.','M01':'Existing Starlette TestClient / AnyIO BlockingPortal deprecation retained. No dependency upgrade or warning suppression.','external_boundary':'Full146 preserved; actual production sources, missing artwork/templates, OCR accuracy, live OTP/account/message delivery, external clipboard paste, physical printing and HTTPS are not accepted by these local tests.','diagnostic_boundary':'验证边界.md; no claim the historical tool transcript contained zero payload or secrets.','package_reconstruction':'New companion receipt binds exact ZIP SHA256, source commit, fresh venv/npm ci/build,108-table isolated restore, frozen-writer and anonymous/private-path negatives. Historicald26 ZIP/receipt remain superseded records.'}
    (ROOT/'docs/acceptance/task6-fix1-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
    print('Published aggregate fix1 receipts; independent task review and external acceptance remain separate.')

if __name__=='__main__':main()
