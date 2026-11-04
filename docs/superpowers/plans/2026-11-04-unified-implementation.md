# 110-ALL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking. 用户已批准本方案开发，直接连续执行，不再请求方法/继续许可。

**Goal:** 在指定目录交付完整统一应用、可核对的包内试回填、110适配和可复现运行/恢复。

**Architecture:** 模块化FastAPI + SQLAlchemy/Alembic + PostgreSQL；React统一工作台与客户入口。旧源码只读，业务证据和关系进入统一模型，外部模型/WAHA/团队空间通过受控适配器，默认关闭外发。

**Tech Stack:** Python 3.12+、FastAPI、SQLAlchemy2、Alembic、psycopg3、React19、Vite、原110必要纯函数。Windows开发用已安装PG17独立集群；部署镜像PG18。

**Spec:** ../specs/2026-11-04-110-all-design.md 及 ../../requirements/必须遵守的功能清单.md。

## Global Constraints

- 原D:\work\110和MCSTAR ZIP只读，禁止启动旧服务、登录账号、付费模型或发送消息。
- 全146项范围保留，未取得外部资料必须显示明确缺口，不能假装完成。
- 金额Decimal/NUMERIC；银行核验、付款、分配、控货、指令与实际提货独立。
- 变更需服务端权限、数据范围、版本、幂等与同事务审计；禁止整表覆盖。
- 无UID跨快照身份须显式映射；同号多行、合法同额款和原始字段不丢。
- 真实数据、原提示词、密钥、会话和附件只在被忽略的受控私有运行目录，不进Git/日志。
- 实际UI使用已选Impeccable code-first，原模板/字段/文案优先，内部中英、首页中英法。

## Review Focus

1. 同银行行并发领取以及已核销后重识别，不能释放/重领已占用款（Task2真实PG事务测试）。
2. 已付款后改价/重传与同号多行，付款/余额/附件/节点均守恒（Task2/3/5变更反例）。
3. 多币种方向及NGN/PCS改港，不乘错单位/汇率（Task2/3明确手算案例）。
4. 跨快照无UID重排/新增同额合法交易，不能重复或吞款（Task5独立来源观察与付款映射）。
5. 所有匿名旁路、客户尾号/本人导出及重试保存，权限必须在API生效（Task1/4/6请求级反例）。

## Shared interfaces

- 包根`backend/app`；`app.db.Base`, `make_engine(url)`, `session_factory(engine)`；PG环境由`MCSTAR_DATABASE_URL`或受控本地配置提供。
- `app.models`显式导出实体（Task1定义统一模型，可按identity/logistics/finance/evidence/documents分文件）。业务核心不能只用通用JSON实体存储。
- `app.common.Actor(id, capabilities, warehouse_ids)`、`require(actor, capability, warehouse_id=None)`、`DomainError(code, message, status=400, details=None)`。
- 所有命令服务`service(db: Session, actor: Actor, payload: dict) -> dict`，不自行commit；HTTP路由统一使用`app.api.dependencies.Database`，它以function-scope拥有唯一事务并在发送成功响应前提交；禁止嵌套`db.begin()`。非HTTP调用者用`with db.begin()`。版本字段`version`，请求字段`expected_version`。金额响应为字符串。
- `app.common.audit(db, actor, action, entity_type, entity_id, before, after)`与`run_command(db, actor, command, key, payload, handler)`；后者同事务存请求哈希/结果，重复同键同体返回同结果，异体409。
- 路由模块导出`router`，由`app.main.create_app(settings)`装配；依赖`app.api.dependencies.get_db/get_actor/mutate`（mutate校验CSRF）。`settings`含DB、私有根目录、功能开关、Cookie与业务时区。所有内部路由`/api`，公开入口专门投影。
- 文件服务`app.attachments.put_file(db, actor, content, filename, media_type, owner_type, owner_id)->Attachment`及`read_file(settings,attachment)`；目标在私有根，哈希校验，禁止任意路径/URL读取。
- 每项任务添加自己的真实行为测试，测试数据全合成。不得改别的任务未提交文件；实现代理不派生子代理。所有git提交只含明确拥有的文件。

### Task 1: 可运行数据库、权限、附件与统一实体

