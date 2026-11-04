# Task 7: assistant, channel origin/delivery and connection contract

This is the canonical Task 7 interface for Task 5 migration and Task 6 forms. It extends the reviewed finance, logistics and documents contracts. It does not attest production 110 pairing, real model quality, customer delivery, current engine compatibility or deployment. All tests use synthetic PostgreSQL records and narrow external HTTP transports. No frontend shell is included.

## Transaction and authorization contract

All internal POST routes require the authenticated current actor, `X-CSRF-Token` and `Idempotency-Key`. They use the existing function-scoped `Database` transaction: commit precedes successful HTTP response. Services do not commit or start nested transactions. Unknown input fields fail with 422; business versions are checked under locks. An authorized same-key/same-body replay returns the committed result; different bodies conflict. Current source and response permissions apply to replay, including saved query results and file retrieval. Financial operations call the existing services and use their shared short transaction guard; no second ledger exists.

Threads are owned by one actual operator, a warehouse and `live|sample`. Even an administrator does not impersonate another thread owner. Channel threads additionally retain their original signed message and immutable instance/session/generation/chat/warehouse/scope boundary. Current source start time, operator map, collection pause and role/scope are checked again for queued execution, model/tool phases, confirmation, file publication and delivery. Rebinding a source after configuration changes is refused.

Capabilities are exact names, with the foundation `*` exception. `assistant.read` reads one's conversations; `assistant.act` creates turns/focus/proposals. These never replace `evidence.read/write/review/export`, `bank.read/import/review/export/identity/override/release`, `finance.read/post`, `packing.read`, `documents.read/export/notify` or attachment ownership. `connections.read` reads cached status; `connections.manage` manages operations/configuration and diagnostic observations. `channels.read/act/send/collect/manage/review` retain their separate meanings. Phone sender/operator identity is not a customer identity.

## HTTP routes and request schemas

All paths below begin `/api`. IDs are the real persisted UUIDs. Output money uses decimal strings. Common conflict/error shape is unchanged: `{"error":{"code":"...","message":"...","details":null}}`. The authoritative strict request classes are `app.api.assistant` and `app.dialogue.inputs`; their generated tool schemas are in `assistant-tool-schema.json`.

