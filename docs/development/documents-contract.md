# Documents, release, tracking and customer contract

Task 4 backend interface. This document describes executable local behavior, not production deployment, acceptance of the full 146-item project, real messenger paste, or physical printing.

## Shared transaction and authorization

All internal commands use `(db, actor, payload) -> dict`, no service commit/nested transaction. HTTP owns the single function-scoped `Database` transaction, authenticated session, `X-CSRF-Token`, and `Idempotency-Key`. Shared financial advisory guard 110002 precedes command and row locks. Replay rechecks capabilities, current source scope and original saved membership. Unknown fields fail. Typed HTTP payloads are in `app.api.documents_inputs`; omitted fields remain unchanged, explicit null stays null.

Command action capability also requires its prefix's `.read`: `documents.read`, `release.read`, or `tracking.read`. Internal formal files/notices containing invoice amounts additionally require current `finance.read` for each source invoice. Original notification/export invoice and warehouse references remain protected after regrouping. Internal command callers must let a failed command roll back the caller-owned transaction.

The private `_basis`/`_current` release helpers evaluate invoice, allocations, payment/evidence status, cargo, holds, party membership/phone, document, shipment and actual warehouse versions without returning those snapshots to warehouse operators. They are not public read APIs. A destination operator needs only `release.read` and `release.physical` on the actual destination warehouse for eligibility/actual pickup. No `bank.read` or administrator grant is required. Uploading an evidence image separately needs `attachments.write`; the upload must belong to the operator. Mixed-destination orders expose only that operator's authorized destination members, while server-side eligibility checks the whole instruction.

## Models and migration

Fixed migration `d11000400001`, parent `c11000300006`, creates the following typed records. Follow-up `d11000400002` upgrades an already applied d1 with warehouse-bound node coverage and per-destination main-node uniqueness. `d11000400003` separately retains the declared hold-removal NGN/USD rate; it does not create an invoice when a native receipt has no bill. Every model retains infrastructure ID/version/timestamps.

| Model | Authority / fields |
| --- | --- |
| HoldFact | Exactly one ticket/receipt FK, original owner-phone text, original version, active/removed state, reason/date/new confirmed phone/removal actor, supervisor cancellation actor/reason. |
| CargoPhoneHistory | Exactly one ticket/receipt FK; raw and confirmed phone plus source version. Created in the same transaction as the underlying source command/audit. |
| ReleaseOrder / ReleaseMember | paid/credit/waiver, actor, promise day, guarantor, reason; immutable original members, actual destination warehouse, complete source snapshot/hash. |
| PhysicalRelease / PhysicalPhoto | Independent ticket/order/destination FK, actor, timezone-aware event time, actual PCS/KG/CBM and original image references. |
| PaymentRegistration | Invoice FK, exactly one existing payment/evidence case FK, source explanation, actor, active flag. This is registration, never cash or allocation. |
| Notification / NotificationMember / CopyEvent | Template version, generation date/timezone, exact text and immutable source snapshot; original member/source versions/warehouses; successful-copy actor and explicit cancellation history. |
| TrackingNode | Exactly one shipment/ticket FK, named node, manual/follow, event time, actor/source; warehouse confirmation additionally binds actual destination warehouse and immutable ticket/Arrival ID/version coverage. Main warehouse nodes are distinct per destination; other nodes are unique per target/node. Audit preserves edits. |
| DocumentExport / ExportMember | Persistent Job FK, actor/customer, kind/format, original selected sources and source-bound projection/hash, resulting private attachment; optional MBL FK. |
| MBLRecord / MBLAttachment / DocumentTemplate | Stable shipment FK, typed MBL fields, versioned original BO/SI/SO/DN attachments; actual template attachment and typed-field→cell mapping. |
| CustomerIdentity / CustomerSession / PhoneChallenge | Verified phone/user association, revocable hashed session/CSRF tokens, hashed challenge/expiry/attempts/provider receipt; no anonymous code disclosure. |
| PublicAttempt | Hashed IP/action bucket, ten-minute window and durable counter; failed proof/verification responses commit the count. |

Existing non-null source phones are retained as migration observations; existing owner-phone ON HOLD markers become active manufacturer holds. The migration does not infer holds from remarks, marks or receipt identifiers. Downgrade refuses any nonempty new business/customer/attempt table; empty-schema round trip and Alembic metadata comparison are exercised.

## Holds, payment registration and release

