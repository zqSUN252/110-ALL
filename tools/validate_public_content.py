"""Compare known private secrets in memory; emit aggregate results only."""
import argparse,hashlib,json,re,subprocess,sys,zipfile
from pathlib import Path
from urllib.parse import urlsplit,unquote
from package import ROOT,included

# Manually inspected redacted contexts: layout numeric dimensions, RFC header
# alphabet and explicitly synthetic test sequences. Hash binding forces a new
# review when any such file changes; substring matches remain in the receipt.
REVIEWED={
 '.impeccable/surfaces/public-welcome.md':'f18795df13216c57ee5952549ebc969be833a54b114579f4dfb9d9d4f98d2069',
 'DESIGN.md':'75ea6507a230364f48b31781593ffa35e371708a44b3ccf2cab38134a0237753',
 'backend/app/connections/transport.py':'2fc5119811e064983df63315118b8b88cbab2f24f25a0f25340fa2086ba9a077',
 'backend/tests/test_assistant.py':'82eb6fded183f6c1eb2af248c41cfd20c9effc8b5ef4e6b56098e58a37d94dc5',
 'backend/tests/test_assistant_edges.py':'d3a8edbee43663795afa76d805323f42aa181b9a40b9d72a05ee8d6f3ed23654',
 'backend/tests/test_assistant_review.py':'d81eb0a2b47c67039cefe3e371d08a621eeb012ccfb636a4beca88506c5cd87c',
 'backend/tests/test_migration.py':'9b48840132a5f37b213b7d092f020599ed0b655c253c7c39c19c79671b154295',
 'web/dist/assets/index-CCyS9R0m.css':'b61aeace7c203da4e91016b471db161dad7bda3118ec215fc5e356951af56f19',
 'web/src/style.css':'1ff670f4699caf8578c5c0dee47a5b620a9330bc80abc935bfb1cddf3a7aee21',
}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path,required=True);args=parser.parse_args()
    secrets=set()
    def collect(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.lower() in {'pass','password','pwd','secret','secretkey','recoverykey','phone_provider_token'} and isinstance(item,str) and len(item)>=4:secrets.add(item)
                if key=='tokens' and isinstance(item,dict):secrets.update(item)
                if key=='database_url' and isinstance(item,str):
                    password=urlsplit(item).password
                    if password and len(password)>=4:secrets.add(unquote(password))
                if isinstance(item,(dict,list)):collect(item)
                elif isinstance(item,str) and item[:1] in '[{':
                    try:collect(json.loads(item))
                    except (ValueError,TypeError):pass
        elif isinstance(value,list):
            for item in value:collect(item)
    with zipfile.ZipFile(args.archive) as archive:
        for name in archive.namelist():
            if name.endswith('.json'):collect(json.loads(archive.read(name)))
        words=re.split(r'[\s/:：]+',archive.read('mcstar_pkg/credentials/admin_cred.txt').decode('utf-8').strip())
        if len(words)>=2 and len(words[-1])>=4:secrets.add(words[-1])
        secrets.update(re.findall(r'MCSTAR-RECOVER-[\w-]+',archive.read('mcstar_pkg/source/server.js').decode('utf-8')))
    for name in ['private/local-review-final.json','private/supplied-snapshot-runtime.json','private/runtime.json']:
        if (ROOT/name).exists():collect(json.loads((ROOT/name).read_text('utf-8-sig')))
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode('utf-8').split('\0')
    names += [p.relative_to(ROOT).as_posix() for p in (ROOT/'web/dist').rglob('*') if p.is_file()]
    files=sorted({n for n in names if n and included(n) and (ROOT/n).is_file()})
    matches=[];details=[]
    for name in files:
        data=(ROOT/name).read_bytes()
        matched=[value for value in secrets if isinstance(value,str) and len(value)>=4 and value.encode('utf-8') in data]
        if matched:
            matches.append(name)
            text=data.decode('utf-8',errors='replace')
            for line_number,line in enumerate(text.splitlines(),1):
                hits=[value for value in matched if value in line]
                if hits:
                    position=min(line.index(value) for value in hits);snippet=line[max(0,position-50):position+100]
                    for value in secrets:
                        if isinstance(value,str) and len(value)>=4:snippet=snippet.replace(value,'<redacted>')
                    details.append({'file':name,'line':line_number,'lengths':[len(value) for value in hits],'redacted_context':snippet})
    unreviewed=[name for name in matches if REVIEWED.get(name)!=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()]
    result={'files_checked':len(files),'known_secret_substring_matches':len(matches),'reviewed_nonsecret_collisions':len(matches)-len(unreviewed),'unreviewed_matches':len(unreviewed),'scope':'Known original archive and controlled local credentials only; not proof of absence of every possible secret or business value','files_with_match':matches,'redacted_match_review':details}
    (ROOT/'private/ui-validation/public-content-scan.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key not in {'files_with_match','redacted_match_review'}}))
    return bool(unreviewed)
if __name__=='__main__':sys.exit(main())
