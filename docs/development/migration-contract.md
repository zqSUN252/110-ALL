# Migration / recovery contract

Task 5 consumes the reviewed schema/finance/logistics/documents/assistant contracts. Services use the caller transaction and never commit. The HTTP `Database` dependency owns one function-scope transaction. Original 110 and MCSTAR sources remain read-only. No external provider is enabled by these tools.

## Runtime and source boundary

- `private/runtime.json` configures the project-owned PostgreSQL 17 cluster; `tools/dev_runtime.py status|start` only targets `private/postgres17`. `start` starts a stopped owned cluster and leaves a running cluster alone. Windows launch uses hidden WMI process creation. No installed service, old application or network configuration is changed.
- `deploy/start-local.ps1` defaults to `private/supplied-snapshot-runtime.json`; this points to the verified supplied-snapshot schema, not the unrelated empty public schema. It starts the new local API on port 8011, with all external actions disabled. The UI must label this dataset “supplied snapshot / ownership pending”, not production acceptance.
- Source private ZIP inputs, attachments, detailed controls and all backups stay under strict approved-principal ACL. No source ZIP or old credentials are added to normal business backup records. New source inputs have an opaque ID and digest, no normal download endpoint. The original ZIP remains at its original read-only location.
- Synthetic tests, restore validation and review datasets use separate schemas. The five original QA SQLite databases, older MCSTAR test/deployment snapshots and credential/session artifacts are never promoted into live finance.
- `make_engine` defaults to 3-second connection and pool acquisition timeouts, overridable by its explicit kwargs. `/health` returns `503 {"status":"database_unreachable"}` without a DSN or exception body when database connectivity fails.

## Typed source business model and UI

The supplied source exposes 439 parties, 28 shipments, 964 ticket rows, 619 bills, 60 slip observations, 73 ledger observations and 165 committed F6 records. The 917 distinct receipt strings are not transaction identities. Source bills remain one control each, including multi-ticket groups. Technical import ownership is an inactive `__migration_nonlogin__` identity, not a historical reviewer or customer.

`LegacyParty`, `LegacyShipment`, `LegacyTicket`, `LegacyBill`, `LegacyBillMember`, `LegacyPaymentObservation` and `LegacyF6` are typed, queryable records. Nullable native links record unresolved ownership, without an unknown warehouse/customer FK. `raw`, source key, JSON pointer, source batch, version and source state retain evidence. `MigrationSourceMap` maps field pointers to typed IDs. `MigrationIssue` represents missing references, files, unknown fields and unresolved controls. `LegacyArchive` retains trash, tombs, history, settings and inert operations; source credentials are scrubbed before this archive exists.

| HTTP endpoint | Authority / behavior |
|---|---|
| `GET /api/legacy/{kind}?limit=50&offset=0&query=` | `legacy.global_read`; `finance.read` additionally for bills, tickets, slips, ledger. `kind`: parties, shipments, tickets, bills, slips, ledger, f6. Response `items,total,source,warehouse_ownership`. No raw field in collection rows. |
| `GET /api/legacy/{kind}/{id}` | Same narrow global permission plus finance.read for raw detail; source/issues retained. Ordinary warehouse-limited finance grants do not confer this global permission. |
| `POST /api/legacy/parties/{id}/resolve` | `legacy.resolve`, `legacy.global_read`, parties.manage/read in selected real warehouse. Body `expected_version,warehouse_id,source`. |
| `POST /api/legacy/shipments/{id}/resolve` | `legacy.resolve`, global/read/finance.read and shipment/packing scope. Body `expected_version,warehouse_id,uploaded_on,physical_reference?,source`. Source `no` and `bl` stay separate; absent physical reference remains empty/pending. |
| `POST /api/legacy/bills/{id}/resolve` | `legacy.resolve`, global/read/finance.read and finance.opening in selected warehouse; confirmed PartyWarehouse membership. Body `expected_version,party_id,warehouse_id,source`. Original currency/dates/control must satisfy native invariants. |
| `POST /api/legacy/tickets/{native_ticket_id}/charge-basis` | Explicit source review, same warehouse packing.manage/read/finance.read. Body `expected_version,charge_components,source`; components must explain exactly the original USD/NGN totals. It does not reprice the bill. |
| `GET /api/migration/batches`, `.../{id}/issues`, `.../{id}/sources` | Administrator only; sanitized batch overview, paged issues and scrubbed source archive. Separate from normal global legacy business access. |
| `GET /api/migration/files/{id}` | Narrow global + finance.read + attachments.read; hash-verified bytes, no-store. After native shipment resolution original workbook references also become normal TicketAttachment links. |
| `GET /api/public/profile` | Only explicitly supplied Hotline and Openshipment public fields; no raw prompt or warehouse ownership inference. |