**Files:** `backend/pyproject.toml`, `backend/app/{settings,db,common,attachments,main,cli}.py`, `backend/app/models/`, `backend/app/api/{dependencies,identity}.py`, `backend/migrations/`, `tools/dev_runtime.py`, `backend/tests/conftest.py`, `backend/tests/test_foundation.py`, `deploy/`基础。

**Interfaces:** 产生上述共享接口；本任务实体为用户/能力/仓库范围/会话/审计/幂等命令/附件/配置/持久任务与outbox。财务、单位、仓库、物流、文档及迁移领域的强类型模型由Tasks2–5随实际服务增加并固定Alembic迁移，避免提前猜字段。JSON只用于来源原文、配置、候选、事件快照等，不能作通用业务行存储。

- [x] RED：API匿名业务拒绝，低权限不能写，陈旧版本409，同幂等键异体409；原始附件只能受权取回。先运行失败证据。
- [x] 建项目隔离venv，锁定依赖；启动独立本机PG17集群（不是已有服务），口令随机保存private，不输出；create_app健康检查、结构迁移、单次受控管理员创建，禁止默认账号/公开bootstrap。
- [x] GREEN：真实PG/HTTP测试通过；CLI可启动、迁移、设置管理员；不启动模型/WAHA。记录精确模型字段供后任务引用。
- [x] 运行当前全套并提交；报告产生接口/依赖/运行方式。19 passed；四项审阅问题修复后Approved。

### Task 2: 精确财务、110证据核对与双入口共享动作

**Files:** `backend/app/finance/`, `backend/app/evidence/`, `backend/app/bank/`, `backend/app/channels/`, `backend/app/api/finance.py`, `backend/tests/test_finance.py`, `backend/tests/test_evidence.py`；按需添加迁移及模型。

**Interfaces:** 消费共享实体/命令事务；产生`finance.service`的create_invoice/create_payment/post_payment/reverse_payment/adjust_invoice/pay_payable及`bank.service`的import_preview/confirm_import/match_case/review_case；`channels.service`接收签名Webhook、授权意图及暂停/发送边界。返回统一序列化dict。

- [x] RED：NGN2000×3PCS=6000，USD与CNY方向、3水单合并/多次分配、53.75未授权待核、同额不同交易、并发唯一claim、证据改动冻结claim、今日按审核日。
- [x] 复用110确定性算法/原文件解析/上下文与局部识别结构；PostgreSQL仓储、跨来源补证、sample隔离、识别队列、原模板归档适配，不执行外部调用。
- [x] 应收/折扣/信用/历史余额，周期应付与可无柜付款，付款/银行/分配状态分开。改价100000→80000返20000余额，→120000留欠20000；不改原款/实放。
- [x] 对话与网页的结构化动作适配走同业务方法、授权operator、nonce确认；发送未启用明确拒绝，uncertain不自动重发。完整自然语言工具循环与连接管理在Task7补齐，不能将此基础接口称完整110交互集成。
- [x] GREEN：真实PG事务/HTTP与纯函数测试全通过，运行全套并提交。

### Task 3: 仓储、F6、出运、箱单及报价完整业务

**Files:** `backend/app/warehouses/`, `backend/app/cargo/`, `backend/app/shipments/`, `backend/app/packing/`, `backend/app/pricing/`, `backend/app/api/logistics.py`, `backend/tests/test_logistics.py`, `backend/tests/test_packing.py`。

**Interfaces:** 消费财务adjust_invoice与共享模型；产生receipts/shipments/tickets/price_cards的CRUD与版本命令、Excel预览提交、split/dispatch/arrive/repack/void/review，按权限查询/筛选/汇总/导出数据。

- [x] RED：20规格体积、收据身份唯一但同号多行保留、分批库存不超余、复核旧版本拒绝；未到港不自动AR，空运到货可AR。
- [x] 实现原文件行提取、多表/合并/TOTAL、F6字段/目的仓/电话来源、原件/重传差异；保持付款/通知/节点映射，歧义待确认。
- [x] 报价含通道/始发地/货类/港/单位/币种/收货日有效期；手改优先，未知不猜价。目的港修改正确重取价并联动已存在AR；清关应付upsert不重复。
- [x] 多票拼柜/空运拼货、同号分批、改压包/作废/恢复与主管复核、断号区间、前日入库/港占比/业务员下钻、模糊查询。
- [x] GREEN：真实PG行为/导入文件测试全通过，运行全套并提交。最终156 passed；两轮修复后SpecCompliance/Quality Approved。

