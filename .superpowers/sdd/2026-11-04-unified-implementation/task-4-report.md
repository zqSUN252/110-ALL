# Task 4 implementation report

Date: 2026-11-04. Status: DONE_WITH_CONCERNS.

Implementation commit: `8e70d73b97ed539b9d669fca6ace6365b01e7363`. Parent/base: `2d296ee63a63f6185767371994c383d54ac9a6b7`, branch `codex/unified-implementation`. This report covers Task 4 backend only. It does not declare the 146-item unified project accepted, deployed, or visually matched to missing original screenshots.

## Delivered behavior

- Typed PostgreSQL records for manufacturer holds and phone history, release instructions/members, independent physical pickups/photos, payment registration, notifications/copy events, tracking nodes, persistent source-bound document exports, MBL/template attachments, verified customer identity/session/challenges and anonymous-attempt counters. Existing cargo, finance, allocation, invoice, pricing authority and Arrival services remain authoritative; no second ledger or generic JSON business table was introduced.
- Versioned text/image notification families for arrival 510/512, receipt 527, release 486/541/543, manufacturer hold 345, removal 464/465/560, loading 552 and current scoped rates 185/207. Actual unit/currency components and invoice rounding differences are explicit. NGN/PCS is neither relabelled KG nor multiplied by a USD rate. Source dates remain separate from business-local generation/signature dates.
- Successful clipboard acknowledgements persist original source membership, actor/time and cancel history. Split/regrouping does not redirect cancellation to a newly computed group. An old generation/date/source is rejected on acknowledgement. Copy status remains distinct from actual delivery and pickup; no automatic send was enabled.
- Independent ON HOLD facts survive ordinary owner-phone edits/clearing, payment and credit. Only the actual owner-phone marker creates a hold. Authorized removal retains reason/date/new phone/rate and invokes the shared source update; supervisor cancellation restores control without granting credit. Native pre-bill removal stores the declared rate as provenance and explicitly returns `rate_applied_to_invoice:false`; it neither creates AR nor silently reprices an independent quote.
- Release instructions validate the entire current member set and full source/finance/control fingerprint. Credit retains debt and records guarantor/promise day. Explicit positive tail waiver up to NGN5000 uses the existing discount service. Payment registration is source-linked and does not post cash. AR color follows registration/valid release/credit facts, not simply any positive debt.
- Ordinary actual-destination warehouse operators can inspect minimal eligibility and record PCS/KG/CBM/time/photos using `release.read`/`release.physical`, without bank.read or administrator authority. Private server validation still checks invoice/holds/order/source. Photos remain separately authorized original attachments; pickup never arises from a main node, China dispatch, payment, instruction or hold removal.
- Distinct warehouse confirmations use TrackingNode plus explicit destination and immutable existing Ticket/Arrival ID/version coverage. Billing Arrival or arrived_port alone cannot imply READY. HOLD345 additionally requires effective customs clearance. Per-node manual clear/follow works. New Arrival batches do not inherit an older confirmation. Price/contact/same-shipment document changes invalidate old instructions but preserve real covered batches. Main warehouse A and B confirmations coexist without losing A or applying A to B.
- Actual XLSX/PDF/PNG files, original logo bytes, available Ebrima/Chinese/emoji resources, 76×130mm label geometry, separate plain/fee/confirmation labels, numeric amounts, unknown-subtotal metadata, Master/port/customer/Totals sheets and E2 freeze. Customer business strings are literal spreadsheet text, including formula prefixes; controlled template formulas are preserved. Rendering treats markup-looking strings as text. No Windows font files or substitute hero images were copied into source.
- Persistent export jobs reauthorize current actor/customer and sources at execution, publish and byte download. Expensive rendering occurs outside the shared command lock. Explicit file download and PNG clipboard endpoints are separate. MBL is typed, shipment-linked and versioned; actual configured XLSX templates drive BO/SI/SO/DN generation, while original S/O uploads remain preserved and readable. Missing originals are explicit inputs, not invented accepted templates.
- Anonymous tracking checks each candidate's own confirmed recipient suffix before aggregation, including duplicate receipt numbers and one courier mapping to multiple owners. It omits goods, amounts, full phone, internal notes, container membership and photo identifiers. Committed selected F6 observations are qualified historical observations, never synthesized native stock.
- Phone ownership registration uses a real configurable HTTPS transport with hashed/expiring/single-use codes and persistent attempt counts. Missing/disabled provider returns 503, never an OTP/debug bypass. Tests fake only provider HTTP transport. Customer sessions/cookies/CSRF, logout revocation, total-budget pagination, current-owner documents/exports/photos are separate from internal employee permissions. Mixed original workbooks are not exposed through one owned row.

