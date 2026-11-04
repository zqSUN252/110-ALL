# Task 3 fix round 2 — N1/N2

Baseline: `84e82cb5a30763d3a2171a3763e8a16ce2cfc3aa`; branch `codex/unified-implementation`. Scope is the two new findings in task-3-rereview-1.md and their affected paths. The eight original I1–I8 repairs are retained. No subagents, old services, external models, source-data mutations or service restart were used.

## N1 — current pricing authority

Added typed, server-controlled `Ticket.pricing_authority` (`quote`, `totals`, `pending`, `review`) and shared packing/pricing.py decisions for actual edits and previews, including replacements. Explicit currency totals or reviewed components supersede the current quote pointer; old immutable quote/component rows and command/revision history are retained. Metadata edits preserve manual totals even if a new default card exists. Confirmed manual unit-rate quotes still multiply actual PCS/KG/CBM quantities. No historical unit price is inferred from a total.

For total/review authority, quantity edits without a pricing choice fail with 409 pricing_decision_required. Explicit `repricing=retain_totals` requires a reason; `repricing=quote` deliberately selects the current default card, and an explicit manual rate selects a new manual quote. Unavailable explicit quoting fails atomically with 409 quote_unavailable. Existing financial source changes still require the complete adjustment preview/version/audit. Current `quote_id`/`quote` projections exclude historical-only quotes; migrated review rows expose `historical_quote_id` separately.

Fixed migration c11000300006 adds the constrained field, marks preexisting tickets review and leaves their totals, quote references, invoices, cash and allocations untouched. Review permits contact/destination metadata repair and actual arrival recording but does not silently recalculate existing AR or create new unreviewed allocation. Explicit reviewed components plus finance preview resolve the authority; legacy unknown component units remain unresolved until supplied. Downgrade with any ticket is refused. The prior c5 component guard is tested with quote-owned components so it remains exercised independently of c6's earlier ticket guard.

## N2 — old-source authorization versus new-link validity

`cargo.commands.native_change` independently authorizes the existing source with current cargo read/scope. Old void or wrong-warehouse relationships can be removed/corrected without qualifying as new assignments. The proposed non-NULL link, or changed identity fields, still validate cargo read, active state, warehouse and known identity/Party/transport. Preview/edit/replacement share this rule. Versioned commands retain previous source IDs in results/audit; successful replay still checks current original/current source access, without re-executing an assignment or rejecting it merely because that receipt was subsequently voided.

## Actual RED/GREEN chronology

All pytest commands use real isolated PostgreSQL schemas and frozen migrations, actual domain services and HTTP. Tests contain only synthetic identities, files and amounts. Run from D:/work/110-ALL with `$env:PYTHONPATH='backend'`; runner is `./.venv/Scripts/python.exe -m pytest`. No domain logic is mocked. Full node names below refer to backend/tests/test_logistics_review_2.py unless otherwise stated.

1. `backend/tests/test_logistics_review_2.py -q` at initial five-test version: **5 failed, 1 warning, 18.20s**. Behavioral RED: two 9000→6000 destination reversions (unpaid/paid), void-old unlink rejected, and old wrong-warehouse repair rejected via both edit/replacement.
2. Same file `-k n2 -q` after the authorization/validity split: **3 passed, 2 deselected, 1 warning, 9.57s**. This preceded the extra new-target parametrization.
3. `backend/tests/test_logistics_review_2.py::test_n1_totals_quantity_requires_explicit_semantic_choice_http -q` before N1 fix: **1 failed, 1 warning, 3.19s**. Actual PATCH returned 200 and reverted to obsolete 8000, instead of requiring an explicit quantity/pricing decision.
4. Same whole file after N1 source/migration implementation: **1 failed, 5 passed, 1 warning, 16.85s**. The remaining failure was a test error-envelope assertion (flat error versus actual nested error.code); production returned the intended 409. Corrected the assertion to match the existing API envelope.
5. `backend/tests/test_logistics_review_2.py backend/tests/test_packing.py backend/tests/test_logistics_review.py -q`: **1 failed, 55 passed, 1 warning, 172.69s**. All eleven new N1/N2 regressions passed, including actual c5→c6 migration with a paid legacy AR. The sole failure was the old c5 component downgrade test expecting its original message while the new c6 ticket guard fired first. Changed only that old test's synthetic fixture to quote-owned components, keeping its component-preservation assertion and directly reaching the c5 guard. Separate new c6 nonempty-ticket guard remains covered.

The eleven new cases cover paid/unpaid components→destination over HTTP; quantity refusal/retain-total/new confirmed unit rate; explicit default reprice and unavailable-card rollback; replacement with reviewed components; void-old unlink and original command replay; wrong-warehouse unlink/new-target repair via ordinary command and replacement with original-scope denial and later target-void replay; and real migration preserving paid legacy AR through metadata/arrival/review resolution.

Final focused/full-suite and actual migration results are recorded below; intermediate pass counts are not a substitute for final-source verification.

