# Foundation schema and API contract

Implemented Task 1 infrastructure, 2026-11-04. This is not acceptance of logistics, finance, migration, sending or customer portal workflows. Later tasks add their own typed SQLAlchemy models and Alembic revisions; no generic business JSON store is provided.

## Runtime and imports

- Python 3.13; pinned transitive dependencies in `backend/requirements.lock`. PostgreSQL 17 was selected because the installed, independently runnable local binary is version 17. Design PostgreSQL 18 was an engineering default, not a business constraint.
- `app.settings.Settings(database_url, private_root, secure_cookies=True, business_timezone='Asia/Shanghai', ...)`; all collection/summaries/model_calls/sending flags default false, main_account_read_only defaults true. Secrets use environment or ignored `private/runtime.json`. Never print Settings/DSNs with credentials.
- `app.db.Base`, `make_engine(url, **kwargs)`, `session_factory(engine)`. PostgreSQL only; no SQLite fallback. Engine hides SQL parameters. Production startup does not create tables.
- `app.main.create_app(settings=None)` is an Uvicorn factory. App state: `settings`, `engine`, `session_factory`. The private storage root is restricted to the operating account.
- `app.api.dependencies.Database/get_actor/mutate`. Every route or transitive dependency takes `db: Database` (an Annotated SQLAlchemy Session with `Depends(get_db, scope='function')`). This is the sole cached request transaction, attached Settings at `db.info['settings']`, and it finishes COMMIT **before any HTTP status/body/cookie is sent**. Never use bare `Depends(get_db)`, which defaults to a dangerously late request-scoped commit. Services must NOT commit or nest `db.begin()` under this dependency. Non-HTTP callers own `with db.begin()` and should attach Settings explicitly when storing files.
- `Actor(id: str, capabilities: set[str], warehouse_ids: set[str])`; `require(actor, capability, warehouse_id=None)` checks both permission and warehouse scope. Explicit administrator `*` bypasses both; ordinary capability names are exact matches.
- `check_version(entity, expected)` rejects missing/stale version with 409. Load mutable entities with `SELECT ... FOR UPDATE` before checking/updating; increment version explicitly once per successful mutation.
- `audit(db, actor, action, entity_type, entity_id, before, after)` records same-transaction snapshots. Never pass password/hash/token/secret fields to snapshots.
- `run_command(db, actor, command, key, payload, handler)` calls a zero-argument handler, returns its dict result, never commits. Actor+command+key identifies the command; transaction advisory locks serialize concurrent retries. Same body returns stored result; different canonical body returns 409. Results and snapshots encode Decimal as strings, dates as ISO. A failing handler rolls back with its outer transaction. Request comparison includes all secrets but persists only a cost-hashed verifier: `argon2id-sha256-v1:` + salted Argon2id of the canonical JSON SHA256. The fast intermediate digest is never persisted by current code. Verification uses Argon2 (including password create/reset), so DB snapshots do not expose a cheap password oracle. This chooses the review's costly-verifier option: no separate HMAC key or key rotation is introduced; salt, algorithm version and work factors are encoded in the verifier. Future versions require an explicit migration; unknown/legacy unupgraded versions fail closed.

## Fixed initial migration

Revision `eec45a507201` creates only the following infrastructure tables. It contains explicit operations, not live metadata.create_all. Follow-up head `a028a4baee91` changes command request_hash to TEXT and cost-hashes existing SHA256 fingerprints without needing original passwords or losing retries. Nonempty command history deliberately prevents downgrade to the weak format; no commands are deleted. Existing historical backups cannot be retroactively hardened by a schema migration and remain controlled sensitive material. All rows inherit `id VARCHAR(36)` generated UUID, `version INTEGER` default 1 in ORM, `created_at` and `updated_at TIMESTAMPTZ` UTC. Raw SQL writers must supply these fields; defaults are Python-side.