The precise payloads, capability/scope rules, defaults, workers, source statuses and artifact endpoints are in `docs/development/documents-contract.md` for Tasks 7/5/6.

## TDD and executed verification

Commands used the project `.venv/Scripts/python.exe`, `PYTHONPATH=backend`, and `MCSTAR_CONFIG=D:\work\110-ALL\private\runtime.json`. All datasets, phones, images and templates used in tests are synthetic. PostgreSQL tests create isolated schemas on the existing independent PG17 instance; no original Windows service or old project was changed. Credentials/DSN are not reproduced here.

Observed behavioral RED evidence:

1. `pytest backend/tests/test_documents.py::test_phone_provider_is_disabled_without_configuration -q --tb=short`: the missing endpoint returned 404 rather than the required explicit 503 disabled-provider response. Subsequent service-import errors during construction were scaffold failures, not claimed behavioral REDs.
2. `pytest backend/tests/test_documents.py::test_billing_arrival_alone_cannot_claim_warehouse_ready -q --tb=short`: `DID NOT RAISE` before the explicit warehouse confirmation guard. Billing-only arrival had incorrectly admitted a READY notice; the node/coverage implementation fixed this.
3. `pytest backend/tests/test_documents.py::test_photo_bytes_require_current_owner_and_hold_reads_are_scoped -q --tb=short`: the real customer HTTP response to `limit=1` contained two rows when both a ticket and receipt existed. The fixed total page budget returns one across both source kinds and the next offset returns the second.

Focused GREEN iterations included 9, 15, 18, 19, 23, 29 and 26 passing checks as the task and late warehouse ruling were implemented. These intermediate counts are not added together or represented as distinct coverage. Specific final focused commands included:

```text
pytest backend/tests/test_documents.py backend/tests/test_tracking.py backend/tests/test_cli_migrations.py -q --tb=short
26 passed, 1 warning

pytest backend/tests/test_documents.py::test_credit_physical_release_and_metadata_invalidation backend/tests/test_tracking.py::test_actual_provider_transport_verification_never_returns_code -q --tb=short
2 passed, 1 warning in 5.57s
```

The first complete intermediate full suite was **172 passed, 3 failed, 1 warning in 488.68s**. Its failures were:

- `test_nonempty_logistics_downgrade_refuses_to_erase_source_facts`;
- `test_review_migration_preserves_component_and_exclusion_evidence[component]`;
- `test_n1_migration_review_preserves_ar_and_allows_metadata_then_explicit_resolution`.

Each expected a particular older migration guard, but the newly added phone-history guard fired first. The first two were resolved by retaining wholly unknown initial phones as the original NULL instead of manufacturing a history observation. A real subsequent contact change still creates history. The third assertion was precisely adapted, with parent authorization, to the actual `cargo_phone_history` refusal; the AR/cash/source-preservation business assertions were retained. `test_c6_still_independently_protects_ticket_without_phone_history` separately creates a real NULL-phone Ticket and proves c6 still rejects discarding its pricing authority. No historical rows are deleted to get past a guard, and no assertion was loosened to any exception.

Two subsequent full-suite attempts were intentionally interrupted before completion to incorporate discovered pagination and final photo-reference/logout corrections. They are not final pass evidence. The final complete run below was started only after source/test freeze:

```text
pytest backend/tests -q --tb=short
181 passed, 1 warning in 484.28s (0:08:04)
Process exit code: 0
```