After continuation, command `backend/tests/test_logistics_review.py::test_review_migration_preserves_component_and_exclusion_evidence backend/tests/test_logistics_review_2.py::test_n1_migration_review_preserves_ar_and_allows_metadata_then_explicit_resolution backend/tests/test_cli_migrations.py::test_frozen_migration_round_trip -q` did not complete. Its only progress output was `EE`, without a final traceback or summary. One native `D:/APP/PostgreSQL/17/bin/pg_isready.exe -h 127.0.0.1 -p 24583 -t 3` under an outer7-second process limit returned **127.0.0.1:24583 - no response**, exit2. After verifying the exact owned pytest command, the agent stopped only its launcher PID45544; child18820 also exited, both were confirmed absent and tool session49271 closed exit1. This is an interrupted run, not a successful guard check or a diagnosed domain failure. No broad PG connection termination or service restart occurred. An earlier attempted probe referenced absent default Program Files/backend-private paths and never launched a connection; the actual controlled configuration is root/private/runtime.json. Configuration absence was not the cause claimed. Parent took over bounded read-only cluster diagnosis before any further test connections.

Parent subsequently reported the former project postmaster27928 absent, no listener on24583, and pg_ctl status exit3/no server running. Parent started only the already-exited project-owned root/private/postgres17 instance; reported automatic WAL recovery and ready at17:07:22, new matching postmaster45808/listener, and final native pg_isready exit0/accepting. The original process exit cause remains unknown. This implementer did not start/restart any service or change network configuration. The parent-provided recovery evidence is distinguished from the following directly executed tests.

6. Same four migration targets with `-q -x` after recovery: **4 passed, 1 warning, 12.33s**. Both old c5 guards, the new c6 nonempty-ticket guard, actual legacy paid-AR upgrade/resolution, and empty-schema base→head roundtrip/metadata equality passed.
7. Actual configured local schema upgrade/check/current, exit0:

```powershell
$env:PYTHONPATH='backend'
@'
from app.cli import migration_config
from app.settings import load_settings
from alembic import command
config=migration_config()
config.attributes['database_url']=load_settings().database_url
command.upgrade(config,'head')
command.check(config)
command.current(config)
'@ | ./.venv/Scripts/python.exe -
```

Output: **No new upgrade operations detected. c11000300006 (head)**. No DSN or credential was printed.

8. **Final business-source full suite:** `./.venv/Scripts/python.exe -m pytest backend/tests -q` completed **156 passed, 1 warning in 385.91s (0:06:25)**, exit0. The sole warning is the existing upstream Starlette anyio.abc.BlockingPortal deprecation. This run followed the isolated old-c5-fixture correction and all N1/N2 source changes; no business source or test changed afterward. It includes all original I1–I8 regressions, the eleven N1/N2 cases, and fixed migration roundtrip/guards. Final cached diff check also passed before source commit.

## Files and contracts

Owned changes: backend/app/api/logistics_inputs.py; backend/app/cargo/commands.py; backend/app/models/logistics.py; backend/app/packing/{allocation,imports,pricing,service}.py; fixed migration c11000300006_explicit_ticket_pricing_authority.py; backend/tests/test_logistics_review.py and test_logistics_review_2.py; docs/development/logistics-contract.md; this report and the original Task3 report appendix. No dependency changes. The logistics contract defines current/historical projections, explicit choices, migration review behavior, financial resolution, native repair and replay for Task 4/5/6.

Static checks run after business-source changes: `./.venv/Scripts/python.exe -m compileall -q backend/app backend/tests` exit0; `./.venv/Scripts/python.exe -m pip check` reports **No broken requirements found**; `git diff --check` exit0 (only working-copy LF/CRLF notices).

## Evidence boundary

No new inspection of original ZIP/old110 was needed for these two scoped defects. Prior original-format evidence and unresolved historical units/source fees remain in the Task3/fix1 reports. This round does not claim production customer/tariff truth, real UI/templates, physical release, notifications, models, production backup/restore or external acceptance. Parent-owned implementation-progress/reference/conflict documentation and its newly updated docs/acceptance/文档校验报告.json remain outside this commit. The parent separately reported 36 read-only source/document checks passed and unchanged original-source fingerprints; those are parent evidence, not additional tests executed by this implementer.

Implementation/source/test/contract commit: **`b398fb01feb682a530cb86e24c1801ae78fdff99`**, `fix(logistics): retain explicit pricing authority and authorize native repairs`. The report and original-report appendix are a following documentation-only commit so this report can identify the exact immutable implementation SHA. Final report commit is supplied in the handoff. Self-review stayed within N1/N2 changed paths and affected migrations/projections; the eight earlier repairs were verified by the final suite rather than reopened as new scope. Final status: **DONE**, with the operational interruption/unknown PG exit cause and external acceptance limits explicitly retained above; no remaining N1/N2 implementation blocker is known.