Mutations require current authenticated session, CSRF and `Idempotency-Key`. Each uses source/current version checks and records a current resolution actor plus evidence; source by-text never authenticates an actor. UI should expose distinct unresolved/source, resolved/native and disputed states and link source issues. It must not display an unresolved native balance as an actual zero.

## Historical finances and group invariants

- All 619 source bill currencies are explicitly NGN. Unknown original currencies on 16 slips stay unknown. Slips and ledger are two observation granularities, not 133 new payments; 55 missing UIDs do not become generated payment identities.
- One resolved source bill creates one Invoice and one preserved historical discount layer. Original received is separately projected as `historical_received_ngn`; no Payment/Allocation is generated. `allocated_ngn` contains actual native allocations only; `received_total_ngn` adds the historical layer. Native outstanding, release eligibility, exports and assistant finance queries share this computation.
- `historical_bank_state` distinguishes unknown historical controls from current evidence; copying a source approval does not create a new bank verification or a new original approval date.
- Group members share one Invoice ID. Export totals count each invoice once. A pending source relationship blocks automatic AR/AP duplication. Arrival cannot replace a group invoice with a member subtotal. Splitting requires the whole source billing group; partial split and per-member destructive void/reprice are explicitly blocked for group financial reconciliation. This is a typed pending resolution, not permission to invent cash/credit.
- Invoice adjustment previews retain `historical_received_ngn`, `historical_discount_ngn`, `historical_excess_ngn` and `historical_review_required`. Historical excess is not `return_to_balance_ngn`. Decreasing discounts below the preserved historical layer or creating unsupported excess is blocked. Actual new payments alone may return to their own customer's unallocated balance and be reversed.
- Missing party/shipment references remain issues. An explicit administrator/source confirmation can link real native ownership; name/date/amount/receipt equality never does so automatically. A changed snapshot is frozen as a new source observation, with no incremental cash or guessed row identity.

## Explicit cross-source bank evidence

`POST /api/legacy/bank-evidence/preview` accepts `claim_id,claim_version,case_version,entry_version,account_version,control_owner_party_id,members,reason,source,cross_party?`. Each member supplies `bill_id,expected_version,invoice_version,amount_ngn,source`. Confirmation posts the identical data plus the returned `preview` to `/api/legacy/bank-evidence/confirm`, with CSRF and Idempotency-Key. `/api/migration/bank-evidence/{id}` returns authorized proof detail. These require legacy.global_read/resolve plus finance.read/post, bank.read/review and evidence.review over the actual case and every affected bill warehouse/account. Same owner/scope is the default. Cross-owner coverage requires the existing `finance.cross_party` capability across the full affected scope and exact source-backed invoice/party beneficiaries; it never infers a payer.

`LegacyBankEvidence` is one header per unique claim, storing original bank basis/version/hash, scope, account, control owner, current confirmation actor/reason/source and optional beneficiary authorization. `LegacyBankCoverage` records one or more versioned original bill controls and exact NGN coverage. Sum must equal the original bank credit; each bill's cumulative coverage cannot exceed its original historical received, including disputed proofs. Only a current verified_unallocated claim without a Payment is eligible; other currencies without source FX facts remain pending.

Binding changes the claim to `historical_bound`, still covered by the existing non-released uniqueness indexes. No Payment, Allocation, original payer/currency, old received or historical approval date changes. Generic release/resolve/post cannot bypass a binding. Source/bank evidence changes dispute both current claim and proof, retaining occupancy and old facts. Current bill projection reports linked, partially_linked or disputed; a disputed proof blocks release until reconciled. Full backup/restore includes both proof tables. Destination warehouse personnel still use the existing minimal release.read/release.physical capabilities to verify and execute an eligible instruction; they do not gain or need global legacy/bank/finance read privileges, and their projection excludes those raw financial fields.

## Nullable SEA quantity compatibility

Approved engineering ruling based on source #337–340: only SEA whose original KG is unknown and whose nonempty, fully known charge components are explicitly non-KG can record Arrival/PhysicalRelease without KG. AIR, EXPRESS, KG pricing, missing pricing basis and known KG keep strict requirements/ceilings. Native ReceiptSpec is unchanged. The removed AIR-style SEA arrival row action is not restored.