| Method/path | Input and response |
| --- | --- |
| POST `/assistant/threads` | `{warehouse_id, scope?:"live"|"sample"="live", title?:string<=200="", source_message_id?:string|null}`. Returns thread record, including `id,version,actor_id,warehouse_id,scope,source_message_id`. A web thread omits source_message_id. |
| GET `/assistant/threads` | Up to 100 current-operator threads, newest first. Current warehouse scope filters results. |
| GET `/assistant/threads/{id}` | Thread plus ordered `turns[]` (text, result, job/status/error), `focus[]`, `files[]` (id/kind/authenticated URL). Saved result permissions are rechecked. |
| POST `/assistant/threads/{id}/turns` | `{expected_version>=1,text?:string<=16000="",source_message_id?:string|null,attachment_ids?:string[<=20]=[]}`. At least text or attachment is required. Channel text must equal the original signed message body. Returns turn id, job_id, thread_id/thread_version, status, executed:false. Files must be personally owned original uploads. |
| POST `/assistant/threads/{id}/focus` | `{expected_version,items:[{case_id,expected_version,image_id?:string|null}]}` max 40. Exact shown order and each case's own version are saved. An image must belong to that case. |
| POST `/assistant/threads/{id}/tools` | `{expected_version,turn_id,name,arguments}`. `name` is one of the strict tools below; arguments are validated against that tool's schema. Uses the same dispatcher as the model, so Task 6 can provide ordinary forms while model calls stay disabled. Returns domain result plus thread_version. |
| POST `/assistant/proposals/{id}/confirm` | `{expected_version,nonce:string[32..100],source_message_id?:string|null}`. Web uses CSRF; channel requires the exact signed `/confirm <proposal-id> <nonce>` from the same authorized boundary. Returns executed:true only after the existing domain service succeeds. |
| GET `/assistant/files/{id}/status` | Version, kind, ready, job status/error; completed bank previews include their actual `preview` with bank file id/version/account mapping. |
| GET `/assistant/files/{id}` | Actual checksum-verified private bytes with their real media type, forced attachment/no-store/nosniff. Pending files return 409. HTML exports also use restrictive CSP. Formal document and notification source checks run at retrieval. |
| POST `/assistant/files/{id}/send` | `{expected_version,recipient:string<=250}`. Explicit human-selected allowlisted target only; no recipient-selecting model tool exists. Queues a guarded typed Delivery; returns delivered:false/provider_accepted:false. Defaults block sending. |
| POST `/connections` | `{expected_version>=0,role:"main"|"assistant"|"capture",session:string[1..100],instance:string[1..100],generation:string[1..100],recovery_enabled?:bool=false,alert_actor_id?:string|null,alert_recipient?:string|null}`. Zero creates; current version updates. Role/session unique. Current actor becomes operation owner. Alert actor, if supplied, must be that actor; target must already be allowlisted. Changing identity resets cached pairing/status/QR and advances revision. |
| GET `/connections` | Cached records plus online/qr_available/diagnostic. `online` requires a WORKING observation within 90 seconds; it never comes from generic service health. No external IO occurs in this GET. |
| POST `/connections/{id}/operations` | `{expected_version,kind:"inspect"|"qr"|"connect"|"recover"|"diagnose"}`. Returns a persisted pending operation, not provider success. Disabled flags return 409 before external IO. |
| GET `/connections/{id}/operations` | Latest 100 operation records, including revision, boundary, attempted_at, outcome and lease. |
| GET `/connections/{id}/qr` | Current PNG only during SCAN_QR_CODE and before 45-second expiry, with current attachment ownership. No implicit provider fetch. |
| POST `/channels/automations` | `{id?:string|null,expected_version>=0,kind:"summary"|"echo",warehouse_id,scope,source_group,destination,paused?:bool=true}`. New=version 0; existing uses id/current version. Source and destination must differ and match configured source warehouse/target whitelist. |
| GET `/channels/automations` | Current operator's scoped configurations and latest 100 history events per configuration. |
| GET `/channels/messages?state=&after=&limit=50` | Warehouse-scoped source records; max 200, next cursor. Exposes origin_held for operator review. |
| POST `/channels/messages/{id}/origin` | `{expected_version,decision:"human"|"bot",reason:string[1..2000]}`. Only configured trusted owner/operator can resolve their own held fromMe source. Known exact outbound IDs cannot become human. Resolving a distinct phone message never resends the uncertain delivery. |
| GET `/channels/deliveries/{id}` | Own delivery plus trusted participant receipts and `group_all_received:false`. Receipt history stays readable to a currently authorized operator after runtime configuration revokes future IO. |
| GET `/channels/receipt-observations` | `connections.manage`; latest 100 signed receipt observations, including unknown formats held for review. |
| POST `/webhooks/waha` | Existing bounded 1 MiB raw-body HMAC-SHA512 endpoint, not an anonymous actor. Adds signed session.status and message.ack/message.ack.group handling. Receipt history is accepted even while collection is paused. |

## Strict tools

Tool calls cannot provide SQL, filesystem paths, URLs, code, credentials, session control or arbitrary recipients. There are at most four model requests and eight tool calls in one leased turn. Invalid/unknown tools produce fixed error results; the remaining bounded loop may repair arguments. Uninterrupted processing persists actual tool results. Interrupted running turns become needs_review; no earlier send or business effect is replayed.

| Tool | Exact arguments and behavior |
| --- | --- |
| query | `{kind:"cases"|"bank"|"bank_files"|"archives"|"templates"|"files"|"cargo"|"invoices",query?:string<=200="",amount?:string|null,currency?:"NGN"|"USD"|"CNY"|null,transaction_date?:ISO-date|null,account_id?:string|null,offset?:int[0..1000000]=0,limit?:int[1..100]=20}`. Bank and bank_files require explicit account_id. Returns items, next_offset and coverage. Bank coverage stays unknown; zero matches never claims nonpayment. Case results persist the shown image order and exact versions in focus. Operational cargo/documents are live-only. |
| case | `{case_id}` → actual case/images/source contexts/reviews under current permissions. |
| compare | `{case_ids:string[1..10]}` → deterministic bank candidate/difference results. No financial mutation. |
| source_assessment | `{case_id,attachment_id,message_id?:string|null}` → complete reviewed fields/source fingerprint for supplementation. Original source text remains data. No model assertion counts as a bank receipt. |
| propose | `{action:"evidence.review"|"bank.review"|"bank.import.confirm"|"evidence.attach"|"payment.post",target_id?:string|null,focus_position?:int[1..40]|null,payload:object}`. Payload is revalidated using the existing strict ReviewInput/BankReviewInput/ConfirmImportInput/ImagesInput/PostInput. Target id is outside payload. Current exact target version required; ordinal focus supplies that selected case's saved version. |
| bank_preview | `{attachment_id,account_id}` → durable file job. Parses original XLS/XLSX outside financial guard, then commits the existing BankFile/BankSourceRows preview under reauthorization. Does not import/claim money. |
| file | `{kind:"evidence"|"bank_original"|"archive"|"document"|"upload"|"pending"|"notification",id?:string|null}`. Evidence id=EvidenceImage, bank_original id=BankFile, archive/upload id=Attachment, document id=DocumentExport, notification id=Notification. Pending requires explicit export, not a fake file link. |
| export | `{kind:"pending"|"bank_archive"|"document",payload?:object={}}`. Requires an explicit human export intent. pending is the complete scoped non-bank-verified list, genuine HTML, no 200-row truncation or false PDF extension. bank_archive payload uses existing ArchiveInput including original template_attachment_id and versioned claims. document payload uses Task 4 Export schema. |
| notification | Exact Task 4 Render schema: `{kind:"arrival"|"receipt"|"release"|"hold"|"remove_hold"|"loading"|"rates",entry?:string|null,ticket_ids?:string[<=200]=[],receipt_ids?:string[<=200]=[],order_id?:string|null}`. Uses current formal generation. Returns draft text plus a protected file reference, sent:false. |