### Task 4: 通知、标签、MBL、跟踪及客户门户

**Files:** `backend/app/documents/`, `backend/app/tracking/`, `backend/app/api/documents.py`, `backend/app/api/customer.py`, `backend/tests/test_documents.py`, `backend/tests/test_tracking.py`。

**Interfaces:** 消费统一实体及财务/物流服务；产生通知render/copy_events/cancel、客户箱单/标签/文档Excel-PDF-PNG导出、hold/credit/release/physical release、主分节点、公开tracking/本人门户；可配置验证provider，不向匿名调用返回验证码。

- [x] RED：510/527/541/552模板、签发日不改付款/装柜日；复制事件成员快照、撤销后改组不误伤；主节点不实放、手改每节点独立。
- [x] 实现原Logo、Ebrima、海空/EXPRESS/PCS字段、76×130标签及显式导出；没有模板/原图时显示真实缺口，不能塞生成图。
- [x] 厂家控货与运费信用分开；主管撤销；目的港实际放货权限/证据与库存检查；改价/改成员旧指令失效。
- [x] 实时查货尾号验证贯穿全部旁路、快递映射、无品名/整柜泄漏；号码归属验证接入接口默认禁用、已验证客户本人导出。
- [x] GREEN：文件实际打开/字节类型/数字金额回读及HTTP反例通过，运行全套并提交。最终197 passed；五项修复后SpecCompliance/Quality Approved；实际外部验收仍待对应证据。

### Task 5: 全域迁移、试回填、恢复与运维

**Files:** `backend/app/migration/`, `tools/migration/`, `tools/backup.py`, `backend/tests/test_migration.py`, `backend/tests/test_restore.py`, `deploy/`, `docs/acceptance/实现与证据.json`。

**Interfaces:** 消费统一实体与所有模块（包括Task7）；产生inventory/stage/reconcile/plan/apply/verify CLI和仅管理员迁移查询；每源表字段/JSON指针对应source_map，未知字段出issue。

- [ ] RED：同批二次0新增、964票行不按917号去重、55无UID观察与付款分离、两来源同款不重记、跨快照重排/改审核/新增同额、墓碑不复活、QA不入live。
- [ ] 实现23键及hist/tokens受控归档/排除，解析嵌套水单/附件/台账/状态；12/42引用异常保留，历史received控制层不伪造现金。
- [ ] 110一致性SQLite备份读取全表/原附件及队列，保留旧claim与不重放，不用本地QA当正式；私密数据和报告只在private/runtime。
- [ ] 对包内最新执行隔离试回填与第二次幂等验证；输出脱敏计数，不输出客户/实际金额/号码。缺生产源模块明确未完成。
- [ ] 空数据库+附件实际恢复并逐域勾稽，完整备份/增量/单写者切换与回退工具；团队空间适配未配置明确未归档。
- [ ] GREEN：全套测试、实际试回填与恢复证据通过，提交源码和脱敏证据。

### Task 6: 全功能React工作台、三语首页、运行交付与验收

**Files:** `web/`、`PRODUCT.md`、`DESIGN.md`、`.impeccable/design.json`及surfaces、`backend/app/main.py`装配/前端静态服务、`tools/start.ps1`、`docs/acceptance/实现与证据.json`、README。

**Interfaces:** 消费其余任务OpenAPI（包括Task7）；所有内页读写真实API，不使用业务演示种子/假按钮，使用统一错误、版本冲突、权限与私有附件。

