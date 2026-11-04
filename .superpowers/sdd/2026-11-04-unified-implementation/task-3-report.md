# Task 3 implementation report

Status: DONE_WITH_CONCERNS — local warehouse/F6/shipment/packing/pricing/master-data backend implemented and checked; production source completeness, real UI/template/export appearance, live models and downstream physical-release/notification acceptance are not claimed.

Branch: `codex/unified-implementation`; starting commit `2877446c4054462e0fe1df79db1593139a885e69`. No implementation/review subagents were spawned. Parent-authored changes to implementation-progress and conflict-resolution documents are excluded from this commit.

## Implemented scope and files

- `backend/app/models/logistics.py`, registry and four frozen `c11000300001`—`c11000300004` migrations: native receipts, versioned specifications, stock events, dispatch batches, reviews, real shared masters/phone history, rate/quote snapshots, shipments/documents/tickets, original source rows/revisions, arrivals and immutable settlement proof.
- `backend/app/cargo/{commands,service,translation}.py`: consistent guard/key/row-lock order, current action/read and original-result replay scope checks, 20+ specs, exact units, nonnegative remaining stock, partial multi-receipt dispatch, repack/merge/void/recover/review, photos and optional queued model candidate.
- `backend/app/warehouses/{masters,analytics,exports}.py`: shared Party/Warehouse edit/list/detail, categories/phone provenance/WhatsApp deeplink, native previous-day analysis and staff drilldown, visible/queued-all Excel datasets.
- `backend/app/pricing/service.py`, `backend/app/shipments/service.py`: date/dimension-specific quotes, explicit manual units and FX, missing-price state, stable clearing AP upsert/reviewed correction.
- `backend/app/packing/{parser,imports,service}.py`: read-only originals, preview/select/commit, source-proven row mappings, independent documents, stable-ticket split, per-ticket/document business void, previewed document recovery, original files/attachments, exact partial arrival and related AR orchestration.
- `backend/app/finance/rounding.py` and narrow shared finance/model/export/authorization changes: source-validated typed settlement adjustment; no arbitrary public rounding input; deduplicated invoice-component Excel sheet.
- `backend/app/api/logistics.py`, `logistics_inputs.py`, routing, explicit worker CLI additions; identity API validates warehouse scopes, now backed by a real FK. Foundation scope fixture now uses a real synthetic Warehouse without weakening its assertions.
- `backend/tests/test_logistics.py`, `test_packing.py`; `tools/check_original_packing.py`; exact downstream contract `docs/development/logistics-contract.md`.

The contract maps implemented behaviors to WH/F6/SHIP/PL/RATE/PARTY requirement IDs and original prompt numbers. Source data, raw prompts, passwords, original customer rows and embedded original workbooks were not committed or printed. Original `D:\work\110`/MCSTAR ZIP were not edited; no old service, WAHA, Docker, account, paid model, collection, historical replay or message send was started.

## RED → GREEN evidence

All database behavior tests use real PostgreSQL test schemas and actual frozen migrations; HTTP tests use the actual FastAPI route/transaction stack. Files are real synthetic XLSX opened with openpyxl. Only the external model HTTP transport is faked, while its persistent queue, concurrent manual edit and accept/version behavior remain real.

Commands use `$env:PYTHONPATH='backend'; ./.venv/Scripts/python.exe -m pytest ... -q` from repository root.

| Regression | Observed RED | Subsequent actual GREEN |
| --- | --- | --- |
| HTTP receipt with 20 specs | `test_receipt_http_twenty_specs_unique_identity_and_review`: POST `/api/receipts` returned404; 1 failed | Included in first 7 domain tests, then 11 and 28 focused tests, and full suites |
| Original-format preview | `test_preview_http_preserves_merged_rows_total_terminator_and_gross`: preview route404; 1 failed | 3 repeated source rows survive merged receipt, TOTAL in second column, NET not substituted, USD/NGN rates extracted |
| Masters/translation/merge | Named 3 tests failed for missing master PATCH/translation route and merge command | All3 included in 11-test GREEN |
| Per-batch rounding | `test_three_batches_round_only_each_batch_then_last_remainder`: second invoice1005.00 instead of expected990.00 | Same test1 passed after per-batch component allocation and last remainder |
| Scope editor | Unknown warehouse scope was accepted200 instead of expected422 | Scope rejection + concurrent dispatch + fake-transport stale translation + real foundation scope test:4 passed |
| Real-format XFD | Synthetic original header/style reproduction failed `file_size` due formatting-only XFD | Same test1 passed after nonempty-bound parsing; all11 actual originals parse structurally |
| F6 correction | Explicit corrected KG11 remained10 | Same test1 passed; raw original10 retained and StockEvent remains empty |
| Exact Mark OR confirmed phone grouping | Same confirmed phone/different Marks incorrectly produced2 groups | Same test1 passed with stable members/conflict flags |
| Explicit settlement rounding | Three parameterized USD/NGN/mixed tests failed missing/different rounded invoice components | Those3 plus paid-source replacement:4 passed; immutable settlement evidence and current adjustment verified |
| Missing imported quantities | Pure `ticket_values` rejected unknown pieces instead of preservingNULL | Same test1 passed; PG import of missing gross weight remains pending and arrival refuses invented0 |
| Historical AR | New partial arrival shrank known historical1500 to495 | Same test1 passed after preserving historical/manual AR outside new automatic eligibility |
| Nonempty downgrade | Native receipt downgrade did not raise | Guard + empty-schema migration round trip:2 passed |
| Read-capability replay + single-ticket void | Replay after domain read revocation did not raise; single-ticket void-preview404 | Same2 tests passed; paid cash credited without voiding sibling ticket/document |
| Final unknown-component gate | Unknown SEA clearingNULL still produced an AR | LiteralNULL blocks new AR; explicit0 later creates correctAR; known pricing-rule zeros remain valid |
| Historical unknown decomposition | Following the final gate, recording actual arrival against a known historicalAR with unknown clearing decomposition raised `existing_invoice_unpriced` | Early historical-basis preservation keeps original1500/due date and records actual arrival |

