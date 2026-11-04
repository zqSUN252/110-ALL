"""Record the exact already-inspected UI/build for independent review."""
from pathlib import Path
import hashlib,json
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
paths=[*sorted((ROOT/'web/src').glob('*')),*sorted((ROOT/'web/dist').rglob('*'))]
hashes={p.relative_to(ROOT).as_posix():digest(p) for p in paths if p.is_file()}
captures=[]
for path in sorted((ROOT/'private/ui-validation/visual-confirmation').glob('*.png')):
    width,surface=path.stem.split('-',1)
    captures.append({'path':str(path),'viewport':{'width':int(width),'height':1000},'route':'/','navigation':{'welcome':'fresh root','receipts':'入库管理 → 新增入库','tickets':'PACKING LIST → synthetic ticket → 主管复核','tickets-horizontal':'PACKING LIST → horizontal scroll end','receivables':'应收账款','evidence':'水单审核 → 添加水单','assistant':'110对话助手'}[surface],'opened_and_inspected':True,'sha256':digest(path)})
packet={'created_at':datetime.now(timezone.utc).isoformat(),'status':'UI review freeze; full Task6 verification incomplete','url':'http://127.0.0.1:8014','data':'isolated synthetic only','source_hashes':hashes,'source_digest':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),'captures':captures,'direction_contract':str(ROOT/'docs/development/ui-contract.md'),'product':str(ROOT/'PRODUCT.md'),'detector':str(ROOT/'private/ui-validation/impeccable-detector.json'),'detector_runs':1,'detector_result':[],'comp':'code-first; no approved image comp','review_boundary':'13 surfaces inspected. Confirmation re-recorded to repair premature pending-load capture and functional floating-scroll overlap; no second aesthetic iteration. No more UI edits during independent review.','known_gaps':['Exact template screenshots 543/24 and hero originals 1/2 unavailable','Real destination clipboard paste, production backup, OCR accuracy, pairing and delivery unverified','English full-flow and customer-registration browser checks still pending; provider disabled in delivered runtime','Mobile freeze group slides before pinning to keep remaining columns reachable; review narrow viewport usability'],'raster_provenance':'All 3 shipping logo rasters have source-origin metadata; scan 0 missing.'}
packet['review_fix_round']=1
packet['fix_mapping']={'1':'Frozen tail width plus selection width reserves full phone cell; real 390px bounding-box assertion passes.','2':'ui-contract now has five commitments and truthful inherited-direction override, no invented seed/comp.','3':'Hotline/Openshipment roles translated zh/en/fr; source contact values unchanged; isolated configured branch tested and restored.','4':'Welcome max-width38ch and balanced wrapping preserves original copy; desktop no three-character tail.','PARTY-04':'ContactLinks uses only active sourced explicit international raw/normalized agreement; multi-number raw preserved, no prefix guessing, no automatic sending.'}
packet['contact_captures']=[{'path':str(p),'viewport':{'width':1280,'height':720},'language':p.stem,'opened_and_inspected':True,'sha256':digest(p)} for p in sorted((ROOT/'private/ui-validation/visual-contact-proof').glob('*.png'))]
packet['focused_tests']='review-fixes.spec.ts RED 2 failed/1 passed on previous build; GREEN 3 passed after one combined review fix/build. customer.spec.ts 1 passed, english.spec.ts 1 passed.'
packet['known_gaps']=[gap for gap in packet['known_gaps'] if not gap.startswith('English')]
packet['review_boundary']='Independent review fix round1: one combined batch/build, same13 viewport captures plus3 configured contact-language captures; all opened. Detector still one prior run. Await same reviewer verdict.'
target=ROOT/'.superpowers/sdd/2026-11-04-unified-implementation/task-6-ui-review.json'
if target.exists():target.with_name('task-6-ui-review-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json').write_bytes(target.read_bytes())
target.write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'packet':str(target),'captures':len(captures),'source_digest':packet['source_digest']}))