The known warning is Starlette's dependency reference to deprecated `anyio.abc.BlockingPortal`; it is not a skipped test or a claimed new feature success.

## Actual schema and preservation

Fixed revisions are d11000400001 (typed records/backfill), d11000400002 (explicit warehouse coverage/destination indexes), and d11000400003 (hold-removal declared rate). Runtime had already applied d1 before the late warehouse clarification, so d2/d3 are real subsequent upgrades rather than edits pretending to upgrade applied schema.

Final runtime verification:

```text
command.current: d11000400003 (head)
command.check: No new upgrade operations detected.
git diff --cached --check: exit 0, no whitespace errors
```

`test_frozen_migration_round_trip` actually downgrades an empty isolated schema to base, checks only alembic_version remains, upgrades head and runs `command.check`. `test_cli_initial_admin_is_one_time` exercises real CLI init-db on another fresh schema. No development/runtime accounts are overwritten.

Nonempty protection layers remain distinct:

| Guard | Actual evidence |
| --- | --- |
| d3 hold-removal rate | An additional isolated real-PG migration probe populated synthetic HoldFact rate, attempted downgrade to d2, received exactly `Cannot discard nonempty hold-removal rate provenance`, then `No new upgrade operations detected.` and preserved head. |
| d2 warehouse coverage | `test_nonempty_document_migration_refuses_downgrade` uses a real warehouse-confirmed prepared source, rejects downgrade and checks schema. |
| d1 phone history | Revised existing n1 migration test reaches `Cannot discard nonempty document/customer/hold facts: cargo_phone_history` after a real contact mutation; associated AR/cash/source assertions remain. |
| c6 pricing authority | New minimal no-phone-history ticket test directly reaches `Cannot discard pricing authority` and verifies retained totals authority. |
| Earlier logistics/component evidence | Existing nonempty logistics downgrade and parametrized component/exclusion migration tests still execute their original business checks; no protection was removed. |

## File and privacy evidence

`test_exports_are_real_files_unknown_totals_and_label_size` opens generated bytes with openpyxl, pypdf and Pillow; checks numeric NGN1500, PDF extracted amount, genuine PNG dimensions, PDF MediaBox 76×130mm within 0.01mm, and unknown weight `value:null,pending_count:1`. `test_actual_ngn_pcs_template_and_literal_xlsx` checks the typed NGN/PCS exception, original formula-looking strings and exported cell types. `test_mbl_requires_real_template_and_preserves_literal_fields` reads actual workbook output: `=1+1` remains text, `A & B <cargo>` round-trips unchanged, while original controlled `=SUM(1,2)` remains a formula.

Synthetic packing/label PNG/PDF/XLSX inspection files are retained only under ignored `private/task4-verification/`. Fee-label and header previews were visually inspected; punctuation baseline and bold fee line wrapping were corrected. Original logo SHA256 was compared with the provided reference (matching `0D5C52A7B233D7776BD7D66C66158287A1506CF5754CE8589CEBA2EFFA11F7F0`). These checks demonstrate actual local artifacts, not missing screenshot fidelity or physical printing.

Real HTTP tests fetch export and photo bytes for the owner, reject anonymous/another customer, then change the actual source phone and reject the formerly authorized download on the next request. The anonymous duplicate-receipt/courier test independently filters different recipients. Failed suffix attempts persist until the rate limit returns 429. Provider tests execute actual domain/PG/customer HTTP paths with only the outbound HTTPS transport fake; anonymous responses never contain the sent code. Logout immediately makes the previous customer session return 401. Public company profile explicitly omits bank/internal contact settings.

## Files and self-review

