"""Concrete requirement-to-implementation inventory; never infers test passes."""
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DETAILS='''SYS-01|欢迎、工作台、正式单证均使用MC STAR LOGISTICS及原Logo。
SYS-02|内部语言/固定分类双语显示，保留原存储与业务文本；查询同页中英切换保留输入/结果；完整全部字段人工语言验收未声称。
SYS-03|按能力显示导航；API逐请求actor/范围/版本/幂等；授权先读取现集合+版本，增删预览/撤权勾选/受控PUT；切换低权限及撤权清草稿。
SYS-04|每账号独立HttpOnly会话；刷新恢复CSRF，不覆盖其他会话。
SYS-05|共享PostgreSQL事实与原件存储；多个授权HTTP会话读写同一记录，非浏览器整库同步。
SYS-06|挂载式页面保留筛选、焦点、详情与脏值；退出/撤权清空旧actor状态；409及离线保留输入。
SYS-07|每项服务端命令同事务审计；管理页查询审计与责任人。
SYS-08|全局授权模糊查找票件/客户/收据/出运/账款；精确链接打开详情。
SYS-09|独立PG17；授权选择器50项稳定游标/业务引用搜索并保留选中项；25k/125k旧全负载与修复后受影响list/search按各自版本分列，PG18生产环境未验收。
SYS-10|共享DataTable横滚与同步浮动滚动条；390px保留字段和可达性。
SYS-11|区分未触碰/创建可选空值/显式清空；空串与null保留接口意图，数量不变0；嵌套只提交可编辑字段，预览/保存同变更集；控货历史保留。
SYS-12|受权资料原件/模板、版本及团队归档适配器；实际团队空间未配置。
SYS-13|管理页备份任务、全模块format1流式备份/校验恢复、空库与只读恢复栅栏。
SYS-14|独立账号/语言/公告按钮；公告版本化新增修改撤回，手机导航折叠不遮账号。
SYS-15|操作部、单证部、财务、110、资料、管理按最终归属导航。
SYS-16|原Logo副本和蓝品牌；入库/PL字段顺序固定，模板按已取得依据生成。
SYS-17|本机127.0.0.1入口已运行；正式域名、TLS与HTTPS部署尚未取得环境验收。
SHIP-01|出运单表单SEA/AIR/EXPRESS及独立状态命令，保留库存/放货分离。
SHIP-02|出运单清关日期、成本账单方与应付联动，不猜成本方。
SHIP-03|单据关联已确认客户及仓库成员资格；收据到票件原生链接。
SHIP-04|票件报价与应收生成分离；SEA详情独立港/计费Arrival数量/来源/version表单；AIR/EXPRESS分批到货；普通港节点不生成AR/目的仓事实。
SHIP-05|作废命令显示关系及版本，保留原文与既有财务证据，禁止级联抹除。
SHIP-06|主箱单/文档与多个票件关系；票件编辑和整组历史变更预览确认。
SHIP-07|AIR/EXPRESS专用分批到货表单，件/KG/CBM独立累计。
SHIP-08|AIR到货行操作与标记；SEA不显示空运分批动作。
PL-01|每份上传文件独立附件和PackingDocument，保留文件哈希及原行位置。
PL-02|箱单/F6导入页上传、选sheet、列映射、预览及确认，英文原值不改写。
PL-03|导入原行、原列及明细位置保留；不按同号直接合并。
PL-04|USD/NGN分项和单位严格Decimal解析；歧义必须人工核对。
PL-05|contact_raw与确认号码分离，ON HOLD原文不被电话标准化覆盖。
PL-06|票件目的港/目的仓来自该票显式字段，不能以主单覆盖。
PL-07|票件专用编辑表单、报价影响预览、版本确认。
PL-08|改港使用当前费率/人工计价权限，财务预览后确认，旧指令失效。
PL-09|文件替换映射/完整变更预览；已收款、历史核销和原附件保留。
PL-10|价卡有效日期/始发地/运输及原行顺序；保存报价版本快照。
PL-11|绑定原件和实际MIME原图/PDF查看及授权下载，不以重绘件冒充原件。
RATE-01|价卡SEA/AIR/EXPRESS、目的港/计价单位/日期表单。
RATE-02|币种、明确结算方向及汇率；金额后端Decimal，浏览器仅显示字符串。
RATE-03|AIR全包价保留来源和计价单位，禁止默认补造。
RATE-04|PCS/KG/CBM与NGN/USD真实组件保留，按单位计算而非统一乘KG。
RATE-05|价卡通知只用相应报价快照，变价使旧图片签名过期。
DOC-01|正式客户箱单共用projection及PDF/XLSX/PNG任务。
DOC-02|正式生成件使用原Logo及确认公司抬头。
DOC-03|海运/空运标题和字段按运输类型投影；原模板缺图项单列。
DOC-04|海运清关单价及真实费用单位显示，不伪造按CBM费率。
DOC-05|PNG预生成绑定当前任务/来源/actor；新任务清旧图并丢弃迟到结果；点击ClipboardItem手势中重新取当前服务器授权PNG；失败明确反馈。
DOC-06|导出选中票/当前筛选/全量各有明确入口与后台授权。
DOC-07|客户XLSX保留正式模板、字段、金额类型；精确截图543/24仍缺。
DOC-08|PL保留原字段、费用、联系、到货和复核列，宽表不压缩丢列。
DOC-09|PL冻结到电话、拖动列宽；390选择列和电话不遮挡。
DOC-10|正式多票客户箱单按选择顺序合并展示，不复制组账金额。
DOC-11|MBL/BO/SI/SO/DN专用模板与原件绑定；缺真实模板明确拒绝伪造。
AR-01|原生票/历史完整组对应一金融身份，USD与NGN按明确汇率合并。
AR-02|服务端授权范围总计覆盖全部页；客户账款及名下运单联动。
AR-03|不展示费用项目列；无登记付款红，部分付款且已有放货/信用黄。
AR-04|账单/客户/运单/收据关系点击打开精确记录，列表上下文保留。
AR-05|未付款/部分/已结清筛选使用服务端余额，不按当前页推算。
AR-06|实际上传/到期字段，历史缺失来源日须显式确认，不能用今天补。
AR-07|单账减免及整组历史修订预览/确认，原因遵守可选规则。
AR-08|仅业务允许范围小额尾款规则；不扩大到银行核验差额。
AR-09|一款多账/多款一账分配、撤销及跨方影响预览，Decimal守恒。
AR-10|票件改价预览旧新余额和冲回分配，实收不丢，旧指令不可复用。
AP-01|应付原账单/核销/出运引用与手工付款分开；无货AP不要求虚构货物。
AP-02|账单方/已确认周期查询及付款，来源账单日与AR上传日区分。
AP-03|应付查询/编辑/导出独立权限，PDF/XLSX真实任务。
PARTY-01|客户/账单方分类及多类别成员管理。
PARTY-02|原始电话、标准号码、来源及历史记录保留，修改需依据。
PARTY-03|仓库目录可配置真实仓库；缺失五仓及空运仓资料不造样例。
PARTY-04|有效带显式国际区号且有来源的电话生成wa.me链接；多号码保留原文并明确选择，无自动消息。
PAY-01|文件上传及剪贴板原图入口，原始字节及哈希保存。
PAY-02|多张证据图片逐图来源、人工核对、识别候选、银行确定性匹配和审核。
PAY-03|识别金额/币种/交易日候选需核对；模型未配置时明确暂停，人工字段可用。
PAY-04|案件创建即在水单列表可见，不把待审图片当已收款。
PAY-05|银行占用/复核版本持久化，与付款入账独立；重试幂等。
PAY-06|Payment/Allocation独立身份，不以收据号或日期金额去重。
PAY-07|审核预览确认后保留详情/原件与后续核销动作，失败不假成功。
PAY-08|今日核销以正式approved_at和业务时区查询，旧日期不冒充今天。
PAY-09|银行核验归档/核销XLSX授权导出，原始行和真实金额保留。
REL-01|放货指令文字和PNG单独复制及持久化标记，实际提货另记。
REL-02|有授权的信用放货命令和审核依据，不能伪造已付款。
REL-03|通知标题使用实际目的仓配置，未确认仓不可补造。
REL-04|厂家控货解除按指定票和来源确认，电话与解除事实独立。
REL-05|控货通知要求实际清关及目的仓确认，不以账款到港自动READY。
REL-06|明确解除时间/原文/取消状态，历史证据保留。
REL-07|主管撤销解控需版本与权限，不移除原控货事实。
REL-08|目的仓最小release.read+physical从指令当前scoped members/remaining选票；专用版本化照片上传/原件回读，实际提货独立事实；已提完仍可读授权历史照片。
NOT-01|最新到货通知按确认日期/仓/原始票据与可用数量投影。
NOT-02|收货通知使用当前收据与CBM，签名核对旧版本不可复制标记。
NOT-03|PL行内只装柜通知；中国仓出库走明确批次。
NOT-04|成功复制保存持久copy事件；列表一次批量查询当前标记并黄底黑字；标记不进入通知/导出业务fingerprint。
NOT-05|UI按原copy ID/version/reason撤销，按生成时原成员清标记并保留历史；不依赖票后来所属箱单。
NOT-06|复制日期签名每日刷新，旧图片重核；不以创建时间冒充当前日期。
NOT-07|图片/文字准备供人员明确复制，默认发送与采集关闭。
NOT-08|实际navigator.clipboard.writeText成功后记录事件，失败显示错误并保留内容。
WH-01|统一入库列表、专用创建/编辑/复核/原件/通知/导出。
WH-02|所属仓、客户、运输、货物及电话字段保留；服务端范围校验。
WH-03|库房/库序和目的仓独立，允许规定字段手工输入。
WH-04|多规格长宽高单位/件/KG，后端精确CBM累计，不能前端伪造合计。
WH-05|货堆原图上传/剪贴板保存并绑定对应收据，查看下载权限校验。
WH-06|仓/系列/序号唯一，断号查询；合法同号历史原行不抹除。
WH-07|快递公司及单号关联入库，客户尾号查货逐条授权。
WH-08|正报明确是/否/未知字段，原值保留。
WH-09|受控筛选输入延迟查询、响应序号拒绝旧结果，焦点/脏值保持。
WH-10|前四业务列冻结，拖列宽与横滚，不改变用户字段顺序。
WH-11|可见/全量真实XLSX异步任务；125001行实际文件回读。
WH-12|行内仅收货通知/收费/普通/确认标签，出库不放行内快捷按钮。
WH-13|出运批次选择收据与实际数量；并发不能超出余量，不自动目的仓放货。
WH-14|改包压包保留旧规格修订与库存事实，明确作废不可整表覆盖。
WH-15|主管复核绑定版本，后续变更使旧复核过期；操作员不能越权。
WH-16|入库事实直接进入查货时间线，不能反推不存在的运输节点。
WH-17|翻译任务及候选版本确认，迟到结果不覆盖人工英文；付费模型保持关闭。
F6-01|F6导入sheet/列映射、原行保留和修订，不重复计入真实库存。
F6-02|F6查询、排序、选择行文字复制使用真实浏览器API。
F6-03|指定前一日/目的港来货分析按授权原生入库统计。
F6-04|按业务员分析及下钻，排除MC规则作用于业务员而非收据前缀。
LABEL-01|76×130竖版标签生成，物理打印尺寸仍需真实打印验收。
LABEL-02|普通/收费/确认标签使用实际抬头和字段，原图不足部分不宣称像素一致。
LABEL-03|费用类型、币种和千分位按真实计价组件渲染。
LABEL-04|标签取当前来源版本；改价、任务切换、格式切换和撤权不允许复用旧PNG；真实客户端粘贴未验收。
LABEL-05|按最后有效要求省略多余仓储展示，不删底层库存数据。
MBL-01|MBL字段表单、真实模板/原件上传、出运关联、版本编辑与授权生成。
TRACK-01|逐票入库/装柜/出运/港口/清关/仓确认/实放时间线，事实彼此独立。
TRACK-02|查询同页中文/英文切换保留当前输入和结果；错误不泄露存在性；本人箱单另有号码归属授权。
TRACK-03|收据/快递号及电话后四位逐候选证明，拒绝整柜遍历与其他客户信息。
TRACK-04|原生收据/快递号同号多行逐条核对后聚合。
TRACK-05|主单跟随与每票manual/follow明确覆盖；仓确认覆盖既有到货批次。
TRACK-06|SEA/AIR时间线尊重真实节点，人工时间来源记录。
TRACK-07|未配置开放节点采集保持暂停，不启动旧服务。
PORTAL-01|号码归属验证、会话恢复和本人列表；仅本机传输fake验收，真实短信渠道未配置。
PORTAL-02|本人选择正式箱单XLSX/PDF/PNG任务、下载及权限；另一号码不可读。
HOME-01|每次root新打开先欢迎，已有会话点进入系统可恢复，不自动跳入后台。
HOME-02|欢迎及配置联系人角色三语，业务联系人原文保持。
HOME-03|六服务固定顺序、桌面3×2、EXPRESS首排；仅已确认2–3天实时报价文案。
HOME-04|管理员受控配置仓库/办公资料，公开白名单投影；缺真实内容不显示假数据。
HOME-05|大图中部/小图查询卡后底部配置入口；原图1/2未取得，不以生成图替代。
HOME-06|大号查询卡整卡链接，真实Hotline/Openshipment配置及三语角色。
INT-01|统一项目与全部146范围，遵循原提示词后指令优先和明确撤销。
INT-02|110确定性匹配、原图/原银行行、唯一占用与版本复核，网页和渠道共用动作。
INT-03|Case/BankClaim/Payment/Allocation/ReleaseOrder/PhysicalRelease独立实体及事务。
INT-04|特殊差额只在已批准规则适用范围，不扩大银行通过条件。
INT-05|采集/摘要/模型/发送关闭；连接只读/冷却/不确定投递/新消息防重放。
INT-06|历史人工核销、原审批依据与现银行补证分离，不虚构新现金。
MIG-01|23类来源键和全部原行 staging/typed legacy、显式桥接、完整组修订、来源缺口。
MIG-02|无UID显式映射、合法同额/同号多行保留、幂等与冲突待复核。
MIG-03|实际supplied snapshot与test_ui/test_bench/restore schemas分离；旧QA排除。
MIG-04|锁定依赖、源码/build/start/docs ZIP、独立解压重建运行及恢复证据，实际状态单列。
'''
GROUPS={
'SYS':('web/src/App.tsx;web/src/ui.tsx;backend/app/api/workspace.py','test_foundation.py;test_foundation_review.py;test_workspace.py','工作台/全局查找/账号管理/公告/备份'),
'SHIP':('web/src/catalog.ts;web/src/workflows.tsx;backend/app/packing/service.py','test_packing.py;test_logistics.py','出运管理/PACKING LIST'),
'PL':('web/src/Operations.tsx;web/src/workflows.tsx;backend/app/packing','test_packing.py;test_logistics_review.py;test_logistics_review_2.py','箱单文档/箱单与F6导入/PACKING LIST'),
'RATE':('web/src/catalog.ts;backend/app/pricing','test_finance.py;test_logistics.py','收费标准'),
'DOC':('web/src/workflows.tsx;web/src/ExportJob.tsx;backend/app/documents','test_documents.py;test_documents_review.py','单证资料与模板/导出/通知'),
'AR':('web/src/EntityPage.tsx;web/src/PartyLedger.tsx;backend/app/api/workspace.py;backend/app/finance','test_finance.py;test_finance_review.py;test_workspace.py','应收账款/客户账款'),
'AP':('web/src/catalog.ts;web/src/Reports.tsx;backend/app/finance','test_finance.py;test_permissions.py','按账单方结算/账单方周期结算'),
'PARTY':('web/src/ContactLinks.tsx;web/src/catalog.ts;backend/app/warehouses/masters.py','test_logistics.py;test_permissions.py','客户与账单方/仓库资料'),
'PAY':('web/src/workflows.tsx;web/src/Operations.tsx;backend/app/bank;backend/app/evidence;backend/app/finance','test_evidence.py;test_finance.py;test_finance_review.py','水单审核/收款与核销/今日核销/银行核验归档'),
'REL':('web/src/HoldReview.tsx;web/src/workflows.tsx;backend/app/documents/release.py','test_documents.py;test_tracking.py','厂家控货/目的仓实际提货'),
'NOT':('web/src/workflows.tsx;backend/app/documents/notifications.py','test_documents.py;test_documents_review.py','收货通知/装柜通知/放货通知/控货通知'),
'WH':('web/src/catalog.ts;web/src/workflows.tsx;web/src/Operations.tsx;backend/app/cargo;backend/app/warehouses','test_logistics.py;test_logistics_review.py','入库管理/中国仓批次出库'),
'F6':('web/src/Operations.tsx;web/src/Reports.tsx;backend/app/warehouses','test_logistics.py;test_logistics_review.py','F6仓储数据分析/入库分析与库存导出'),
'LABEL':('web/src/workflows.tsx;backend/app/documents/rendering.py','test_documents.py;test_documents_review.py','入库普通/收费/确认标签'),
'MBL':('web/src/catalog.ts;web/src/workflows.tsx;backend/app/documents','test_documents.py','MBL/BO/SI/SO/DN'),
'TRACK':('web/src/App.tsx;web/src/workflows.tsx;backend/app/tracking','test_tracking.py','客户自助查询/物流节点'),
'PORTAL':('web/src/App.tsx;web/src/ExportJob.tsx;backend/app/tracking/customer.py','test_tracking.py;test_documents.py','验证本人手机号/本人箱单'),
'HOME':('web/src/App.tsx;web/src/style.css;backend/app/api/customer.py','test_workspace.py;test_documents.py','公开欢迎/首页资料配置'),
'INT':('backend/app/bank;backend/app/channels;backend/app/dialogue;web/src/AssistantTools.tsx','test_channels.py;test_connections.py;test_assistant.py;test_evidence.py','110对话助手/账号连接/来源消息/银行流水'),
'MIG':('backend/app/migration;web/src/Operations.tsx;tools/backup.py;tools/package.py;tools/start.ps1','test_migration.py;test_migration_review.py;test_migration110.py;test_legacy_bank_evidence.py;test_restore.py','来源记录/完整组修订/迁移与备份')}
EXTERNAL={
'SYS-12':'团队空间实际归档服务未配置','SYS-17':'正式域名/TLS/HTTPS未部署','SYS-16':'精确模板543/24原图未取得','DOC-01':'精确模板截图缺失','DOC-07':'原截图543/24缺失，不能声明像素同版','DOC-11':'部分原业务模板尚未提供','MBL-01':'缺模板时明确不可生成，需真实模板验收','HOME-04':'未提供的仓库/联系人保持缺失','HOME-05':'宣传原图1/2未提供','PARTY-03':'缺失真实仓库资料未补造','PAY-03':'真实OCR模型与准确率未验收','WH-17':'实际付费翻译模型未开启','PORTAL-01':'真实短信/WhatsApp归属通道未配置，仅窄运输fake本机验证','NOT-07':'真实客户端投递未测试','DOC-05':'真实微信/WhatsApp粘贴未测试','NOT-08':'真实客户端粘贴未测试','LABEL-01':'真实打印机与纸张尺寸未验收','LABEL-02':'部分原标签截图未提供','MIG-01':'未取得当前MCSTAR/110生产一致性备份','MIG-03':'当前生产正式源未取得，包内快照非现网状态','INT-05':'真实账号配对/采集/发送按边界禁用'}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--final-local',action='store_true');parser.add_argument('--review-fix1',action='store_true');parser.add_argument('--final-wave',action='store_true');args=parser.parse_args()
    if args.final_wave:
        import re
        def whole(name):
            text=(ROOT/'private'/name).read_text('utf-8',errors='replace')
            passed=re.search(r'(\d+) passed',text)
            return bool(passed) and not re.search(r'(\d+) failed',text) and not re.search(r'(\d+) error',text)
        assert whole('final-wave-full-backend-r5.log'), 'Final whole backend run must pass'
        assert whole('final-wave-full-browser-r6.log'), 'Final whole browser run must pass'
    elif args.final_local:
        from publish_acceptance_evidence import test_receipt
        assert test_receipt('fix1-backend-full.log' if args.review_fix1 else 'backend-final-isolated.log')['state']=='passed', 'Final whole backend run must pass'
        assert test_receipt('fix1-browser-final.log' if args.review_fix1 else 'browser-final-stable.log')['state']=='passed', 'Final whole browser run must pass'
    original=json.loads((ROOT/'docs/acceptance/实现与证据.json').read_text('utf-8'));prior={r['requirement_id']:r for r in original['requirements']}
    details=dict(line.split('|',1) for line in DETAILS.strip().splitlines());requirements=json.loads((ROOT/'docs/requirements/requirements.json').read_text('utf-8'))
    assert set(details)=={r['id'] for r in requirements} and len(details)==146
    records=[]
    for r in requirements:
        key=r['id'];source,tests,route=GROUPS[key.split('-')[0]]
        records.append({**prior.get(key,{}),'requirement_id':key,'title':r['title'],'source_prompt_numbers':r['prompts'],'implementation':details[key],'implementation_files':source.split(';'),'ui_entry':route,'covering_domain_tests':['backend/tests/'+name for name in tests.split(';')],'verification_boundary':'关联域回归与具体浏览器证据分列；并不声称每项外部/像素/真实投递条件已验收。最终执行结果见验证收据。','external_unaccepted':EXTERNAL.get(key),'whole_requirement_acceptance':'external_input_or_environment_pending' if key in EXTERNAL else 'local_implementation_pending_final_review'})
    output={'task':6,'status':'IMPLEMENTED_LOCAL_CHECKS_COMPLETE_FINAL_WAVE_REVIEW_CLOSED' if args.final_wave else 'IMPLEMENTED_LOCAL_CHECKS_COMPLETE_REVIEW_PENDING' if args.final_local else 'IMPLEMENTING_FINAL_VERIFICATION','schema_head':'h11000800003' if args.final_wave else 'g11000600002','requirements_total':146,'requirements':records,'browser_evidence':{'web/tests/full-chain.spec.ts':'真实UI跨页面主链，3条主资料API fixture，其余主要动作UI操作','web/tests/workspace.spec.ts':'reload CSRF/权限/焦点/409/离线/ClipboardItem/跨账号及撤权','web/tests/customer.spec.ts':'号码归属/本人导出/退出/另一号码；仅outbound phone transport fake','web/tests/english.spec.ts':'英文新仓/入库表单保存','web/tests/review-fixes.spec.ts':'手机冻结电话可见/联系人三语/安全号码链接，不实际跳转或发送'},'runtime':'http://127.0.0.1:8011','runtime_data':'supplied package snapshot, controlled authentication; not asserted current production','prior_task5_evidence':'docs/acceptance/迁移审查记录.md'}
    if args.final_wave:
        output['review_fix_round']=2
        output['verification_receipt']='final-wave-verification.json'
        output['browser_evidence']['web/tests/final-wave.spec.ts']='FR-I08/I09 true browser flow on fresh schema'
        output['browser_evidence']['web/tests/final-wave-ui.spec.ts']='FR-M01/I02/I03/I04/I05 normal UI incl. unknown old cancellation selection'
        closed={'SHIP-05','SHIP-02','AR-01','AR-02','AR-09','AR-10','REL-01','REL-02','REL-04','REL-05','REL-07','REL-08','WH-13','WH-14','PL-07','PL-08','PORTAL-01','PORTAL-02','NOT-04','INT-05'}
        for item in output['requirements']:
            if item['requirement_id'] in closed and item['whole_requirement_acceptance']!='external_input_or_environment_pending':
                item['whole_requirement_acceptance']='local_implementation_final_wave_finding_closed'
        output['final_review']={'reviewed':'FR-I01--I09, FR-M01, FR-M02','result':'CLOSED','new_findings':'NEW-I01 closed; adopted NEW-M01/M03/M04/M05; recorded NEW-M02/M06/M07','verification_gaps':'listed in final-wave-verification.json and final-rereview.md','external_unaccepted':21}
    if args.review_fix1:
        output['review_fix_round']=1
        output['verification_receipt']='task6-fix1-verification.json'
        output['browser_evidence']['web/tests/task6-fixes.spec.ts']='15项真实浏览器回归：SEA/最小目的仓照片及提货/>50付款和claim/显式清空/grants/PNG竞态与撤权/原成员通知撤销/同页语言'
        for item in output['requirements']:
            if item['requirement_id'] in {'SYS-02','SYS-03','SYS-09','SYS-11','SHIP-04','SHIP-08','REL-08','DOC-05','DOC-06','LABEL-04','NOT-04','NOT-05','TRACK-02','PARTY-02','PARTY-03'}:
                item['covering_review_tests']=['backend/tests/test_workspace_fix1.py','backend/tests/test_workspace_performance.py','web/tests/task6-fixes.spec.ts']
                item['review_fix_round']=1
    (ROOT/'docs/acceptance/实现与证据.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print('Mapped 146 requirements; no inferred external acceptance.')
if __name__=='__main__':main()
