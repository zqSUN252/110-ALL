# Local backend runtime

Follow `docs/development/schema-contract.md` for exact setup, CLI and API contracts. Only an independently owned localhost PostgreSQL 17 cluster is started. Existing Windows PostgreSQL service and all original project services remain untouched.

Production must supply `MCSTAR_DATABASE_URL` and a private storage root available only to the service identity, run `python -m app.cli init-db` before startup, and terminate HTTPS in a controlled proxy. Secure cookies default on; only the generated localhost development configuration turns them off. Do not publish `private/`, logs, backups, environment files or credentials, or mount them as static files.

Run the process with a dedicated OS account that owns the private root and has only the required database privileges. Deployment secrets must be injected by the deployment's controlled store; administrator passwords are entered interactively, never passed as CLI arguments. Start Uvicorn with `app.main:create_app --factory`.

No production deployment, external-domain verification, capacity benchmark, backup/restore acceptance, collection activation, model use or message sending has been performed by Task 1.