Evidence review payload is `{expected_version,fields?:{amount?,currency?,transaction_date?,payer_name?,transaction_reference?,recipient_account?,bank_name?,business_reference?},action?:"correct"|"defer"|"retract"|"reject"="correct",reason?:string|null}`. Every field accepts explicit null and omission remains distinct. Reject/defer/retract need no irrelevant bank ID. Supplementation uses full `source_assessment:{verdict,reason,fields,source_fingerprint}`; same SHA with different text is not assumed corroboration. Posted correction preserves cash/allocations and disputes the existing claim through the shared service.

Proposals return `{id,version,status:"confirmation_required",preview,nonce,confirmation_command,expires_at,executed:false}` with ten-minute expiry. Questions, conditional/negative text and mismatched action intent return question_only. Payment posting returns an authenticated `/finance/payments/{id}` continuation so the complete current party/invoice/amount/FX/source/allocation preview is confirmed in the normal financial flow. A model completion without actual tool results yields a deterministic “not executed” reply. Provider text never becomes an execution receipt.

## Files, documents and delayed sends

DialogueFile binds actual attachments or Task 4 exports/notifications to a thread and source snapshot. Originals retain MIME/hash/bytes. Source reads and exports reuse Task 4 authorization, original member order, current rate-card collection, fresh notification signature day, billed arrival currency/rounding, actual warehouse source coverage and current artifact ownership. Pending and archive workers lease→release transactions→parse/render→recheck sources and permissions→publish. Source changes fail explicitly. Artifact delivery validates current formal source before and after file IO, then submits only through the existing delivery worker. It never sends cached Notification.body or an attachment id as an authorization shortcut.

WAHA media retrieval requests one current authorized message by encoded session/chat/provider id. Only the configured provider origin and `/api/files/` endpoint are accepted; traversal, nested escaping, userinfo, fragments, arbitrary origins and redirects fail. JSON responses are bounded to 1 MiB; files to max_attachment_bytes (default 20 MiB), timeout bounded. PNG/JPEG/WebP are verified, XLS/XLSX originals retained (XLSX ZIP counts/decompressed limit checked), PDF headers and UTF-8 HTML checked. No arbitrary message text/model URL/path is fetched. Original media attachment identity is persisted on ChannelMessage. Image evidence and captionless originals can enter the same authorized channel thread.

## Origin, delivery and unknown outcomes

`fromMe` alone is never a bot flag. Configured trusted owner JID, participant identity, actual chat and precise outbound message id are used. Known outbound IDs become bot_ignored; unknown owner/participant conflicts stay origin_held. During sending or uncertain IO to that session/chat, own-source callbacks are held. After a provider response stores its exact ID, matching held callbacks become bot; distinct trusted human messages may become recoverable. Text equality is never identity. A held human with an outstanding uncertain send requires explicit owner review; the uncertain send itself remains untouched.

Delivery states: pending→sending→submitted only for a successful provider response with actual id; explicit rejection→failed; timeout/missing id/process interruption→uncertain. Neither failed nor uncertain is automatically retried. Real signed ack facts are stored separately: 1=server, 2=device, 3/4=read/played. Direct delivery may advance to delivered/read; group delivery remains submitted and reports each trusted participant separately. Unknown/unsupported signed receipt shapes are persisted as held observations. A receipt arriving before the send response is linked later by exact instance/session/message/chat. A configuration change cannot erase already-observed facts.