| Export / table | Additional fields and constraints |
| --- | --- |
| `User` / users | username VARCHAR(120) unique; password_hash TEXT Argon2; active BOOLEAN |
| `Grant` / grants | user_id FK users; capability VARCHAR(120); unique(user_id,capability) |
| `WarehouseScope` / warehouse_scopes | user_id FK users; warehouse_id VARCHAR(36); unique(user_id,warehouse_id). Warehouse FK added by the warehouse task when its table exists. |
| `Session` / sessions | user_id FK users; token_hash CHAR-sized VARCHAR(64) unique; csrf_hash VARCHAR(64); expires_at TIMESTAMPTZ; revoked_at nullable TIMESTAMPTZ |
| `AuditEvent` / audit_events | actor_id nullable FK users; action,entity_type,entity_id VARCHAR(120); before,after nullable JSONB event snapshots |
| `Command` / commands | actor_id FK users; command VARCHAR(120); key VARCHAR(200); request_hash TEXT versioned costly verifier; result JSONB; unique(actor_id,command,key) |
| `Attachment` / attachments | sha256 VARCHAR(64); object_path VARCHAR(160); filename VARCHAR(255); media_type VARCHAR(120); size_bytes INTEGER; created_by FK users; owner_type VARCHAR(80); owner_id VARCHAR(120) |
| `AppSetting` / app_settings | key VARCHAR(120) unique; value JSONB configuration |
| `Job` / jobs | kind VARCHAR(120); status VARCHAR(32), default pending; payload JSONB; attempts INTEGER; available_at TIMESTAMPTZ; locked_at nullable TIMESTAMPTZ; error_code nullable VARCHAR(120); idempotency_key VARCHAR(200) unique |
| `Outbox` / outbox | topic VARCHAR(120); payload JSONB; status VARCHAR(32), default pending; delivered_at nullable TIMESTAMPTZ; idempotency_key VARCHAR(200) unique |

Job/outbox are persistence foundations only: no worker or external send is started. Domain records/relationships/amounts belong in dedicated typed tables, not these payload columns.

## HTTP routes

All mutation inputs reject unknown fields. Authentication cookie `mcstar_session` is HttpOnly/SameSite Strict; production Secure defaults true. Session and CSRF tokens are stored only as SHA256 hashes. `/api/me` and every authorized request re-read active user/grants/scopes. API grants changes therefore apply next request. Password reset revokes prior sessions. JSON response validation does not echo rejected input secrets.

| Route | Authorization and behavior |
| --- | --- |
| GET /health | Database SELECT 1; returns only status |
| POST /api/auth/login | JSON username/password; returns user and csrf_token; sets cookie. Incorrect credentials 401; correct password on inactive account 403. |
| POST /api/auth/logout | Auth + X-CSRF-Token; revokes current session and clears cookie |
| GET /api/me | Auth; identity, capabilities, warehouse_ids |
| GET /api/admin/users | users.read |
| POST /api/admin/users | users.manage + CSRF + Idempotency-Key; username/password (minimum 12 chars) |
| PATCH /api/admin/users/{id} | users.manage + CSRF + Idempotency-Key; expected_version and optional username/active/password |
| DELETE /api/admin/users/{id} | Same authorization; JSON expected_version; soft deactivation preserves audit identities; cannot deactivate self |
| PUT /api/admin/users/{id}/grants | Same authorization; expected_version, capabilities[], warehouse_ids[]; cannot remove own * |
| GET /api/admin/settings | settings.read; business settings only |
| PUT /api/admin/settings/{key} | settings.manage + CSRF + Idempotency-Key; expected_version (0 creates), value object; allowlist billing/display/business_timezone/company_profile. Secrets and runtime switches rejected. No unknown pricing/contact defaults supplied. |
| GET /api/admin/audit?limit=100 | audit.read; bounded maximum 500 latest entries |
| POST /api/attachments | attachments.write + CSRF + Idempotency-Key; multipart file, owner_type=user, owner_id=current actor; returns metadata only |
| GET /api/attachments/{id} | attachments.read plus current owner or *; checksum verified; forced download, no-store, nosniff |

Failure body: `{"error":{"code":"...","message":"...","details":null}}` for DomainError; validation response contains field locations/types without input values. No public bootstrap or public static private files.