- New focused packages: `backend/app/documents/` (access, configuration, projections, notifications, release, exports, rendering, MBL, phone hooks, original assets), `backend/app/tracking/` (nodes/customer identity).
- Typed entities: `backend/app/models/documents.py`; fixed d1/d2/d3 migrations.
- Routes/payloads: `backend/app/api/documents.py`, `documents_inputs.py`, `customer.py`; narrow main/model/settings/worker hooks.
- Existing packing projection only changes actual_release_at to recorded independent PhysicalRelease evidence.
- Exact dependency additions: ReportLab 4.4.5, pypdf 6.1.3, charset-normalizer 3.5.2. Official reference links/license distinctions are already recorded in `docs/references/成熟实现与参考依据.md`; actual adopted library behavior is now checked in this task, without claiming external template acceptance.
- Tests: new `test_documents.py`, `test_tracking.py`; one parent-authorized exact migration-guard assertion in `test_logistics_review_2.py`.
- Handoff contract: `docs/development/documents-contract.md`.

Self-review checked source authorization at mutation/replay/file paths; immutable copy membership; fee currency/unit/rounding authority; ordinary destination positive case; independent pickup; known/unknown totals; source literal rendering; current-owner privacy; provider-disabled behavior; warehouse coverage/follow/clear/multi-destination semantics; and fixed migration progression. No independent reviewer/subagent was spawned per assignment. Existing `docs/requirements/冲突与待确认事项.md` edits are parent-owned and intentionally excluded from implementation staging.

## Real external acceptance boundary

Missing original BO/SI/SO/DN workbook samples, exact “24号”/layout screenshots, hero1/2 images, deployed font configuration outside this Windows host, real verified-phone delivery, real 微信/WhatsApp clipboard paste, physical 76×130mm printing and full React forms remain unverified. Template configuration/resource status/provider-disabled responses expose these limits directly. No real messages, accounts, paid models, existing WAHA connections, collection, historical-message replay, network configuration changes or old-service control were performed. Backend APIs are implemented and locally exercised; these external workflow/input checks are not claimed complete.

## Exact requirement and original-prompt mapping

The following source IDs/numbers are taken from the binding requirement catalog. They identify implemented backend responsibility and the tests/contract; UI-only details and external artifacts retain the above boundary.

