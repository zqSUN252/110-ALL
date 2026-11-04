"""Build a source/build/docs ZIP using an explicit public-root allowlist."""
import hashlib,json,subprocess,zipfile
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
ALLOWED={'backend','web','tools','docs','.impeccable'}
ROOT_FILES={'AGENTS.md','README.md','PRODUCT.md','DESIGN.md','.gitignore'}
EXCLUDED={'private','runtime','data','backups','artifacts','.git','.venv','node_modules','__pycache__','.pytest_cache','test-results','playwright-report','review','build'}
def included(name):
    path=PurePosixPath(name)
    return not path.is_absolute() and '..' not in path.parts and not set(path.parts)&EXCLUDED and (path.parts[0] in ALLOWED or name in ROOT_FILES) and path.suffix.lower() not in {'.pyc','.log','.zip','.sqlite3','.db','.key','.pem','.tsbuildinfo'}
def main():
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode('utf-8').split('\0')
    names+= [p.relative_to(ROOT).as_posix() for p in (ROOT/'web/dist').rglob('*') if p.is_file()]
    files=sorted({n for n in names if n and included(n) and (ROOT/n).is_file()})
    if not {'web/dist/index.html','web/package-lock.json','backend/requirements.lock','tools/start.ps1'}<=set(files):raise ValueError('Delivery inputs missing')
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'source_worktree_clean':not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip(),'files':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files},'excluded':'private business/configuration/data, secrets, git, environments, dependencies, scratch reviews'}
    output=ROOT/'artifacts'/('MCSTAR-110-ALL-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.zip');output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for n in files:archive.write(ROOT/n,n)
        archive.writestr('package-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:raise ValueError('ZIP integrity failed')
        if any(not included(n) and n!='package-manifest.json' for n in archive.namelist()):raise ValueError('Unsafe package entry')
    receipt={'path':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'entries':len(files)+1}
    (ROOT/'private/package-latest.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps(receipt))
if __name__=='__main__':main()
