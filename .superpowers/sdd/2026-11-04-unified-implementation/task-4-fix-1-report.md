# Task 4 fix round 1 / 5

Date: 2026-11-04. Base: `ca36dfadcaa4156da4a6e5b3071b51144d0dc48b`. Implementation commit: `38fe82ca5f2f7137438f9dc3691a18e2ea64330d`.

Scope: the five Important findings I1–I5 in `task-4-review.md`, plus directly exposed behavior in their tests. The source fix is locally verified as recorded below; independent limited rereview remains the parent's next gate. This does not claim acceptance of the entire Task 4 external workflow or all 146 project items.

## Findings → implementation and evidence

| Finding / requirement | Implemented correction | Real regression evidence |
| --- | --- | --- |
| I1 — DOC-06 #322/#323; DOC-07 #261/#262/#335; DOC-10 #28/#335/#355; PORTAL-02 #303/#381/#386/#518 | `exports.members` restores order from immutable snapshot `(source_type,id)` rows. It verifies exact saved membership and rejects missing/extra/duplicate identities. Worker authorization, execute, publish and download all use that same resolver. No random UUID order or snapshot comparison bypass. Existing queued jobs are compatible; no new schema is needed. | Two real tickets have deliberately reversed member UUID order. Real worker completes; HTTP downloads actual XLSX; Master rows remain SYN1 then SYN-SECOND. A real source edit then makes old download return 409 and a queued job fail `stale_export`. |
| I2 — RATE-05 #185/#207; NOT-02 #179/#202/#203/#207/#527; NOT-04 #117/#155/#428/#516/#532/#533 | Rates/receipt snapshots store selection date, mode, source conditions and all selected effective PriceCard records/versions. Text uses this exact saved selection. Image/copy rebuild it and compare with all other source facts. Empty-to-present, conflict additions, disabled cards and price changes invalidate old content. Existing confirmed QuoteSnapshot/invoice authority is unchanged. Pre-fix generations lacking price-dependency proof require regeneration; copied history remains. | Eight real PG+HTTP combinations: rates/receipt × price10→20, no card→card, new conflicting card, disable. Old image and copy return 409; fresh generations return current20 or explicit no-card text and support real image/copy. Adding an unrelated port card does not stale a known-port notice. The existing source quote link stays unchanged. |
| I3 — NOT-04 #117/#155/#428/#516/#532/#533; NOT-05 #553/#554/#555/#557 | Added GET `/api/receipts/{id}/notification-marks`, requiring `documents.read` on the actual receipt warehouse. Generalized the existing marks member query by source type, preserving ticket behavior and ordering. Both return copy ID/version/kind/actor/time/display metadata. Existing authorized cancel command preserves original events; active marker disappears from fresh reads. | Native receipt render/copy → new HTTP read returns saved marker → cancel using returned ID/version → fresh client reads empty active marks. An employee granted documents.read/notify only in another warehouse receives 403. |
| I4 — DOC-03 #306/#307/#310/#318/#320/#321/#326; DOC-04 #535; DOC-08 #263; #568 | XLSX pure currency totals are explicitly `USD charges` / `NGN charges`, including Totals sheet. Actual Unit Charges retains component/currency/unit/unit price/amount. Master and port sheets share the same writer, customer sheets retain real Freight/Clearing lines. Legacy API currency-total keys remain compatible. Shared unit display avoids scientific notation. | Actual workbook readback for NGN/PCS freight6000 alone, and freight6000 plus independent SEA clearing900: Master and port show currency totals6000/6900, distinct real component lines; customer sheet shows matching component amounts and totals. No fixed Clearing NGN header remains. Totals currency values also read back. |
| I5 — NOT-01 #510/#512; REL-04 #342/#343/#355/#450; shared finance HALF_UP/rounding contract | SEA conversion invokes `finance.settle` instead of Decimal's default HALF_EVEN. With an existing invoice, charge lines consume the shared projection's billed-arrival currency/components; this fixes partial-arrival REMOVE ON HOLD without changing its original goods-detail quantities. Existing invoice rounding adjustment remains separate and is never silently absorbed into a fee. | Arrival and REMOVE ON HOLD each verify USD.03×1.5 = NGN.05, no adjustment. A second matrix uses actual two partial batches: billedUSD.66→NGN.99 plus existing adjustment+.01 = formal invoice1.00. Real text values are parsed and compared to the shared invoice and settle function. |

## TDD commands and actual outputs

Environment for every pytest command:

```powershell
$env:PYTHONUTF8='1'
$env:MCSTAR_CONFIG='D:\work\110-ALL\private\runtime.json'
$env:PYTHONPATH='backend'
.venv/Scripts/python.exe -m pytest <arguments below>
```

Only synthetic data/templates/files and isolated PostgreSQL schemas were used. UTF-8 output avoids the review probe's CP936 currency-symbol print issue. No DSN, password or real business data is reproduced.

1. Initial RED command: `backend/tests/test_documents_review.py -q --tb=short`.

   Actual result: **13 failed, 1 passed, 1 warning in 30.83s**. I1 returned `failed / stale_export` despite unchanged sources. I3 returned 404 instead of the requested marker response. Both I4 cases found the incorrect Clearing NGN header. I5 observed `Decimal('0.04') != Decimal('0.05')`. Eight I2 cases initially stopped in a test fixture because received_on was passed twice; those are explicitly **not** behavioral RED evidence.

2. After correcting only that fixture, I2 RED command: `backend/tests/test_documents_review.py -k i2 -q --tb=short`.

   Actual result: **8 failed, 6 deselected, 1 warning in 22.03s**. All eight cases reached actual business responses: `[image.status, copy.status] == [200,200]` instead of `[409,409]` after the real relevant price-card mutation.

3. First implementation focused command: `backend/tests/test_documents_review.py -q --tb=short`.

   Actual result: **2 failed, 12 passed, 1 warning in 38.92s**. I1/I2/I3/I5 passed. The remaining I4 assertions expected plain `2000/PCS`; actual Decimal display retained `2000.00/PCS` and `9E+2/CBM`. This formatting mismatch was fixed with the existing normal fixed-decimal unit formatter shared by formal customer lines and table cells; currency/component classification was already corrected.

4. Additional I5 partial-arrival/remove-hold RED: `backend/tests/test_documents_review.py -k 'i5 and remove_hold' -q --tb=short`.

   Actual result: **1 failed, 1 passed, 14 deselected, 1 warning in 6.47s**. Existing-adjustment removal text converted the full original USD1 to NGN1.50 while the existing partial invoice had charge_usd.66 and authoritative converted.99. Charge lines now reuse that existing billed-arrival projection. This changes no physical facts, amounts, invoice policy or stored data.

5. Final focused GREEN after these fixes:

```text
pytest backend/tests/test_documents_review.py backend/tests/test_documents.py backend/tests/test_tracking.py -q --tb=short
41 passed, 1 warning in 119.58s (0:01:59)
Process exit code: 0
```

6. Source/test freeze, then one final full suite for this fix round:

```text
pytest backend/tests -q --tb=short
197 passed, 1 warning in 520.22s (0:08:40)
Process exit code: 0
```

No earlier complete Task 4 result was substituted for this fix's final result. The warning is the existing Starlette use of deprecated anyio.abc.BlockingPortal, not a skip or new domain failure.

## Schema, files, boundaries

No model, migration or dependencies changed in this round. Existing immutable snapshots already carry a trustworthy source sequence, and event snapshots are the proper place for price-selection proof. Runtime checks after source freeze returned:

```text
command.current: d11000400003 (head)
command.check: No new upgrade operations detected.
```

The final full suite also includes the existing real empty-schema downgrade/upgrade/check and nonempty preservation tests; no history is removed to bypass migration guards.

Owned implementation files: `backend/app/api/documents.py`, `backend/app/documents/{exports,notifications,projection,rendering}.py`, new `backend/tests/test_documents_review.py`, and `docs/development/documents-contract.md`. Parent-owned `docs/requirements/冲突与待确认事项.md`, `docs/development/implementation-progress.md` and `docs/references/成熟实现与参考依据.md` are excluded from staging. The original report receives a fix1 addendum; this report is a separate evidence commit identifying the exact source commit.

Self-review checked all five corrections against their actual source paths and the limited regression suite, including unchanged privacy/current-source checks, literal spreadsheet behavior, warehouse coverage, ordinary destination roles, phone hold persistence, independent cash/credit/copy/pickup facts and unknown aggregates. No subagent/reviewer was spawned. Source readback and tests do not replace the parent's independent limited rereview.

Remaining external boundaries are unchanged: missing original templates/screenshots/font-size interpretation/hero photos; real provider delivery; real 微信/WhatsApp paste; physical 76×130mm printing; full React interaction and production rollout. No real messages, paid model, provider, existing WAHA, old services or network settings were used or changed.