- [ ] RED：浏览器测试首次欢迎页、入系统登录、权限、编辑不跳页/不丢焦点；端到端合成入库→分批→到货→AR→多图核销→通知→实放链。
- [ ] Impeccable code-first，先读craft-floor；操作部/单证部/财务/110/资料/管理全模块路由，行内限制/冻结/拖列/横滚/筛选/点击跳转/今日核销及文档复制导出。
- [ ] 通知复制预生成PNG，用ClipboardItem/image/png实际字节，失败不标成功、不转打印/下载；记录仅已复制。宽表与大文件性能、异步任务可见。
- [ ] 真实数据只通过私有API显示；三语首页真实资源/联系人受控配置，缺原图不伪造；本人查货/注册/导出按后端验证。
- [ ] npm build/前端行为测试和后台全套；桌面1440与手机390批量检查，修复一批后复看；Impeccable detector一次与独立UI/全分支审阅。
- [ ] 对146项记录已实现/程序验证/外部未验收，不把旧测试或本机模拟当真实WA/生产；完成可复现启动、数据回填、恢复、依赖锁及交付说明。

### Task 7: 110自然语言助手、连接与扫码管理完整接入

**执行位置：** Task4后、Task5前。编号是稳定任务ID，执行顺序为1→2→3→4→7→5→6。

**Files:** `backend/app/dialogue/`, `backend/app/connections/`, `backend/app/api/assistant.py`, `backend/tests/test_assistant.py`, `backend/tests/test_connections.py`，typed模型/固定迁移，`docs/development/assistant-contract.md`。

**Interfaces:** 消费Task2财务/证据/结构化nonce/投递，Task3查询与Task4文档归档；产生网页与授权小号共同使用的turn/tool/query/archive dispatcher、当前来源版本确认和确定性结果回复。接入WAHA session status/QR/pairing诊断/有限恢复/连接告警，所有外部操作受配置与权限控制，默认不调用。

- [x] RED：自然语言提问不执行付款；模型虚报无工具动作不回复已完成；当前版本/权限/来源一致；原图/银行/票件/档案查询与文件回取守范围；明确指令经nonce调用相同真实PG服务。报告区分业务RED、脚手架RED和实现后的旧版对照，不混称事前TDD。
- [x] 复用110 dialogue/entry_services/tools/reply和connections/health/pairing owning逻辑，改为统一仓储；保留自然语言查询银行/凭证/文件、预览银行新表确认导入、复核/暂缓/修正/撤回、完整待核清单、原模板归档回取。
- [x] 保留110原本“本人手机发群消息仍处理”的行为；fromMe不等于机器人。用可信session owner/participant与确切发送回执区分人工自发和后台回复，保留正确群归属；未知来源暂缓可核对，不能一律丢掉或自动循环。以原v0.8.1修复及原群回显/entry source为准。
- [x] 会话/群/模型/operator配置不含明文秘密；Model工具仅白名单动作，不接任意SQL/路径/代码。网页和小号对相同case版本操作得到同一实际结果，发出不确定绝不盲重发。
- [x] 连接当前状态、二维码失效/在途请求版本保护、手动刷新条件、手机验证状态与线路诊断；自动恢复只对之前已成功关联且适用故障，冷却10分钟、每小时最多2次，不自动重启未关联/验证中账号，不升级旧引擎或替换网络节点。
- [x] 连接事件与授权对象通知通过既有受控outbox，暂停/主号只读/发送白名单继续强制。外部不可用时状态可回读，不把服务health当WhatsApp在线。
- [x] 消息状态区分提供方受理/实际投递回执/已读；只拿到API message ID不得称客户已收到。保留已知发送ID用于精确bot去重和延迟回执关联，未知投递仍不盲重发。
- [x] GREEN：真实PG工具效果+外部HTTP窄fake验证完整dispatcher与会话契约，默认flag实际called=False；不真实扫码/采集/发送/付费调用。修复后最终全套241项通过，独立复审Approved。实际生产配对与消息回执仍未验收。

## Execution decisions

- 用户已经明确开发授权，writing-plans的再次选择执行方法/确认步骤由本轮指令覆盖；连续按任务执行。
- 新目录尚无应用/Git；直接初始化独立`codex/unified-implementation`分支，不另建用户未要求的worktree。
- 每次只有一个实现代理，控制器负责审阅/接口/集成记录；模型按任务复杂度显式选择，代理不再派生代理。
- 外部资料与真实账号验证缺口保留，不阻止可独立开发和本机验证的全部代码。
- Task2实现复核发现自然语言编排和连接管理尚未具体落入服务任务，补Task7保持完整110范围；这是工程拆分修正，不减少功能或另行启动用户未要求的项目。