Ticket, Arrival and PhysicalRelease KG remain NULL across batch sums, warehouse coverage, remaining quantities, export and notifications. A measured batch KG may be stored without creating a source total. Mixed known/unknown totals remain unknown; projections may expose known subtotal and pending count. Every known PCS/CBM/KG total and final remainder stays bounded. Import never creates a historical Arrival, warehouse receipt, customs clearance or physical pickup. Actual port/billing Arrival, warehouse-bound coverage and physical release remain distinct.

## MCSTAR CLI and normal upload workflow

Use `.venv/Scripts/python.exe tools/migration/run.py ACTION --archive <read-only-original-zip> --dataset <stable-namespace> --schema supplied_<name> [--plan <private-plan.json>]`.

Actions are `inventory`, `stage`, `reconcile`, `plan`, `apply`, `verify`. Detailed staging/reconciliation and reviewed plans live under `private/migration/<schema>`. `apply` and `verify` require the exact reviewed plan. The plan binds source/member hash, parser version, configuration hash, source mappings, issues and file references. Exact second apply creates zero records. `verify_independent.py` separately rereads original Decimal controls, quantities, references, F6 provenance and stored file hashes. Private detailed money never appears in stdout.

For Task 6 normal browser uploads:

1. `POST /api/migration/sources`, raw `application/zip` body, admin session+CSRF. Separate 64 MiB compressed archive limit, at most 10,000 members/256 MiB declared total/64 MiB per member, bounded ratios, no encryption/duplicate/traversal paths. The actual supplied archive is approximately 30.9 MB, so ordinary 20 MiB business attachment limits are not used. Only the known data/prompt members are read; no source JS is run/extracted.
2. `GET /api/migration/sources/{opaque_id}/plan?dataset=...` returns the reviewable counts/hashes/plan. No server filesystem path is accepted from ordinary HTTP input.
3. `POST .../{opaque_id}/apply` with `{dataset,plan_hash}` and Idempotency-Key queues an inert-until-run local migration job. `GET /api/migration/jobs/{id}` reports state/result. Invoke `tools/migration/worker.py` to execute one authorized local job; no automatic model/channel worker starts.
4. `POST /api/migration/backups` queues an admin backup job through the same explicit worker. Authorization is rechecked when the job starts. Interrupted running work becomes needs_review, not blindly replayed. File acquisition/parsing/backup does not hold the global financial advisory guard.

Known active originals: 37 JPEG slip refs and 11 XLSX shipment refs. Hist/trash originals are additional historical references, not active cargo. Exact identical bytes may share a private object while every source reference remains distinct. Missing slip originals remain explicit issues; no placeholder image is generated.

## Generic 110 SQLite acquisition and native adapter

`tools/migration/sqlite_export.py --source <owner-supplied-db> --manifest <private.json> --snapshot <new-private.db> --output <new-private-export> --classification synthetic|production_supplied` accepts only:

- `assertion=owner_consistent_backup`: source owner explicitly produced a consistent standalone database; not merely an arbitrary .db without visible WAL.
- `assertion=owner_frozen_bundle`: owner explicitly confirms the entire DB+WAL(+SHM) bundle is frozen. Manifest contains `source` evidence and exact filename→SHA256 `files`. Companions are copied through OS read-only handles into owned private workspace; only the copy is opened by SQLite. Hash stability checks detect change but do not prove freezing on their own.

The copy undergoes real SQLite Backup API, bounded acquisition, integrity check and full-table counts/columns. Unconfirmed live sources and changed bundles fail closed. Committed uncheckpointed WAL is tested and retained; original DB/WAL/SHM bytes and Windows security descriptors (owner, ACL and inheritance) stay unchanged. `mode=ro` alone can change SHM readmarks; immutable/raw base copies are not used as a consistency claim. No old services are stopped or automatically inspected.

CLI snapshot/export outputs must be under the configured owned private root. Use a new snapshot directory and a separate new export directory; existing destinations are rejected. Before mkdir, ACL protection or writes, resolved paths and every existing ancestor are checked for reparse points. Output roots must be disjoint from the source directory and any manifest `bundle_root` or `attachment_root`, in both directions. Same-parent input/output is rejected: recursive private ACL protection must never reach originals. The source owner declaration is still required; layout checks do not establish source consistency.