| Requirement | Original prompt numbers | Backend / evidence |
| --- | --- | --- |
| RATE-05 收费标准通知范围 | #185、#207 | Current actual scoped cards; rates template and typed projection checks |
| DOC-01 统一客户箱单版本 | #80、#102、#104、#157、#244、#249、#303 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-02 公司Logo及抬头 | #107、#129、#131、#134、#249、#250 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-03 海空运标题和字段 | #306、#307、#310、#318、#320、#321、#326 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-04 海运清关单价 | #535 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-05 图片直接复制 | #198、#249、#252、#381、#385、#386、#387、#388、#389、#391、#499、#501、#502、#515 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-06 单页清单导出选择 | #322、#323 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-07 客户Excel同版式 | #261、#262、#335 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-08 主表完整字段 | #263 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-09 宽表冻结与列宽 | #42、#44、#126、#220、#300、#302、#404、#556 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-10 客户箱单合并展示 | #28、#335、#355 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| DOC-11 SO/SI/DN文档范围 | #408、#462 | Shared formal projection / real exports; file, owner-worker and literal XLSX tests |
| AR-03 应收列与颜色 | #26、#32、#40、#42、#44、#149、#524 | Payment registration versus allocation; precise color test |
| REL-01 文字及图片放货指令 | #8、#16、#18、#19、#70、#71、#376、#485、#486、#541、#543 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| REL-02 信用放货 | #22、#25、#87、#538、#539、#540、#541 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| REL-03 实际仓库标题 | #22、#70、#71、#194、#284、#336、#541 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| REL-04 解除控货资料 | #342、#343、#355、#450 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| REL-05 控货专用到货通知 | #345、#560 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| REL-06 解控日期及取消文案 | #450、#464、#465、#560 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| REL-07 主管撤销解控 | #464、#465 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| REL-08 目的港实际放货独立 | #421、#454、#487、#488、#550、#551 | Hold / source-valid instruction / independent pickup; hold, credit, warehouse, photo tests |
| NOT-01 最新到货通知骨架 | #102、#111、#308、#510、#511、#512 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| NOT-02 最新收货通知及CBM | #179、#202、#203、#207、#527 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| NOT-03 装柜通知取代列表放货 | #552 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| NOT-04 通知标记永久保存 | #117、#155、#428、#516、#532、#533 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| NOT-05 装柜黄底与撤销 | #553、#554、#555、#557 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| NOT-06 复制当日文本日期 | #559 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| NOT-07 人工发送习惯 | #168、#169、#171 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| NOT-08 文字复制可用 | #174、#271、#484、#485、#541 | Versioned render / copy / cancel / signature; copy, loading, template and HTTP image tests |
| LABEL-01 竖版标签尺寸 | #197、#198、#200、#204、#206 | Real label renderer; PNG/PDF opens, MediaBox and fee-unit tests; physical print pending |
| LABEL-02 标签抬头与字段 | #208、#209、#210、#211、#212、#213、#214、#215、#216、#217、#218、#219、#221、#224 | Real label renderer; PNG/PDF opens, MediaBox and fee-unit tests; physical print pending |
| LABEL-03 费用文字与千分位 | #208、#209、#222、#223、#253 | Real label renderer; PNG/PDF opens, MediaBox and fee-unit tests; physical print pending |
| LABEL-04 运输变化与确认标签 | #531、#532 | Real label renderer; PNG/PDF opens, MediaBox and fee-unit tests; physical print pending |
| LABEL-05 删除多余仓储展示 | #192、#207 | Real label renderer; PNG/PDF opens, MediaBox and fee-unit tests; physical print pending |
| MBL-01 MBL数据建设 | #462 | Typed MBL / configured real workbook / original SO; template and literal-cell test |
| TRACK-01 按票件的全流程透明 | #133、#135、#264、#545 | Per-node follow and warehouse coverage / per-source public proof; tracking suite |
| TRACK-02 双语客户跟踪 | #375、#377、#382、#383、#384 | Per-node follow and warehouse coverage / per-source public proof; tracking suite |
| TRACK-03 后四位验证与柜号限制 | #401、#547、#549 | Per-node follow and warehouse coverage / per-source public proof; tracking suite |
| TRACK-04 入库及快递查货 | #419、#430 | Per-node follow and warehouse coverage / per-source public proof; tracking suite |
| TRACK-05 主分单同步与独立调整 | #545、#546、#548、#550、#551 | Per-node follow and warehouse coverage / per-source public proof; tracking suite |
| TRACK-06 海空运时间线 | #422、#459、#467、#471 | Per-node follow and warehouse coverage / per-source public proof; tracking suite |
| TRACK-07 暂停通用开放节点API | #265、#266 | Per-node follow and warehouse coverage / per-source public proof; tracking suite |
| PORTAL-01 手机号注册及自助资料 | #518 | Verified identity / current owner / bytes; provider, export, photo and pagination tests |
| PORTAL-02 导出正式客户箱单 | #303、#381、#386、#518 | Verified identity / current owner / bytes; provider, export, photo and pagination tests |

## Fix round 1 — limited gate I1–I5

The independent gate at `ca36dfadcaa4156da4a6e5b3071b51144d0dc48b` found five Important issues despite the original 181-test pass. They are addressed by implementation commit `38fe82ca5f2f7137438f9dc3691a18e2ea64330d`: restore multi-source export sequence from the immutable source snapshot; bind rates/receipt generations to the selected effective price-card set; expose native Receipt copy markers through a scoped persisted API; label XLSX currency totals correctly while retaining true fee components; and reuse shared HALF_UP conversion plus current billed components/explicit rounding in arrival/removal text.

Actual behavioral REDs and final focused GREEN (**41 passed**) are recorded in [task-4-fix-1-report.md](task-4-fix-1-report.md). The fix also covers partial-arrival REMOVE ON HOLD currency reconciliation discovered through I5's requested nonzero-adjustment test. It does not alter billing, actual warehouse/pickup facts or hold/credit policy. No schema migration was needed; runtime remains d11000400003 with no metadata diff.

Fix-round final source verification: **197 passed, 1 warning in 520.22s (0:08:40), exit 0**. Independent limited rereview and the previously listed external/template/UI acceptance boundaries remain separate; the original Task 4 pass is not used as evidence that these five omissions did not exist.