| HTTP | Required payload | Capability / result |
| --- | --- | --- |
| POST `/api/holds/remove` | `members:[{hold_id,expected_version,source_version,finance_preview?}], reason, removed_on, confirmed_phone, ngn_per_usd` | `release.hold`, plus existing cargo/packing/financial edit rights for the corresponding shared update. Members must belong to the same shipment. Returns original member IDs, `REMOVED ON HOLD DATED ON`, `next_action=arrival`. |
| POST `/api/holds/{id}/cancel` | `expected_version,reason` | `release.supervise`; restores active hold, preserves history and returns display `REMOVED ON CREDIT`, `credit_authorized=false`. |
| POST `/api/release-orders` | `members:[{ticket_id,expected_version}],mode:paid|credit|waiver,promise_on?,guarantor?,reason?` | `release.issue`; every current grouped member must be selected. Credit additionally requires `release.credit`, actual promise day/guarantor; waiver requires `release.waiver` and shared `finance.discount`. |
| GET `/api/release-orders` | `limit=50,offset=0` | `release.read`, max page 200; minimal authorized eligibility/remaining quantity projection. |
| GET `/api/release-orders/{id}/eligibility` | — | `release.read` on actual destination; boolean validity, member receipt IDs and remaining quantities only. |
| POST `/api/physical-releases` | `order_id,expected_version,ticket_id,pieces,kg,cbm,occurred_at,photo_ids[]` | `release.physical` on destination; current complete order/source/hold/payment validation, remaining arrived stock, final-batch exact remainder and real image verification. Returns independent pickup and updated order version. |
| POST `/api/payment-registrations` | `invoice_id,expected_version,payment_id OR case_id,source_version,source` | `release.register`, current invoice/source finance/evidence read rights. Does not post payment. |
| GET `/api/tickets/{id}/receivable-state` | — | `release.read` and invoice `finance.read`; invoice, registration IDs, valid order IDs, credit flag, separate physical totals, color. |

Only source cargo-owner phone markers create holds. An ordinary edit/blank, invoice payment or credit command cannot remove an existing hold. Source-phone history is captured for create/import/edit/replacement paths with a same-transaction ORM hook. Authorized removal calls existing shared ticket/receipt mutation services, so a changed rate must satisfy their complete financial preview/version checks. Supervisor cancellation changes hold state, not debt or physical quantities.

Removal responses preserve `removal_ngn_per_usd` and explicitly report `rate_applied_to_invoice`. Ticket bills use the shared financial mutation/preview path. A native pre-bill receipt retains the declared rate as removal provenance; this command does not silently rewrite an independent price-card quote or create AR. Quote changes remain the existing explicit pricing command. Initial wholly unknown phones are already preserved as NULL on the original source row; history is appended when an actual observation/change exists, including later clearing to NULL.

Normal release requires no active own/native hold, a current applicable warehouse-arrival confirmation with covered existing Arrival batches, valid current AR and no disputed contributing payment. Credit retains the debt. Explicit tail waiver must be positive and at most NGN5000 and uses the existing discount service; it is never bank matching tolerance or a synthetic payment. Ordinary cargo/phone/member/party/warehouse/shipment changes invalidate source fingerprints even without a FinanceEvent. Copied historical text and already recorded physical pickup remain facts.

AR-03 projection: no registration/allocation is red; registration waiting for review is separately visible without increasing allocated cash; credit or a partial payment with a valid release instruction is yellow. A generic positive outstanding balance alone does not color every row red, and a partial payment without valid release does not become yellow.

## Notifications and copy protocol

POST `/api/notifications/render` takes `kind`, optional `entry`, `ticket_ids[]`/`receipt_ids[]`, and `order_id` for release. Capability `documents.notify`. Kinds/version IDs: `arrival=ARRIVAL-510-512-v1`, `receipt=RECEIPT-527-v1`, `release=RELEASE-486-541-543-v1`, `hold=HOLD-345-v1`, `remove_hold=REMOVE-HOLD-464-465-560-v1`, `loading=LOADING-552-v1`, `rates=RATE-185-207-v1`.

Receipt/arrival/hold/loading expand the shared packing-service group and persist the exact member set. Arrival/hold require an applicable explicit `warehouse_arrived` confirmation; HOLD-345 also requires an applicable `customs_cleared` confirmation. READY quantities are only covered warehouse batches. Billed quantities/amounts remain the independent shared finance actual-arrival projection, so a newly billed port batch cannot be presented as newly available warehouse stock. Loading accepts only `entry=packing-row`; that entrance rejects a release notice. Actual main/overridden loading time in business timezone and actual shipment reference are mandatory. Known-port rates use current date/transport/origin/class cards for that port; unknown port shows TO BE CONFIRMED and dated full-card reference. No card means explicit pending, not a zero quote. The cancelled storage-estimate/reminder text is absent.

