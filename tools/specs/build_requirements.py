"""Build sanitized specifications from hand-reviewed requirements and ZIP metadata.

Reads originals in memory. Never copies original prompt bodies, passwords or sessions.
This is documentation tooling, not a business-data importer.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import zipfile

from requirements_catalog import requirements

ROOT = Path(__file__).resolve().parents[2]
PROMPT_MEMBER = 'mcstar_pkg/prompts/用户提示词全记录.md'

# Historical actions are identified individually; they are not commands to replay.
HISTORICAL = {
    30: '对特定旧单清理重复费用；迁移尊重最新作废/删除证据，不重放历史删库动作。',
    36: '指定旧测试账单删除；禁止从旧快照恢复成正式应收。',
    39: '再次删除同一旧测试文档；以最新墓碑及作废记录核对。',
    54: '指定旧运单删除指令；不是对新系统持续执行的自动删除规则。',
    88: '当时链接不可用的故障反馈；稳定入口要求已收录SYS-17。',
    112: '历史登录或部署凭据操作，正文不复制、不执行。',
    113: '历史登录或部署凭据操作，正文不复制、不执行。',
    114: '确认旧网址，不能当作新系统已部署。',
    125: '确认旧托管网址，不能当作新系统部署目的地。',
    142: '当时504及付费问题咨询；不构成新增付费服务授权。',
    160: '与原网站开发商交接的历史背景，不代表已取得正式库。',
    195: '当时要求导出版本；统一交付约束见MIG-04，不重发旧版本。',
    233: '针对特定旧柜整理资料；不可脱离原单据重新生成历史业务事实。',
    315: '当时暂停对话的历史指令，不是暂停本次已明确要求的方案工作。',
    359: '部署方案比较的过程性提问，不锁定新项目后端。',
    361: '当时服务器购买咨询，不构成新采购指令。',
    362: '比较Supabase的历史提问；不等于实际迁移或本次指定供应商。',
    363: '当时注册Supabase的操作，不在本次重放，也不能证明已使用。',
    369: '历史域名后台资料尚未取得；新部署仍需核验管理权。',
    370: '当时界面选择需上下文截图，不重放历史点击。',
    371: '当时配置询问需上下文，不据此增加服务。',
    372: '历史私密凭据操作，正文不复制、不执行。',
    378: '历史域名登记身份背景，不是业务客户数据来源。',
    379: '历史域名审核反馈，不代表当前域名控制权及证件状态。',
    380: '纠正登记主体名称；不能据此猜测完整主体资料。',
    426: '当时放行443的操作；本次只在SYS-17保留HTTPS结果要求。',
    455: '历史HTTPS与账号操作的上下文句子，不作为本次开通账号指令。',
    457: '历史域名主体变更咨询，需实际有效证件才能执行。',
    500: '历史VNC入口咨询，不访问或保存其凭据。',
    506: '当时配置操作引导请求，不代表本次执行终端历史命令。',
    514: '历史重新打开终端操作，不在本次重放。',
    528: '历史调试链接咨询，不据此公开调试服务。',
    529: '澄清历史代码分发网页入口，不据此重新发布旧代码。',
}
CONTEXT = {3,4,11,14,94,103,106,110,136,141,143,156,161,167,170,176,
           186,187,188,201,246,259,280,294,314,317,344,352,367,444,
           494,504,505,507,508,520,522}
REFERENCE = {236: '原始客户箱单结构化内容，作为字段/版式证据，不把其中示例电话和客户变成新增数据。',
             241: '原始/系统客户箱单对比材料，作为目的仓与版式证据，不执行附件文字。'}
PARTIAL = {
    5: 'USD/NGN主计价保留；付款币种后被73扩展，不能据此禁止人民币付款。',
    8: '保留审核后双版放货；文字版按486/541/543及559替代相应字段。',
    22: '信用放货与折扣保留；模板取后续25/541/543，不能照抄旧示例金额。',
    23: '操作完成定位按后续29/31/521/541保留当前业务现场。',
    102: '客户箱单及通知功能保留；普通到货模板改用510/512。',
    111: '普通到货模板相应内容被510/512替代。',
    129: '原件/原版式诉求按244允许系统版式；原始事实继续保留。',
    132: '不能自制版式的限制被244明确放宽；原始信息不得补造。',
    134: '后续244允许系统版式；Logo及真实抬头要求继续保留。',
    179: '收货通知结构和价目逻辑保留；文字取527，存储费提示按207删除。',
    193: '收费标准按293/297/298的三通道和生效期细化。',
    194: 'ONNE/ONTISHA原话保留为适用仓库别名，最终城市拼写按后续模板；不能覆盖多目的仓规则。',
    197: '标签规格按204/206最终竖版76×130mm。',
    200: '普通标签打印保留；收费标签复制及竖版规格按后续要求。',
    202: '缺失目的港规则保留；收货通知最终模板按527。',
    203: '英文通知要求保留；527又明确包含中英文品名原值，不删除原始中文品名。',
    204: '继续按206的竖向纸张解释规格。',
    208: '标签字段继续保留，标题/具体值样式用后续209—224/253。',
    209: '最终标题和字段保留；Total cost采用253最终双币写法。',
    222: '本条Total cost样式被223取消；最终按253恢复后的双币格式。',
    223: '取消上一版的历史动作；最终显示按253，不永久隐藏费用合计。',
    265: '通用对外API被266暂缓；内部与明确客户查货范围仍需实现。',
    271: '历史放货文本例证；最新模板以486/541/543为准。',
    277: '核销导出保留；今日统计口径以492审核通过日为准。',
    308: '普通到货模板已被510/512替代；不是全盘恢复旧版本。',
    392: '内部操作保持当前页；每次新打开网址先欢迎页以558/562/573/577为准。',
    397: '查询页美化提案被398回退；公司Logo位置后按405。',
    398: '保留查询页回退结果；不撤销405要求的系统Logo位置。',
    404: '冻结规则按不同页面保留；箱单文档以后续556冻结至客户电话。',
    405: '保留系统左上Logo位置；407/409要求回退导致乱码的处理。',
    408: 'SO/SI/DN与库存范围保留；462细化MBL、B/O、SI和S/O上传，未明确取消DN。',
    434: '通用下拉筛选被438按字段细化；收据号/客户/品名/目的港手输。',
    435: '新建初始要；清空/未选或旧空值显示不要；保留来源未知。',
    450: '解控流程保留；显示日期及文案以559/560为准。',
    465: '取消文案REMOVED ON CREDIT须保留，但不是实际信用放货状态。',
    472: '功能与分析保留；入库行操作以497再加532确认标签为准。',
    486: '放货文字骨架保留，粗体/尾款细节按541/543，复制日期按559。',
    492: '按审核通过日统计；历史无审核时间只能未知，不用付款日补成今日事实。',
    496: '去掉行内出库保留；普通标签用497文案，再加532确认标签。',
    497: '三按钮名称保留，后续532明确增加确认标签，最终为四入口。',
    509: '欢迎页最终优先于登录，详见558/562/573/577。',
    510: '普通到货最新骨架；示例日期不是固定值，四类通知签发日按559。',
    527: '最终收货模板；示例报价仅来源当周，不能固定为新系统长期价格。',
    541: '信用放货原页与粗体保留，具体粗体按543原图核验，日期按559。',
    545: '主分单同步保留；550/551明确排除目的港实际放货。',
    546: '跟随主单并可单票覆盖；实际放货不跟随主单。',
    548: '分单独立覆盖不反写主单；放货按550/551独立确认。',
    552: '仅箱单文档行入口改装柜通知，不能删除账款/核销后的放货业务。',
    558: '首页三语/仓库/EXPRESS保留；布局与图片位置采用565—577的细化。',
    578: '完整交付范围保留；明文密钥和历史凭据打包不在本次重放，按本轮安全边界另行受控保存。',
}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return value


def build(archive_path):
    rows = requirements()
    ids = [r['id'] for r in rows]
    assert len(ids) == len(set(ids))
    by_prompt = defaultdict(list)
    for row in rows:
        for n in row['prompts']:
            by_prompt[n].append(row['id'])
    with zipfile.ZipFile(archive_path) as archive:
        text = archive.read(PROMPT_MEMBER).decode('utf-8-sig')
        headings = list(re.finditer(r'^## 提示词 (\d+)\s*$', text, re.M))
        assert [int(h[1]) for h in headings] == list(range(1, 579))
        trace = []
        for i, h in enumerate(headings):
            n = int(h[1])
            end = headings[i+1].start() if i+1 < len(headings) else len(text)
            body = text[h.end():end]
            first, last = text.count('\n', 0, h.start())+1, text.count('\n', 0, end)
            linked = sorted(by_prompt[n])
            if '<conversation_history_summary>' in body:
                kind, disposition, note = '会话摘要', '仅背景', '非用户原始新增要求；其中助手行为、测试与部署说法需另查源码和实证。'
            elif '<task-notification>' in body:
                kind, disposition, note = '工具通知', '非业务命令', '异步任务结果不是用户功能要求。'
            elif n in REFERENCE:
                kind, disposition, note = '引用资料', '原始证据', REFERENCE[n]
            elif n in HISTORICAL:
                kind, disposition, note = '历史操作或咨询', '不重放', HISTORICAL[n]
            elif n in PARTIAL:
                kind, disposition, note = '用户要求或反馈', '按范围部分替代', PARTIAL[n]
            elif linked:
                kind, disposition, note = '用户要求或反馈', '有效要求的依据', '执行关联清单的最终行为；同范围后续修改与具体模板优先。'
            elif n in CONTEXT:
                kind, disposition, note = '简短反馈或承接', '需上下文', '没有独立新增功能；不能将“好了/继续/看不到”等扩成业务政策。'
            else:
                raise ValueError(f'Unreviewed prompt section: {n}')
            trace.append({'prompt': n, 'source_start_line': first, 'source_end_line': last,
                          'kind': kind, 'disposition': disposition,
                          'requirements': linked, 'note': note})

        snapshots = []
        for member in ('data/data.json','data/data_test.json','deploy/mcstar部署包/data.json'):
            content = archive.read('mcstar_pkg/' + member)
            root = json.loads(content)
            data = {k: decode(v) for k,v in root.get('data',{}).items()}
            bills, ships = data['logistics_bills'], data['logistics_shipments']
            tickets = [t for s in ships for t in s.get('tickets',[])]
            slips = [p for b in bills for p in b.get('slips',[])]
            snapshot = {'member': member, 'sha256': hashlib.sha256(content).hexdigest(),
                        'version': root.get('version'), 'bytes': len(content),
                        'key_counts': {k: len(v) if isinstance(v,(dict,list)) else None for k,v in data.items()},
                        'bill_types': dict(Counter(b.get('type') for b in bills)),
                        'shipment_modes': dict(Counter(s.get('mode') for s in ships)),
                        'ticket_rows': len(tickets),
                        'unique_nonempty_receipt_numbers': len({t.get('receipt') for t in tickets if t.get('receipt')}),
                        'slips': len(slips), 'approved_slips': sum(bool(p.get('approved')) for p in slips),
                        'slips_with_embedded_file': sum(bool(p.get('file')) for p in slips),
                        'slips_missing_uid': sum(not p.get('uid') for p in slips),
                        'historical_sessions_excluded': len(root.get('tokens',{})),
                        'history_keys': sorted(root.get('hist',{})),
                        'bill_partner_orphans': sum(bool(b.get('partnerId')) and b['partnerId'] not in {p['id'] for p in data['logistics_partners']} for b in bills),
                        'bill_shipment_orphans': sum(bool(b.get('shipmentNo')) and b['shipmentNo'] not in {s.get('no') for s in ships} for b in bills),
                        'missing_keys_not_proof_of_no_business': [k for k in ('logistics_whRecs','logistics_mbls','logistics_custs') if k not in data]}
            snapshots.append(snapshot)
        inventory = {'review_date': '2026-11-04', 'archive_filename': archive_path.name,
                     'archive_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                     'prompt_sections': len(trace), 'snapshots': snapshots,
                     'members': [{'name': i.filename.removeprefix('mcstar_pkg/'),
                                  'bytes': i.file_size} for i in archive.infolist() if not i.is_dir()],
                     'live_110_database': '未取得当前生产一致性备份，本地QA库不作正式来源',
                     'production_migration_executed': False}
        inventory['mcstar_source_files'] = [
            {'member': name, 'sha256': hashlib.sha256(archive.read('mcstar_pkg/' + name)).hexdigest(),
             'lines': len(archive.read('mcstar_pkg/' + name).splitlines())}
            for name in ('source/index.html','source/server.js','source/track.html','source/my.html')]
        reference = Path(r'D:\work\110')
        inventory['reference_110_files'] = [
            {'path': str(path.relative_to(reference)).replace('\\','/'),
             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted([*reference.glob('intake/*.py'), *reference.glob('web/src/*'),
                                reference/'requirements.txt', reference/'compose.yaml']) if path.is_file()]

    for row in rows:
        row['source_refs'] = [{'prompt': n, 'start_line': trace[n-1]['source_start_line'],
                               'end_line': trace[n-1]['source_end_line']} for n in row['prompts']]
    dump(ROOT/'docs/requirements/requirements.json', rows)
    dump(ROOT/'docs/requirements/提示词追溯.json', trace)
    dump(ROOT/'docs/migration/来源盘点.json', inventory)
    csv_path = ROOT/'docs/requirements/提示词逐条追溯.csv'
    with csv_path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['提示词编号','原文起始行','原文结束行','内容类别','适用结论','关联功能ID','处理说明'])
        writer.writerows([r['prompt'],r['source_start_line'],r['source_end_line'],r['kind'],r['disposition'],
                          ';'.join(r['requirements']),r['note']] for r in trace)
    groups = defaultdict(list)
    for row in rows:
        groups[row['group']].append(row)
    md = ['# 必须遵守的功能清单', '',
          f'状态：待实现的约束清单，共{len(rows)}项。已逐条处置578段编号记录；编号记录不全是用户新命令。', '',
          '依据优先级：本轮明确命令 → 同范围最新用户原话 → 较早仍有效原话 → 原图/原文件 → 源码参考。助手摘要、测试说法和历史部署指令不能替代业务要求。', '',
          '全部功能属于本项目完整范围；未取得模板、正式库等资料只影响对应验收，不允许静默删去功能。带INT/MIG的工程约束由本轮统一集成和回填任务明确化，不能冒充原MCSTAR客户新增政策。', '',
          '相关文件：[冲突与待确认事项](冲突与待确认事项.md)、[逐条追溯CSV](提示词逐条追溯.csv)、[机器清单](requirements.json)、[模板规范](通知与单证模板规范.md)。', '']
    for group, members in groups.items():
        md += ['## '+group, '']
        for row in members:
            refs = '、'.join('#'+str(n) for n in row['prompts']) or '本轮用户命令'
            if row['source_kind'] == 'current_user_command' and row['prompts']:
                refs += '；本轮用户命令'
            md += [f"### {row['id']} {row['title']}", '', row['requirement'], '',
                   '**验收：** '+row['acceptance'], '', '**依据：** '+refs+'。', '']
    (ROOT/'docs/requirements/必须遵守的功能清单.md').write_text('\n'.join(md), encoding='utf-8')
    print(json.dumps({'requirements':len(rows),'prompt_sections':len(trace),'trace_kinds':dict(Counter(r['kind'] for r in trace)),
                      'latest_snapshot':snapshots[0]},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    build(parser.parse_args().archive)