## Connections and recovery

Connection identity/configuration is separate from provider/session observations. An operation captures a monotonically increasing request revision and identity boundary; publication refuses an older response after a later status/phone-verification event. QR is invalidated on state changes. WORKING, scan/phone/password/passkey verification states are not automatically restarted. An API health result alone never establishes WhatsApp online.

inspect uses GET `/api/sessions/{session}`; 404 becomes NOT_CONNECTED. QR uses GET `/api/{session}/auth/qr`, PNG only, at most 1 MiB/4 million pixels. Explicit connect is allowed only STOPPED/FAILED/NOT_CONNECTED, with 30-second manual cooldown. It uses start/restart; creating an absent session additionally requires the deployment's explicit protected webhook URL/key and creates only that named session. No logout, reset, engine upgrade, alternate account reset, read acknowledgement or proxy-route change exists.

Automatic recovery requires recovery_enabled, ever_paired plus actual owner id, nonblocked auth, FAILED/STOPPED for at least 60 seconds or STARTING for 300 seconds, and a successful TLS+WebSocket transport probe within 90 seconds. Cooldown is 10 minutes; at most two actual connect/recover attempts per rolling 60 minutes. Failed/uncertain requests consume attempts. Pre-IO source/auth/transport deferral has attempted_at=NULL and cannot claim a restart. Expired running operations become uncertain and do not replay. The original bounded proxy CONNECT/TLS/WebSocket probe is adapted without account login; it does not establish account or delivery success.

Offline after sustained 60 seconds and stable recovery after 20 seconds create typed ConnectionEvents and controlled Outbox records. Only the selected currently authorized actor/allowlisted recipient is eligible. Sending disabled leaves outbox facts visible without IO. Summary and echo configurations/history remain persisted and paused by default; their worker needs collection+summaries+sending flags and distinct source/destination, current start/boundary/actor. Summary reuses the first successful whole-image candidate; it never invokes paid recognition again. Source message/event id deduplicates delivery.

## Deployment configuration and protected secret references

Settings added: `connection_control_enabled=false`, `connection_diagnostics_enabled=false`. Existing collection_enabled, summaries_enabled, model_calls_enabled, sending_enabled remain false; main_account_read_only remains true, and main sends/read acknowledgements are prohibited regardless. Startup creates no workers and initiates no connection.

Only deployment-controlled environment/runtime storage supplies secrets, never HTTP form payloads. `MCSTAR_WAHA_API_KEY`, `MCSTAR_WAHA_WEBHOOK_KEY`, `MCSTAR_WAHA_TRANSPORT_PROXY_URL` (may contain proxy credentials), `MCSTAR_DIALOGUE_API_KEY` are secret references, not example values. WAHA nonsecret config names: `MCSTAR_WAHA_BASE_URL`, `INSTANCE`, `GENERATION`, `CAPTURE_SESSION`, `ASSISTANT_SESSION`, `MAIN_SESSION`, `SINCE` (Unix seconds), `SCOPE`, `GROUPS` (JSON chat→warehouse), `OPERATORS` (JSON sender→real user), `OWNERS` (JSON session→trusted own JID), `RECIPIENTS` (JSON target array), `WEBHOOK_URL`. These suffixes all use `MCSTAR_WAHA_`.

Model adapter: `MCSTAR_DIALOGUE_ENABLED=false`, `MCSTAR_DIALOGUE_BASE_URL` (HTTPS OpenAI-compatible chat-completions base), `MCSTAR_DIALOGUE_MODEL`, `MCSTAR_DIALOGUE_API_KEY`. It reuses the existing ModelConfig validation/profile and original dialogue thinking parameters (`enable_thinking:true`, `preserve_thinking:false`). No provider/model is invented as a production default. Normal console output contains only operational ids/status/called/error codes; source/model content and nonces stay in protected database records. Tests never load original 110 secrets or create API keys.

## Workers

With `PYTHONPATH=backend`, run the existing explicit CLI `python -m app.domain_worker <kind>`:

| kind | Work |
| --- | --- |
| channel-turn | Persist one accepted human assistant-session message in the shared thread, or execute one exact authorized nonce confirmation. Collection gate; no network. |
| dialogue | One leased bounded model/tool turn. Both global model_calls_enabled and configured dialogue adapter must be active. Network transport fake in tests only. |
| assistant-file | One original bank-preview, complete pending HTML or original-template archive job; no external network. |
| document-export | Existing Task 4 formal export renderer; assistant references use this same worker. |
| connection | One explicit leased inspect/QR/connect/recover/diagnose operation, gated by the corresponding control/diagnostic flag. |
| connection-monitor | Local offline/recovery timers, eligible capped recovery intent and selected alert outbox→delivery queue. No direct send. |
| channel-automation | One configured summary/echo event using existing whole candidate. No model calls. |
| delivery | Existing guarded delivery worker, extended with file/notice source checks and accurate receipts. |
| media --operator-id USER | Existing bounded provider media worker, extended to genuine XLS/XLSX/PDF/HTML originals. |

## Typed schema and Task 5 migration obligations

Fixed revision `e11000700001` follows `d11000400003`; explicit frozen tables/columns/FKs/indexes, not live metadata.create_all. Every new table inherits id/version/UTC created_at/updated_at.

| Model/table | Typed relationships and state |
| --- | --- |
| DialogueThread / dialogue_threads | actor FK, warehouse FK, live/sample scope, original ChannelMessage FK, title |
| DialogueTurn / dialogue_turns | thread FK, unique Job FK, unique nullable source message FK, human text, protected result snapshot, model profile |
| DialogueFocus / dialogue_focus | thread FK + unique position; case FK, exact case_version, nullable image FK |
| DialogueFile / dialogue_files | thread/turn FKs; nullable Attachment/DocumentExport/Notification FKs; kind and immutable source/export snapshot |
| DialogueProposal / dialogue_proposals | thread/turn FKs; allowlisted action, validated payload, nonce hash/expiry/used time, actual domain result |
| Connection / connections | unique role/session; instance/generation/operator FK; observed status/timestamps/revisions; owner/ever_paired/auth block; QR attachment/expiry; transport result; recovery and selected alert actor/recipient |
| ConnectionOperation / connection_operations | connection/actor FKs; kind, captured boundary/revision; state/lease; actual attempted_at and outcome |
| ConnectionEvent / connection_events | connection FK, offline/recovered kind, Outbox/Delivery FKs |
| DeliveryReceipt / delivery_receipts | unique(instance,session,source_id,chat_id,participant,ack), Delivery FK when linked, received_at |
| ChannelReceiptObservation / channel_receipt_observations | unique(instance,session,body_hash); signed event type/payload; parsed/held state; nullable receipt FK |
| ChannelAutomation / channel_automations | kind/actor/warehouse/scope, source/destination, paused, captured deployment boundary |
| ChannelAutomationEvent / channel_automation_events | unique(automation,message), source message FK, whole recognition FK, delivery FK, state |

Existing ChannelMessage adds from_me (false for prior rows), origin_state (human for previously accepted rows), nullable original attachment FK. Delivery adds captured boundary and nullable DialogueFile FK. Old Task 2 `delivered` rows are conservatively renamed submitted because that version only established API acceptance. Legacy pending deliveries without a captured boundary fail closed. Migration never invents participant receipts, pairing success or bank/financial facts. Nonempty new histories/provenance prevent downgrade; empty schemas support base/head round-trip. Preserve source mappings and originals for all these entities, migrate running leases to needs-review/paused, and never replay historical or uncertain sends.

## Validation and evidence boundary

The Task 7 report under `.superpowers/sdd/2026-11-04-unified-implementation/task-7-report.md` records actual RED/GREEN commands, final full-suite count, Alembic upgrade/check/round-trip/protection and implementation SHA. Tests cover real PG effect and role/source/nonce/version boundaries, phone-owner vs bot race, early participant receipts, default called:false, failed recovery caps, stale QR, real original files, complete 205-row pending HTML, current document/notice revalidation and bounded transport. Scaffold missing-module RED is distinguished from behavioral counterexamples.

Requirements: INT-01/02/03/04/05/06 are this round's engineering integration requirements (not invented MCSTAR prompt numbers). SYS-03 maps #57/#100/#116/#347/#418/#465; SYS-05 #59/#60/#65/#67/#69/#72/#115/#225/#226/#324/#330/#358/#360/#432/#530; SYS-07 #145. DOC-01/06/07/11 and NOT-01/02/04/06/07 reuse their existing Task 4 prompt mappings rather than reinterpret templates. Original source code and current official WAHA docs are implementation references, not evidence of real production pairing/sending/model quality. Those checks remain external acceptance work.