The initial complete suite after most implementation was **113 passed, 1 warning, 250.05s**. It preceded the nonempty downgrade guard and final permission/single-ticket routes. The guard was separately covered by the2 migration tests. After the latter2 fixes, a complete suite actually ran: **116 passed, 1 warning, 265.26s**.

The final NULL-component/historical-decomposition gate was added **after** that116-test full suite. Its final focused command covers the changed branches and positive/negative neighbors:

```powershell
$env:PYTHONPATH='backend'
./.venv/Scripts/python.exe -m pytest `
  backend/tests/test_packing.py::test_unknown_clearing_component_does_not_become_zero_ar `
  backend/tests/test_packing.py::test_existing_historical_ar_is_not_shrunk_by_new_partial_arrival_rule `
  backend/tests/test_packing.py::test_arrival_settlement_rounding_is_explicit_and_control_total_conserved `
  backend/tests/test_packing.py::test_manual_pcs_ticket_reprice_updates_paid_ar_and_sea_air_mode_rejected `
  backend/tests/test_packing.py::test_partial_air_arrivals_exact_last_remainder_one_ar `
  backend/tests/test_packing.py::test_replacement_reorder_keeps_paid_identity_and_readonly_originals -q
```

Final focused result: **8 passed, 1 warning, 22.26s**. This ran after the final gate and includes all three parameterized settlement modes. Do not interpret the116-test suite as a rerun after the final gate. The sole repeated warning is the preexisting upstream Starlette `anyio.abc.BlockingPortal` deprecation.

## Database, dependency and working-tree checks

- Actual `command.upgrade(config,'head'); command.check(config); command.current(config)` returned `No new upgrade operations detected.` and `c11000300004 (head)`. The final read-only metadata check repeated that exact result.
- `python -m compileall -q backend/app`: exit0. `python -m pip check`: `No broken requirements found.`
- `git diff --check`: exit0; only ordinary repository LF→CRLF notices, no whitespace errors. No dependency addition or lock change required.
- `c11000300001` refuses a nonempty logistics/quote/master downgrade; `c11000300003` refuses dropping settlement evidence or nonzero invoice rounding; `c11000300004` refuses converting unknown source values to mandatory invented quantities. Empty owned test schemas still downgrade to base and upgrade to head.
- Existing invalid warehouse-scope IDs cause migration/API failure rather than fake Warehouse creation. Real source gaps remain migration review items.

## Read-only real original-file evidence

`tools/check_original_packing.py` reads the supplied ZIP in place, recursively decodes embedded originals only in memory, deduplicates identical file bytes for the **format check only**, and emits structural counts. It does not deduplicate business rows or write original files. Known embedded objects use `{name,data}`; there were11 unique originals across snapshots.

The first read-only attempt exposed formatting dimensions extending to XFD/16,384 columns and more than40 sheets in some originals. This was not real16,384-column cargo. After the bounded nonempty-cell fix, the final run returned:

| Original index (no client filename) | Recognized sheets | Retained source rows | Candidate cargo observations | USD values | NGN values | Ambiguous clearing basis | Unknown destination |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 30 | 1009 | 258 | 192 | 64 | 66 | 129 |
| 2 | 39 | 1166 | 190 | 126 | 62 | 64 | 190 |
| 3 | 19 | 585 | 109 | 80 | 27 | 53 | 55 |
| 4 | 1 | 30 | 5 | 5 | 5 | 0 | 5 |
| 5 | 33 | 933 | 108 | 72 | 36 | 36 | 36 |
| 6 | 56 | 1713 | 312 | 230 | 76 | 149 | 149 |
| 7 | 36 | 1084 | 184 | 139 | 46 | 92 | 92 |
| 8 | 43 | 1237 | 164 | 108 | 54 | 54 | 57 |
| 9 | 43 | 1227 | 151 | 99 | 50 | 50 | 51 |
| 10 | 34 | 996 | 146 | 102 | 41 | 60 | 59 |
| 11 | 17 | 514 | 89 | 26 | 25 | 37 | 89 |
| Total | 351 | 10494 | 1716 | 1179 | 486 | 661 | 912 |

