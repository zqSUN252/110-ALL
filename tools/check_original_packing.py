"""Read-only original workbook compatibility check; emits structural counts only.

Usage: PYTHONPATH=backend python tools/check_original_packing.py <user-supplied-zip>
No workbook bytes, customer rows, filenames, hashes or credentials are printed/written.
"""
import argparse,base64,hashlib,json
from zipfile import ZipFile
from collections import Counter
from app.packing.parser import parse


def extract(value,found):
    if isinstance(value,dict):
        for key,item in value.items():
            if key=='originalFile' and isinstance(item,dict) and isinstance(item.get('data'),str) and 'base64,' in item['data']:
                content=base64.b64decode(item['data'].split('base64,',1)[1]);found[hashlib.sha256(content).digest()]=content
            if isinstance(item,(dict,list)):extract(item,found)
            elif isinstance(item,str) and item[:1] in ('{','['):
                try:extract(json.loads(item),found)
                except (ValueError,TypeError):pass
    elif isinstance(value,list):
        for item in value:extract(item,found)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('zip');args=parser.parse_args();found={}
    with ZipFile(args.zip) as archive:
        for name in archive.namelist():
            if name.endswith('.json'):
                try:extract(json.loads(archive.read(name)),found)
                except (ValueError,TypeError):pass
    print(json.dumps({'unique_original_workbooks':len(found)}),flush=True)
    for index,content in enumerate(found.values(),1):
        try:
            rows,mapping,issues=parse(content);cargo=[r for r in rows if r['eligible']]
            result={'index':index,'recognized_sheets':len(mapping),'source_rows':len(rows),'candidate_cargo_rows':len(cargo),
                    'gross_weight_values':sum(r.get('kg') is not None for r in cargo),'usd_values':sum(r.get('freight_usd') is not None for r in cargo),'ngn_values':sum(r.get('clearing_ngn') is not None for r in cargo),
                    'issues':dict(Counter(c for r in rows for c in r['issues'])),'sheet_issues':len(issues)}
        except Exception as exc:result={'index':index,'failure_type':type(exc).__name__,'code':getattr(exc,'code',None)}
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