`app.attachments.put_file(db, actor, content, filename, media_type, owner_type, owner_id)` writes a uniquely named private temporary object, flushes/fsyncs/verifies its bytes, and publishes through a same-filesystem atomic hard link that cannot overwrite a concurrent winner. A matching winner is verified and reused. Unsupported atomic-link filesystems fail safely. Only that invocation's temporary name is removed on error/success; published originals and unrelated temporaries are never deleted. `read_file(settings, attachment)` validates object path and SHA256 and returns bytes. These lower-level functions require domain callers to check owner ACLs; the generic HTTP route only supports self-owned user uploads. Domain attachments must use that domain's authorization service, including warehouse/entity scope. Never expose read_file directly without authorization. Published files survive DB rollback as private unreferenced immutable objects; no automatic deletion runs. Independent attachment metadata is preserved even if bytes match.

`app.private_storage.protect_private_root(path)` is shared by the app and runtime setup. On Windows it rebuilds and verifies each existing directory/file ACL with **only the executing account** having FullControl, discarding previous explicit as well as inherited grants. New children inherit that approved principal; reparse points or unenforceable permissions fail startup. The helper runs before runtime secret writes. The Windows policy is verified on this host using a pre-existing isolated root, child directory and file explicitly granting Everyone read. System/elevated administrative override is outside discretionary ACL guarantees; no other account is silently approved.

## Commands from repository root (PowerShell)

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
.\.venv\Scripts\python.exe tools/dev_runtime.py start
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m app.cli init-db
.\.venv\Scripts\python.exe -m app.cli create-admin --username YOUR_ADMIN_NAME
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

CLI administrator creation prompts for a password or consumes MCSTAR_ADMIN_PASSWORD supplied privately by the calling process. It refuses once an administrator exists and never overwrites an account. Tests exercise it in isolated schemas. No development account/password is seeded into public schema.

`tools/dev_runtime.py start` initializes/reuses only `private/postgres17`, binds localhost on a privately recorded free port, creates database mcstar, and persists random credentials under restricted ignored private/keys. It never controls `postgresql-x64-17`. `status` inspects only its own data directory. No Docker, WAHA, model or messaging process is started.

Tests use actual PG with unique `test_<UUID>` schemas, apply frozen migrations, and drop only their own schema afterwards. Missing DB configuration is a test failure, not a skip. Alembic autogeneration/check should run against a migrated database and the complete current model registry; subsequent domain revisions must have `down_revision='a028a4baee91'` or the then-current head.

## Requirement evidence boundaries

- SYS-03 (#57, #100, #116, #347, #418, #465): request-level permission and refresh foundation verified; domain role matrices remain domain-task acceptance.
- SYS-04 (#82, #84, #148, #151, #173, #177, #257, #319, #346, #350, #354, #446, #498, #513): persistent accounts, independent sessions and controlled CRUD foundation; full publish/sync workflow remains later acceptance.
- SYS-05 (#59, #60, #65, #67, #69, #72, #115, #225, #226, #324, #330, #358, #360, #432, #530): real PG authoritative writes, stale conflicts and concurrent idempotency verified; two real devices/production connectivity not yet accepted.
- SYS-07 (#145): audit transaction and rollback foundation verified; business-action audits remain domain-task acceptance.
- SYS-09 and SYS-13: local runtime/migration verified; multi-year capacity, complete production restoration and deployment remain unverified.

## Task6 integration extension (current local head)

The foundation evidence above is historical. Current local head is `g11000600002`, descending from reviewed Task5 `f11000500006`. `g11000600001` adds the versioned `announcements` table with controlled Chinese/English fields and active state; the ordinary server command wrapper owns permissions, idempotency and transactional audit. `g11000600002` adds ticket ordering indexes `(created_at DESC,id)` and `(warehouse_id,created_at DESC,id)`; the Ticket model declares the same indexes so Alembic check has no artificial removal diff. There are108 application tables. No currency, bank identity, cash allocation, release or physical stock semantics are changed by these revisions.

The supplied package snapshot has actually been upgraded to this head and independently backed up/restored with complete table/hash checks. Native review records remain separate from isolated synthetic tests and performance schemas. Current local UI/HTTP, scale, recovery and package results are recorded in `docs/acceptance/运行验证.json`; they do not certify the current production source, PG18, external delivery or production deployment.