The response provides `id,version,notification_id,template_version,text,generated_on,ticket_ids,receipt_ids,delivery_status=not_sent,copy_action`. Text and image both exist. `GET /api/notifications/{id}/image` returns real PNG after current source/finance/hold/order checks; no static private mount exists.

Rates/receipt generations also persist `source_snapshot.rate_dependencies`, one entry per original source row: business-local selection date, applicable/full-reference mode, source destination/transport/origin/class and the complete selected effective PriceCard records (including IDs/versions, prices and validity). Text uses that same saved selection. Image and copy rebuild and compare it, including empty-to-nonempty, newly conflicting, disabled and repriced sets. Known-port notices ignore unrelated ports; unknown destination uses the full effective reference set. Older generations lacking this dependency proof must be regenerated; their historical copy facts remain. Existing confirmed quotes and invoices are never repriced by rendering.

SEA charge conversion calls the shared `finance.settle` HALF_UP authority. Where a notice has an existing invoice, charge lines use its billed-arrival currency/components rather than full-ticket charges; this also applies to REMOVE ON HOLD with partial arrivals. Its invoice rounding adjustment remains an explicit separate line, so displayed conversion plus native NGN plus adjustment reconciles to the existing invoice. Original goods quantities and invoice records are unchanged.

Frontend sequence:

1. Generate fresh immediately before each copy, including an overnight-open modal. Four signed templates use the newly generated business-local day; receipt/payment/loading/hold event dates are never rewritten.
2. Copy text or fetched PNG using the actual clipboard API. Preserve selectable text on failure; do not substitute print/download.
3. Only after clipboard success, POST `/api/notifications/{id}/copy-events` with `expected_version,clipboard_success:true`. Stale day/source/order or false clipboard status is rejected. A persistence failure means copied but marker not saved; retry the same idempotency key.
4. POST `/api/notification-copies/{id}/cancel` with copy-event version/reason. It acts on the generation's original members even after split/regrouping. `notifications.marks(db,ticket_ids)` returns original actor/time, copy ID/version, loading label `📦已通知装柜 ✕`, yellow background/black foreground. This is never actual delivery or pickup.

## Formal files, labels and workers

POST `/api/exports`: `ticket_ids[]`/`receipt_ids[]` OR `document_id` for explicit full-document export; `format=xlsx|pdf|png`, `kind=packing|label_fee|label_plain|label_confirmation`. Requires `documents.export`, corresponding read/scope/finance rights. Label selects exactly one source and PNG/PDF. Response is persistent export/job ID and status; no first-page workbook masquerades as a complete document.

GET `/api/exports/{id}` returns status/error/ready. GET `/api/exports/{id}/file` explicitly downloads a completed source-current artifact; GET `/api/exports/{id}/clipboard` returns only PNG bytes without attachment disposition. Both recheck current source permissions, original export ownership and displayed-source fingerprint. Source changes require regeneration. No URL token or attachment ID bypass grants access.

`python -m app.domain_worker document-export` runs one explicit leased job. Actor/customer rights are refreshed before execution and publication. Projection transactions take the shared guard; expensive font/render/workbook assembly runs outside the transaction/guard. Publication checks the lease and revalidates sources. Expired five-minute leases may be reclaimed; stale workers cannot overwrite a newer completed job. Error status stores only a safe code. Startup launches no worker or external send.

The immutable snapshot's ordered `(source_type,id)` rows define export member order at execution, publication and download. Random ExportMember UUID order has no business meaning. The saved member set must exactly match the snapshot without duplicates; real member/source changes remain stale. This also restores existing pre-fix queued exports without schema changes.

Shared `packing_projection` consumes current `pricing_authority` and typed charge components. Historical quotes are not current prices. NGN/PCS stays NGN/PCS, physical KG remains separate, different currencies/units are never combined into a fake rate. Nonzero invoice `rounding_adjustment_ngn` is explicit. Unknown aggregates include `value:null,known_subtotal,pending_count`; source unknowns are never summed as zero.

Real XLSX has Master, destination-port, customer-format and Totals sheets, numeric cells and readback checks. Master columns through client phone freeze at E2; goods columns have width 20/wrap. Per-customer sheets use the same formal field sequence as PDF/PNG. All untrusted strings are literal cells, including `=`, `+`, `-`, `@`; original source values are unchanged. Original template formulas remain formulas. PDF uses literal text drawing, so source XML/HTML-looking text is not interpreted.