All11 had zero missing-header sheet issues. The inspected originals supplied no recognized gross-weight values (0 for each); no NET weight or fabricated0 was substituted. Synthetic tests explicitly cover a supplied gross and separate NET column. Other final issue occurrences:16 invalid pieces,16 invalid CBM,9 freight quantities missing,2 clearing quantities missing,1 invalid clearing numeric value. An observation can have multiple issues. These counts are not counts of accepted shipments/customers/transactions; source FULL/subdocuments are deliberately not globally deduplicated. Every original candidate is reviewable and can be corrected/excluded with a reason.

The actual `CLEARING CHARGE PER CBM TOTAL` label does not unambiguously say whether the cell is a unit rate or total. The parser preserves its raw number and requests a basis override. **Readable original files are not automatically confirmed fees, inventory or production migration.** No native stock was synthesized from these shipped-document exports.

## Hand-checked conservation cases

1. 20 specification rows, each100×50×20CM,2pieces,7.5KG total per row:40PCS,4.000000CBM,150.000000KG. Dispatch15PCS/1CBM/50KG leaves25PCS/3CBM/100KG. A26-piece follow-up is rejected. Concurrent30-piece commands from the same version have one winner; balance10PCS/1CBM/50KG.
2. Two whole available synthetic receipts40PCS/4CBM/150KG each merge to80PCS/8CBM/300KG; source balance becomes0, original source/spec rows persist. F6 link/correction creates no second inbound event.
3. NGN2000/PCS×3=6000NGN, no USD multiplier; destination changes retain the manual PCS/currency snapshot. Native receipt40PCS gives80000NGN under the same explicit unit rate.
4. Original USD1, FX1500 and three equal arrival masses: USD .33/.33/.34; AR495/990/1500 cumulative. At FX1.5: settlement .50/.50/.50, cumulativeAR .50/1.00/1.50, explicit rounding .00/.01/.00. Original total never becomes1.51.
5. Native NGN1 splits .33/.33/.34, total1.00. Mixed USD1+NGN1 at FX1.5 settles .83/.83/.84, total2.50. These are allocation remainders, not new fees. Snapshot records bind real ticket/arrival/invoice versions; forged999 rounding proof and arbitrary HTTP rounding field are rejected.
6. Pay NGN1 after batch2 at FX1.5: after batch3 real cash remains1, full invoice1.50, due.50. Excel retains a separate .01 rounding cell when exporting after batch2, without multiplying the payment cash by invoice count.
7. Paid original1500 repriced by explicit source replacement to1200: same ticket/invoice, original cash1500, current allocation1200, available300; due date remains2026-11-15; actual arrival quantities remain3KG. Split moves these exact identities, not another payment. Business void credits the actual allocation back to the same payment; explicit recovery restores the bill without silently reapplying that cash.
8. Unknown SEA clearingNULL remains NULL and prevents new automaticAR despite actual arrival. Explicit reviewed clearing0 subsequently creates the1500AR from the known USD/FX. Historical1500AR with unknown decomposition is preserved at its original2026-01-15 due date during a new partial arrival.

## Temporary local connection incident

One two-test process and diagnostic connection stopped progressing at new connection establishment. A timed traceback showed psycopg `_connect_gen`/`wait_conn`; direct localhost TCP creation also failed to return promptly. No `pg_stat_activity` result was obtained, so this is **not asserted to have been a database lock**. The parent observed a separate HTTP helper waiting too; the user reported no intentional network switch. Cause remains unconfirmed.

Only positively identified agent-owned test/diagnostic Python processes were requested to stop. No PostgreSQL/old-service restart or broad connection termination occurred. Later a single native `pg_isready` probe with inner3s/outer7s bound returned `127.0.0.1:24583 - accepting connections`, exit0; subsequent focused and complete real-PG suites succeeded. Any potentially interrupted synthetic test-schema cleanup was not guessed by deleting unrelated schemas.

## Downstream and external limits

- Task4 owns independent pickup/release/hold/notification/node facts. Current stable receipt/ticket/document/shipment IDs and versions are documented. Logistics metadata/member changes can invalidate a dependent instruction even with unchanged fees; they do not promise a FinanceEvent. No shipment/dispatch/arrival here synthesizes actual release.
- Real source row defects, missing contacts/destinations/gross weight, ambiguous fee columns and current price-card validity require explicit real evidence; unknown values stay pending. Original rows are never matched/merged by name/date/amount/receipt alone.
- Latest production snapshots/native F6 stock completeness, current warehouse contact/address material, actual latest cards and final notification/document template appearance remain external acceptance inputs. No customer data or business policy was invented to pass tests.
- Model test uses only `httpx.MockTransport`; all real model flags remained disabled. It proves persistence, original-text retention and late-candidate protection, not actual model translation quality/latency/cost.
- XLSX artifacts are structurally opened/read back, but no real user browser, WPS second-machine check, exact print layout, WhatsApp/WeChat paste, production concurrency/capacity or backup/restore exercise is claimed.

Final delivery commit is supplied in the parent-facing result; the detailed callable/HTTP/schema and requirement mapping is in `docs/development/logistics-contract.md`.