Every table/column/nested JSON pointer is archived or explicitly credential-excluded. Unknown tables produce issues. Attached files require explicit controlled relative paths and hash manifest; no source paths are automatically followed. Known message session strings are routing provenance, not executable sessions. Historical queues never become executable jobs.

`tools/migration/apply110.py plan|apply --export <private/source.json> --mapping <private/mapping.json> --dataset <stable-id> --plan <private/review.json> --actor <active-native-admin>` implements native mapping of known 110 bank files/imports/rows, workflow cases, original source images, confirmed ledger/claim occupancy, reviews and inert outbox. It accepts no QA classification. Mapping supplies explicit source evidence, warehouse, scope, per-import native account and expected version, uploaded hash-matching original bank file Attachment IDs, per-case account IDs and optional image IDs/channel instance. Missing ownership/original bank files block native apply. No amount/name matching chooses ownership.

Original bank claim/ledger state, reviewer text, reason, dates and versions remain in immutable source archive/EvidenceReview fields. Current imported BankClaim is disputed until current basis is revalidated, retaining active uniqueness. Duplicate original fingerprint candidates are not merged and cannot be newly claimed until adjudicated. No Payment is generated. Known historical messages are created only with actual time and explicit routing mapping; incomplete ones retain issues. Old outbox body stays private archive; native delivery body is blank, with historical/uncertain state and non-executable provenance.

No production 110 database or complete original attachment directory has been supplied. Synthetic known-format capability is verified separately from unavailable production migration acceptance.

## Backup, restore, incrementals and cutover

`tools/backup.py backup --path <new-private-bundle>` captures all current model tables in a PostgreSQL REPEATABLE READ snapshot, hashes every table and referenced immutable attachment, and rereads hashes on restore. It retains typed sources, source maps, financial/logistics/document/assistant histories, identities and grants. Sessions, OTP, nonce actions and executable proposals are excluded; passwords require a reset, work pauses, sending becomes uncertain, recovery and automations remain disabled. Root source ZIPs, environment credentials and source-input files are not business backup objects.

`tools/backup.py restore --path <bundle> --schema restore_<newname>` accepts only an empty owned schema and a new protected attachment root. It validates the exact source migration revision; older bundles must be restored using their matching source revision before upgrade. Foreign keys remain enabled, temporarily deferred for cross-module cycles, validated, and restored to their original flags. Nonempty targets refuse overwrite. `tools/restore_access.py --username <existing-native-user> --reason ...` permits local reset only of a restored native account explicitly marked reset-required; it never activates the importer or imported old users and has no fixed password. Password comes from protected process environment or hidden interactive input, never arguments/output.

`incremental --base <full> --current <next-full> --path <new-delta>` produces real keyed row/file changes. `materialize --base <full> --current <delta> --path <new-full>` reconstructs and verifies the next full bundle. Neither replaces an operational DB in place.

`archive --path <bundle>` returns `not_archived` with no configured destination. `--team-directory <explicit-controlled-directory>` uses an actual write+readback adapter; a mismatch fails. No cloud/team destination is guessed, and no external archive was performed.

`freeze|cutover --expected-version N --generation <id> --source <handoff-evidence>` updates the owned DB writer generation. Mutating HTTP, idempotent domain commands and worker admission reject a stale generation. Local settings must explicitly select the current writer. Operators must drain in-flight transports before switching: the generation cannot undo an already sent request. `backout-plan --after <aware-ISO-time>` enumerates new business audit pointers and requires reconciliation if any exist. Backout restores a separate empty schema and activates that generation only after source/new-event reconciliation; it never claims to reverse external bank, message or physical facts or overwrite a nonempty database.

## References and verification limits

The implementation uses mature existing libraries rather than a custom SQLite VFS or snapshot engine: [SQLite Backup API](https://www.sqlite.org/backup.html), [SQLite WAL readonly constraints](https://www.sqlite.org/wal.html), [PostgreSQL 17 transaction isolation](https://www.postgresql.org/docs/17/transaction-iso.html) and [SQLAlchemy pool configuration](https://docs.sqlalchemy.org/en/20/core/pooling.html). These documents explain the selected mechanisms; actual project tests/trial evidence, not documentation claims, establish verification.

The Task 5 report records exact final head, test commands, isolated actual trial, repeat-zero controls and empty restore. Actual source coverage does not constitute production external account, cloud destination, multi-year scale or UI acceptance. Task 6 must consume this profile and normal endpoints and keep its synthetic E2E/benchmark data separate.