Master/port/Totals currency aggregates are labelled `USD charges` and `NGN charges`; legacy projection keys `freight_usd`/`clearing_ngn` remain compatible but are currency totals, not component classifications. `Actual Unit Charges` retains each real component, currency, unit, unit price and amount. Customer sheets retain the same component identities and currency totals, so NGN/PCS freight is never labelled as clearing. Unit prices use ordinary fixed decimal display, not scientific notation.

Labels are 76×130mm portrait PDF and 300dpi PNG; CARGO RECEIPT, original Logo, MC STAR LOGISTICS/广州铭鑫思达, independent CBM/destination, actual Code128 receipt barcode above source date, bold values, freight/clearing thousands and final USD+NGN cost. Confirmation uses the same geometry without fee lines. Overflow is an explicit error rather than clipping. Real printer dimensions and real 微信/WhatsApp paste remain external acceptance work.

## MBL and original templates

POST `/api/mbl`: `shipment_id,expected_version` (0 on create), dirty typed fields `number,shipper,consignee,notify_party,vessel,voyage,port_loading,port_discharge,final_destination,description,marks,packages,gross_kg,cbm,freight_terms,booking_no,sailing_on`; `documents.manage`. Stable shipment association is unique. GET `/api/mbl/{id}` returns fields, original attachment references and explicit availability for BO/SI/SO/DN.

POST `/api/document-templates`: `kind:BO|SI|SO|DN,expected_version,attachment_id,field_mapping:{typed_field:'Sheet!A1'}`; `documents.templates`. Requires a real owned XLSX and distinct valid actual cells. `POST /api/mbl/{id}/exports` takes MBL version/kind and queues the same worker; missing original template returns `template_missing`. Generated file is the configured workbook with typed fields written literally; this is not a replacement self-designed template. POST `/api/mbl/{id}/attachments` takes version/kind/owned attachment; GET `/api/mbl/{id}/files/{attachment_id}` rechecks current shipment scope before returning original bytes. Original S/O is preserved/viewable as an attachment, not overwritten by a generated document.

## Tracking, customer identity and byte privacy

POST `/api/tracking/nodes`: `target_type:shipment|ticket,target_id,expected_version,node,mode:manual|follow,occurred_at,source`; `tracking.manage`. Allowed shared node names: packing_created, packing_sent, loading, departed, arrived_port, customs_cleared, warehouse_arrived. Each ticket/node can override/clear/restore-follow independently. Actual physical pickup is never a writable/broadcast node. GET `/api/tickets/{id}/timeline` (`tracking.read`) includes bilingual labels, original actor/source, separately recorded actual arrivals and physical releases.

For `warehouse_arrived`, payload additionally requires `destination_warehouse_id`. Its action/read scope is that actual destination warehouse, so ordinary destination operators need no origin warehouse, bank or admin grant. GET `/api/tracking/warehouse-preview?target_type=shipment|ticket&target_id=...&destination_warehouse_id=...` exposes only that destination's source tickets, arrival IDs/versions, dates/quantities and target expected version. A confirmation snapshots the currently existing arrival batches. A later port/billing Arrival is never silently included by an older warehouse node. Main confirmation A and then B remain separate per-destination records; A's fact is neither erased nor applied to B. A ticket's manual override/clear blocks that node's main follow; explicit follow restores only the matching main coverage.

Ticket versions are retained for audit and stale-instruction protection, while physical warehouse applicability uses stable ticket/Arrival identities, original Arrival versions, current actual warehouse and effective node coverage. Price/phone edits or a same-shipment document split do not erase already confirmed warehouse batches. Applicable node/coverage facts participate in release/export/notification fingerprints. Revoke/edit a confirmation invalidates dependent unexecuted instructions; historical physical pickups remain. Ordinary `Arrival` remains Task 3 port/billing batch evidence; `arrived_port`, other main nodes, or existence of AR cannot substitute for warehouse receipt. No new per-ticket intake engine is mandated. Existing unbound legacy warehouse nodes require actual confirmation rather than migration guesses.

GET `/api/tickets/{id}/holds` and `/api/receipts/{id}/holds` expose the scoped current hold/source versions required by the removal form (`release.read`). Ticket response separates own and linked-native controls. GET `/api/tickets/{id}/notification-marks` exposes persisted original copy/cancel markers under `documents.read`.

GET `/api/receipts/{id}/notification-marks` provides the same persisted active-marker projection for native receipts under `documents.read` on the receipt's current warehouse. Both source kinds share the member-query logic and return `copy_id,version,kind,copied_at,actor_id,label,background,foreground`. Cancellation uses the existing `/api/notification-copies/{id}/cancel` command; subsequent fresh reads omit that cancelled marker while retaining its original CopyEvent and audit history. Browser caching is not required to restore receipt markers.

