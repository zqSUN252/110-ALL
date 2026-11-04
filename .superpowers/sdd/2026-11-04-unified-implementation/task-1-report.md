# Task 1 implementation report

Status: functional infrastructure delivered; no claim of full MCSTAR business acceptance.

## Implemented

Python 3.13 project with pinned direct dependencies and full `requirements.lock`; only project `.venv` used. Real PostgreSQL infrastructure models: User, Grant, WarehouseScope, Session, AuditEvent, Command, Attachment, AppSetting, Job and Outbox. Domain logistics/finance models are intentionally owned by later tasks under the clarified task scope, preserving full business scope.

FastAPI application factory, one-request transaction dependency, fresh database grants per request, Argon2 password hashing, hashed session/CSRF tokens, HttpOnly/SameSite cookie, admin user CRUD (soft delete), grants, settings allowlist, audit, private attachment upload/download, deterministic idempotency and explicit optimistic version checks. Command retries use PG advisory transaction locks. Decimal serialization preserves exact decimal strings. Initial-admin CLI has no public HTTP counterpart and refuses subsequent bootstrap.

Private attachments use SHA256 addressed immutable files, safe paths, byte verification and forced downloads. API owner spoofing and cross-user reading are denied; generic API uploads are self-owned only. Domain callers must enforce their own entity/warehouse ACL before calling the lower-level file service. Private storage ACL restricted to executing account; file bytes can remain privately unreferenced after transaction rollback and are not auto-deleted.

Alembic initial revision `eec45a507201` is explicit/frozen and creates only infrastructure. App startup does not call create_all. `tools/dev_runtime.py` created a separate PG17 cluster at ignored private/postgres17 with a randomly chosen free localhost port and random password in restricted ignored private/keys. Existing Windows service postgresql-x64-17 remained Running and was not controlled. No models, Docker/WAHA, external account collection or sends were started.

## Validation evidence

1. RED: first foundation test collection failed with ModuleNotFoundError for missing FastAPI, before implementation. Dependencies were then installed locally. Additional behavioral RED run showed 2 failures / 8 passes: default validation leaked rejected password input, and settings accepted secret keys. Both were corrected and rerun GREEN.
2. Full command: `$env:PYTHONPATH='backend'; .\.venv\Scripts\python.exe -m pytest backend/tests -q`.
3. Final suite: **12 passed**, one upstream Starlette/AnyIO deprecation warning. Actual PG, independently randomized schema per fixture; no SQLite, no skips on missing database.
4. Covered anonymous rejection; login/logout; low privilege rejection; grants effective next request; CSRF refusal; stale 409; same-key same-body replay and different-body 409; exactly one settings audit; owner-only attachment access; spoofed owner rejection; attachment corruption rejection; filename traversal sanitization; user create/update/deactivate; password not echoed; sensitive/runtime settings rejected; transaction failure rolls back business row/audit/command together; concurrent identical commands execute handler once; Decimal output stays string.
5. Migration downgrade to base, upgrade to head, and Alembic metadata drift check passed. CLI init-db, one-time admin creation, repeated bootstrap refusal, and HTTP login of CLI-created admin verified inside an isolated test schema. No development/public-schema administrator is seeded.
6. CLI init-db migrated the owned development DB. Standalone Uvicorn startup and localhost HTTP health/auth smoke were exercised. The first smoke client inherited a system proxy and timed out; rerun explicitly bypassed environment proxies for localhost.

## Interfaces and handoff

Exact imports, tables/fields, route authorization, commands, schema ownership and service conventions are in `docs/development/schema-contract.md`. Deployment boundary is `deploy/README.md`. Next tasks should add typed entities and revisions after eec45a507201, import models into app.models, and attach domain routers through create_app. Use get_db as the HTTP transaction owner; services never commit. Mutation services lock current row, check expected_version, write versioned update, audit and run_command within that transaction. Never store passwords/tokens in audit snapshots.

Requirement traceability: SYS-03 (#57/#100/#116/#347/#418/#465), SYS-04 (account persistence source set documented in schema-contract), SYS-05 (authoritative save/version source set documented there), SYS-07 (#145). This proves the infrastructure portions only. Complete domain permission matrix, production deployment, multiple real devices, multi-year performance and full backup/restore are not yet accepted. No production business data or customer details were invented or loaded.
