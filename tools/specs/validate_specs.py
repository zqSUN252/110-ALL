"""Verify design artifacts/traceability and unchanged inputs, never run business code."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

from requirements_catalog import requirements

ROOT = Path(__file__).resolve().parents[2]


def validate(archive_path):
    checks = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    req = json.loads((ROOT/'docs/requirements/requirements.json').read_text(encoding='utf-8'))
    trace = json.loads((ROOT/'docs/requirements/提示词追溯.json').read_text(encoding='utf-8'))
    inv = json.loads((ROOT/'docs/migration/来源盘点.json').read_text(encoding='utf-8'))
    check('146个唯一需求ID', len(req)==146 and len({r['id'] for r in req})==146)
    check('清单覆盖578段且编号连续', [r['prompt'] for r in trace] == list(range(1,579)))
    check('所有需求均有验收条件及原始或本轮依据', all(r['acceptance'] and (r['prompts'] or r['source_kind']=='current_user_command') for r in req))
    check('所有来源行号有效', all(1<=r['source_start_line']<=r['source_end_line'] for r in trace))
    lookup = {r['id']:r for r in req}
    check('全部追溯ID存在', all(k in lookup for r in trace for k in r['requirements']))
    check('双向提示词追溯一致', all(r['id'] in trace[n-1]['requirements'] for r in req for n in r['prompts']))
    check('摘要和工具通知不作为新增命令', Counter(r['kind'] for r in trace)['会话摘要']==30 and
          Counter(r['kind'] for r in trace)['工具通知']==3 and
          all(not r['requirements'] for r in trace if r['kind'] in {'会话摘要','工具通知'}))
    with (ROOT/'docs/requirements/提示词逐条追溯.csv').open(encoding='utf-8-sig',newline='') as file:
        csv_rows = list(csv.reader(file))
    check('CSV包含表头及578行且不复制原提示词正文', len(csv_rows)==579 and all(len(r)==7 for r in csv_rows))
    mandatory = (ROOT/'docs/requirements/必须遵守的功能清单.md').read_text(encoding='utf-8')
    matrix = (ROOT/'docs/acceptance/验收矩阵.md').read_text(encoding='utf-8')
    check('每项在功能文档有独立条目', all(f"### {r['id']} " in mandatory for r in req))
    check('每项在验收矩阵有独立行', all(f"| {r['id']} |" in matrix for r in req))
    check('19个验收场景完整', all(f'| E{n:02d} |' in matrix for n in range(1,20)))

    markdown = [ROOT/'README.md',ROOT/'AGENTS.md',*sorted((ROOT/'docs').rglob('*.md'))]
    broken = []
    for file in markdown:
        text = file.read_text(encoding='utf-8')
        for target in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)',text):
            if re.match(r'^[a-zA-Z]+://',target) or target.startswith('#'):
                continue
            path = target.strip('<>').split('#')[0]
            if not (file.parent/path).resolve().exists():
                broken.append(str(file.relative_to(ROOT))+':'+target)
        check('代码围栏成对:'+str(file.relative_to(ROOT)), len(re.findall(r'^```',text,re.M))%2==0)
    check('全部文档本地链接可解析', not broken)

    latest = inv['snapshots'][0]
    check('来源原包哈希未变', hashlib.sha256(archive_path.read_bytes()).hexdigest()==inv['archive_sha256']==
          '4c47a5ffb612026bbc6944dc4729a6f7f5d07f2cf2df95c9a26b155f0c51fb40')
    check('包内最新计数与版本一致', latest['version']==101908 and latest['ticket_rows']==964 and
          latest['unique_nonempty_receipt_numbers']==917 and latest['slips']==60 and latest['approved_slips']==57 and
          latest['slips_missing_uid']==55 and latest['bill_types']=={'ar':604,'ap':15} and
          latest['bill_partner_orphans']==12 and latest['bill_shipment_orphans']==42)
    mapping = (ROOT/'docs/migration/数据回填方案.md').read_text(encoding='utf-8')
    check('最新23键在迁移映射逐项出现', len(latest['key_counts'])==23 and all('`'+k+'`' in mapping for k in latest['key_counts']))
    check('110参考源码指纹未变', all(hashlib.sha256((Path(r'D:\work\110')/r['path']).read_bytes()).hexdigest()==r['sha256']
          for r in inv['reference_110_files']))

    # Match known credentials in memory; only aggregate status is ever emitted.
    secret_values = set()
    def collect(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key.lower() in {'pass','password','pwd','secret','secretkey','recoverykey'} and isinstance(item,str) and len(item)>=4:
                    secret_values.add(item)
                if key=='tokens' and isinstance(item,dict):
                    secret_values.update(item.keys())
                if isinstance(item,(dict,list)):
                    collect(item)
                elif isinstance(item,str) and item[:1] in '[{':
                    try:
                        collect(json.loads(item))
                    except (ValueError,TypeError):
                        pass
        elif isinstance(value,list):
            for item in value:
                collect(item)
    with zipfile.ZipFile(archive_path) as z:
        for member in z.namelist():
            if member.endswith('.json'):
                collect(json.loads(z.read(member)))
        credential = z.read('mcstar_pkg/credentials/admin_cred.txt').decode('utf-8')
        parts = re.split(r'[\s/:：]+',credential.strip())
        if len(parts)>=2 and len(parts[-1])>=4:
            secret_values.add(parts[-1])
        server = z.read('mcstar_pkg/source/server.js').decode('utf-8')
        secret_values.update(re.findall(r'MCSTAR-RECOVER-[\w-]+',server))
    owned = [ROOT/'.gitignore', *markdown, *sorted((ROOT/'docs').rglob('*.json')),
             *sorted((ROOT/'docs').rglob('*.csv')), *sorted((ROOT/'tools/specs').glob('*.py'))]
    hits = []
    for file in owned:
        value = file.read_text(encoding='utf-8-sig')
        if any(s and len(s)>=4 and s in value for s in secret_values):
            hits.append(str(file.relative_to(ROOT)))
    check('新文档及工具无原包已知口令/会话/恢复秘密', not hits)
    for file in (ROOT/'tools/specs').glob('*.py'):
        compile(file.read_text(encoding='utf-8'),str(file),'exec')
    check('文档工具Python语法有效', True)
    check('未把应用验收或生产迁移标为完成', inv['production_migration_executed'] is False and '尚未完成正式业务验收或生产迁移' in (ROOT/'README.md').read_text(encoding='utf-8'))
    report = {'date':'2026-11-04','scope':'仅文档、追溯与来源完整性；不是应用验收',
              'checks_passed':len(checks),'checks':checks,'requirements':len(req),'prompt_sections':len(trace),
              'known_secrets_found':False,'source_archive_unchanged':True,'reference_110_unchanged':True,
              'application_code_present':(ROOT/'backend/app/main.py').exists(),
              'application_complete':False,'production_migration_executed':False}
    (ROOT/'docs/acceptance/文档校验报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('archive',type=Path)
    validate(parser.parse_args().archive)