POST `/api/tracking/query`: `reference,last4,language:zh|en`. Receipt/courier candidates are individually checked against their own recorded international recipient phone (ticket requires `phone_confirmed`), before aggregation. An ON HOLD owner string, another same-mark customer, first same-receipt match or one courier member never authorizes others. Unknown/wrong suffix return the identical 404. No commodity, amount, full phone, internal note, bank, actor/source text or container-wide list is returned. Committed selected F6 observations are separately labelled historical_external_observation; excluded/raw rows are not published and no native stock/pickup is synthesized. Generic open node APIs and public raw-document/photo downloads are absent.

POST `/api/customer/verification` takes international `phone`; POST `/api/customer/register` takes `challenge_id,code`. Requires genuinely enabled configured delivery, otherwise `provider_disabled` 503. Verification code is Argon2-hashed, five-minute expiry, at most five tries, single-use; no debug response or bypass exists. A narrow HTTPS provider transport posts recipient/code/reference/purpose with a bearer secret, expects an actual `delivery_reference`; ambiguous delivery remains unconfirmed and does not permit registration. Tests replace only that HTTP transport. Failed attempts commit their counters (no thrown business error rolling them back).

Verified sessions use separate HttpOnly/SameSite strict `mcstar_customer` cookie and CSRF token. They never grant internal admin/finance/attachment APIs. POST `/api/customer/logout` validates customer CSRF, revokes the current session and clears its cookie. GET `/api/customer/documents?limit=50&offset=0` returns only currently number-linked native receipts/tickets, with one total page budget across both kinds. POST `/api/customer/exports` takes explicit own source IDs, `kind=packing,format`; requires customer CSRF and Idempotency-Key. GET `/api/customer/exports/{id}` and `/file` or `/clipboard` recheck active session, current own source records and full-file ownership, including after phone reassignment. Formal own documents intentionally retain commodity/fee fields; suffix tracking does not authorize them. Mixed original source workbooks are never exposed through this portal; it produces own-customer formal files.

Internal actual photos: scoped timelines include each actual pickup's `photo_ids`. GET `/api/physical-releases/{id}/photos/{attachment_id}` requires current destination `release.read`; verified-owner counterpart `/api/customer/physical-releases/{id}/photos/{attachment_id}` requires current ownership of **all** tickets sharing that original photo. Anonymous suffix tracking omits photo identifiers. No customer/source match grants arbitrary workbook/bank originals. All byte responses are private/no-store/nosniff.

## Configuration and verified boundary

GET `/api/documents/profile` (`documents.configure`) returns the current profile and version for editing. POST `/api/documents/profile` (`documents.configure`, expected version 0 on create) supports only china_contacts, packing_list_contact, customer_care_contact, tracking_domain, bank_name, account_holder, account_number and explicitly approved public_company_name/public_hotline/public_address/public_website. Values are controlled runtime configuration, not hardcoded customers/accounts. GET `/api/public/profile` returns only that explicit `public_` subset, never internal warehouse/bank configuration. Missing contacts/address/bank facts stay TO BE CONFIRMED.

Settings / corresponding `MCSTAR_` environment names: `phone_delivery_enabled` (false), `phone_provider_url`, `phone_provider_token` (secret, repr excluded), plus the existing `sending_enabled` (false); `document_font_regular`, `document_font_bold`, `document_font_chinese`. Windows local rendering uses the actually present Ebrima normal/bold, Microsoft YaHei and Segoe Emoji resources. Windows font files are not bundled. The original logo/logo_side bytes are in `backend/app/documents/assets`; hero photographs are absent. GET `/api/documents/resources` reports actual availability and pending exact-source layout/font-size/physical-paste evidence. Missing fonts/glyphs fail explicitly rather than silently substituting.

Pinned rendering dependencies: ReportLab 4.4.5, pypdf 6.1.3, charset-normalizer 3.5.2; existing Pillow12/openpyxl unchanged. Local tests open actual XLSX/PDF/PNG, read numeric amounts and literal cells, inspect label MediaBox dimensions, validate source permissions with real HTTP, and exercise PostgreSQL migrations. Original BO/SI/SO/DN samples, exact “24号”/screenshots, hero images, live provider delivery, real messenger paste/physical print, full React interactions and production rollout remain separate evidence boundaries.

## Requirement binding

Exact original-prompt mapping follows the binding catalog. Backend responsibility is implemented as described above; real paste/print, original visuals and full UI retain the stated evidence boundary.

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
