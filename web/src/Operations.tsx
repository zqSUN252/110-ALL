import { useContext, useEffect, useState } from "react";
import { api, rows, query, download, type Row } from "./api";
import {
  DataTable,
  Fields,
  Form,
  ErrorBox,
  RecordView,
  Picker,
  f,
  type Field,
} from "./ui";
import {
  lookup,
  required,
  select,
  paymentFields,
  versions,
  clean,
  ticketFields,
} from "./catalog";
import { useT, LangContext } from "./i18n";
import { ActionForm } from "./workflows";
import {AssistantTools} from './AssistantTools';
import {Reports} from './Reports';

function ImportFlow({ bank = false }: { bank?: boolean }) {
  const t = useT();
  const [preview, setPreview] = useState<Row>(),
    [selected, setSelected] = useState(new Set<string>()),
    [corrections, setCorrections] = useState<Record<string, Row>>({}),
    [exclusions, setExclusions] = useState<Record<string, string>>({}),
    [edit, setEdit] = useState<Row | null>(null),
    [fileInput, setFileInput] = useState<Row>({}),
    [result, setResult] = useState<Row>(),
    [error, setError] = useState<any>(),
    [replace, setReplace] = useState(false),
    [map, setMap] = useState<Record<string, string>>({});
  const fields = bank
    ? [
        lookup("account_id", "accounts", true),
        f("attachment_id", "file", { required: true }),
      ]
    : [
        lookup("warehouse_id", "warehouses", true),
        select("kind", ["packing", "f6"], true),
        f("attachment_id", "file", { required: true }),
        f("column_mapping", "rows", {
          fields: [
            required("sheet"),
            required("field"),
            required("column", "number"),
            select("basis", ["total", "CBM", "KG", "PCS"]),
          ],
        }),
      ];
  const mappingBody = (b: Row) => {
    const { column_mapping, ...body } = b;
    const mapping: Row = {};
    for (const r of column_mapping || []) {
      mapping[r.sheet] ??= { fields: {} };
      mapping[r.sheet].fields[r.field] = {
        column: r.column,
        ...(r.basis ? { basis: r.basis } : {}),
      };
    }
    return { ...body, ...(column_mapping?.length ? { mapping } : {}) };
  };
  const selection = () =>
    [...selected].map((id) => ({
      row_id: id,
      ...(corrections[id] ? { values: corrections[id] } : {}),
    }));
  const excluded = () =>
    rows(preview)
      .filter((r) => r.eligible && !selected.has(r.id))
      .map((r) => ({ row_id: r.id, reason: exclusions[r.id] || "" }));
  return (
    <section>
      <Form
        fields={fields}
        initial={bank ? {} : { kind: "packing" }}
        submitLabel={["读取原文件预览", "Preview original file"]}
        submit={async (b, k) => {
          setFileInput(b);
          const r = await api(
            bank ? "/api/bank/imports/preview" : "/api/cargo/imports/preview",
            bank ? b : mappingBody(b),
            "POST",
            k,
          );
          if(bank) r.account_version=(await api('/api/bank/accounts/'+b.account_id)).version;
          setPreview(r);
          setSelected(new Set());
          return r;
        }}
      />
      {preview && (
        <section className="detail-pane">
          <h2>
            {t(
              "原文件 / 工作表 / 字段预览",
              "Original file / sheet / field preview",
            )}
          </h2>
          {bank ? (
            <>
              <RecordView data={preview} />
              <Form
                fields={[
                  f("sheets", "strings", {
                    required: true,
                    help: [
                      "逐行输入上方已核对的工作表名称。",
                      "Enter one reviewed sheet name per line.",
                    ],
                  }),
                  select("date_order", ["auto", "dmy", "mdy"]),
                  f("trusted_reference", "bool"),
                ]}
                initial={{
                  expected_version: preview.version,
                  account_version: preview.account_version,
                  date_order: "auto",
                  trusted_reference: false,
                }}
                submitLabel={["确认导入原始银行行", "Confirm bank rows"]}
                submit={async (b, k) => {
                  const r = await api(
                    "/api/bank/imports/" + preview.id + "/confirm",
                    b,
                    "POST",
                    k,
                  );
                  setResult(r);
                  return r;
                }}
              />
            </>
          ) : (
            <>
              <RecordView
                data={{ mapping: preview.mapping, issues: preview.issues }}
              />
              <DataTable
                identity="import-preview"
                items={rows(preview)}
                selectable
                selected={selected}
                onSelection={setSelected}
                columns={[
                  { key: "sheet" },
                  { key: "row_number" },
                  {
                    key: "receipt",
                    render: (r) => (
                      <button
                        className="text-button"
                        onClick={() => setEdit(r)}
                      >
                        {r.receipt || t("核对原行", "Review row")}
                      </button>
                    ),
                  },
                  { key: "contact_raw" },
                  { key: "pieces" },
                  { key: "kg" },
                  { key: "cbm" },
                  { key: "freight_usd" },
                  { key: "clearing_ngn" },
                  { key: "eligible" },
                  { key: "issues" },
                  {
                    key: "exclude",
                    title: ["排除依据", "Exclusion reason"],
                    render: (r) => (
                      <input
                        aria-label={
                          t("排除依据", "Exclusion reason") + " " + r.id
                        }
                        value={exclusions[r.id] || ""}
                        onChange={(e) =>
                          setExclusions({
                            ...exclusions,
                            [r.id]: e.target.value,
                          })
                        }
                      />
                    ),
                  },
                ]}
              />
              {edit && (
                <div className="detail-pane">
                  <RecordView data={edit.raw} />
                  <Form
                    key={edit.id}
                    title={t("明确修正原字段", "Confirm source corrections")}
                    fields={ticketFields
                      .filter(
                        (f) =>
                          !["document_id", "native_receipt_id"].includes(f.key),
                      )
                      .map((f) => ({ ...f, required: false }))}
                    initial={corrections[edit.id] || {}}
                    submit={async (b) => {
                      setCorrections({ ...corrections, [edit.id]: clean(b) });
                      return {};
                    }}
                  />
                </div>
              )}
              <label>
                <input
                  type="checkbox"
                  checked={replace}
                  onChange={(e) => setReplace(e.target.checked)}
                />
                {t(
                  "替换已有箱单，保留明确身份",
                  "Replace an existing document with explicit identity mapping",
                )}
              </label>
              {replace && (
                <div>
                  {[...selected].map((id) => (
                    <div className="field" key={id}>
                      <Picker
                        kind="tickets"
                        title={
                          (rows(preview).find((r) => r.id === id)?.receipt ||
                            id) +
                          " → " +
                          t(
                            "对应旧票件（空白为新票）",
                            "Existing ticket (blank = new ticket)",
                          )
                        }
                        value={map[id] || ""}
                        onChange={(v) => setMap({ ...map, [id]: v })}
                      />
                    </div>
                  ))}
                </div>
              )}
              <ActionForm
                key={String(replace) + preview.id}
                action={{
                  key: "commit",
                  title: replace
                    ? ["替换前核对差异", "Review replacement differences"]
                    : ["确认原始行导入", "Confirm selected source rows"],
                  cap: "packing.import",
                  fields: replace
                    ? [
                        lookup("document_id", "documents", true),
                        required("reason"),
                      ]
                    : [
                        lookup("shipment_id", "shipments"),
                        f("reference"),
                        f("uploaded_on", "date"),
                      ],
                  preview: replace
                    ? async (b) =>
                        api(
                          "/api/packing/documents/" +
                            b.document_id +
                            "/replacement-preview",
                          {
                            batch_id: preview.id,
                            mapping: selection().map((s) => ({
                              ...s,
                              ticket_id: map[s.row_id] || null,
                            })),
                            excluded_rows: excluded(),
                          },
                        )
                    : undefined,
                  run: async (b, k) => {
                    const r = replace
                      ? await api(
                          "/api/packing/documents/" +
                            b.document_id +
                            "/replace",
                          {
                            expected_version: (
                              await api(
                                "/api/packing/documents/" + b.document_id,
                              )
                            ).version,
                            batch_id: preview.id,
                            mapping: selection().map((s) => ({
                              ...s,
                              ticket_id: map[s.row_id] || null,
                            })),
                            excluded_rows: excluded(),
                            reason: b.reason,
                            preview: b.preview,
                          },
                          "POST",
                          k,
                        )
                      : await api(
                          "/api/cargo/imports/" + preview.id + "/commit",
                          {
                            ...b,
                            expected_version: preview.version,
                            selection: selection(),
                            excluded_rows: excluded(),
                          },
                          "POST",
                          k,
                        );
                    setResult(r);
                    return r;
                  },
                }}
                onSaved={() => {}}
              />
            </>
          )}
          {result && <RecordView data={result} />}
        </section>
      )}
      <ErrorBox error={error} />
    </section>
  );
}

function Assistant() {
  const t = useT();
  const [threads, setThreads] = useState<Row[]>([]),
    [thread, setThread] = useState<Row>(),
    [flags,setFlags]=useState<Row>(),
    [error, setError] = useState<any>();
  const refresh = () =>
    api("/api/assistant/threads")
      .then((d) => setThreads(rows(d)))
      .catch(setError);
  useEffect(() => {
    void refresh();
    api('/api/workspace/integrations').then(setFlags).catch(setError);
  }, []);
  useEffect(() => {
    if (!thread) return;
    const interval = setInterval(
      () =>
        api("/api/assistant/threads/" + thread.id)
          .then(setThread)
          .catch(setError),
      2500,
    );
    return () => clearInterval(interval);
  }, [thread?.id]);
  return (
    <section>
      <ErrorBox error={error} />
      {flags&&<p role="status">{flags.model_calls_enabled?t('模型调用已启用；执行仍受部署及权限控制。','Model calls enabled; deployment and permissions still apply.'):t('模型调用未启用。可以使用下方查询、对比及文件功能；对话任务尚未调用模型。','Model calls are disabled. Queries, comparison and files remain available; conversation tasks have not called a model.')}</p>}
      <Form
        title={t("新建对话", "New conversation")}
        fields={[lookup("warehouse_id", "warehouses", true), f("title")]}
        submit={async (b) => {
          const r = await api("/api/assistant/threads", b);
          setThread(r);
          void refresh();
          return r;
        }}
      />
      <div className="toolbar">
        {threads.map((r) => (
          <button
            key={r.id}
            onClick={() =>
              api("/api/assistant/threads/" + r.id)
                .then(setThread)
                .catch(setError)
            }
          >
            {r.title || r.created_at}
          </button>
        ))}
      </div>
      {thread && (
        <div className="detail-pane">
          <h2>{thread.title || t("对话", "Conversation")}</h2>
          <AssistantTools thread={thread} onRefresh={()=>api('/api/assistant/threads/'+thread.id).then(setThread).catch(setError)}/>
          <div className="chat-messages">
            {(thread.turns || []).map((turn: Row) => (
              <article className="chat-turn" key={turn.id}>
                <p>{turn.text}</p>
                <RecordView
                  data={{
                    status: turn.status,
                    result: turn.result,
                    error_code: turn.error_code,
                  }}
                />
              </article>
            ))}
          </div>
          <Form
            key={thread.id}
            fields={[
              required("text", "textarea"),
              f("attachment_ids", "file", { multi: true }),
            ]}
            submitLabel={["提交对话", "Submit turn"]}
            submit={async (b, k) => {
              const current = await api("/api/assistant/threads/" + thread.id);
              const r = await api(
                "/api/assistant/threads/" + thread.id + "/turns",
                { ...b, expected_version: current.version },
                "POST",
                k,
              );
              setThread(await api("/api/assistant/threads/" + thread.id));
              return r;
            }}
          />
          <p>
            {t(
              "识别、模型、采集与发送由部署控制。未启用时显示实际未执行状态。",
              "Recognition, models, collection and sending are deployment-controlled. Disabled operations report that they were not executed.",
            )}
          </p>
          {(thread.files || []).map((file: Row) => (
            <p key={file.id}>
              {file.availability === "ready" && file.url ? (
                <a href={file.url}>{file.kind}</a>
              ) : (
                file.availability
              )}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function Configuration({ home }: { home: boolean }) {
  const t = useT();
  const [current, setCurrent] = useState<Row>(),
    [error, setError] = useState<any>();
  const path = home ? "/api/workspace/home-profile" : "/api/documents/profile";
  useEffect(() => {
    api(path).then(setCurrent).catch(setError);
  }, [path]);
  const fields: Field[] = home
    ? [
        f("warehouses", "rows", {
          fields: [
            required("name"),
            required("address", "textarea"),
            f("receiving_hours"),
            f("contacts", "textarea"),
            select("country", ["CN", "NG"], true),
          ],
        }),
        f("hotline", "textarea"),
        f("openshipment", "textarea"),
        f("hero_large", "file"),
        f("hero_small", "file"),
        required("source"),
      ]
    : [
        "china_contacts",
        "packing_list_contact",
        "customer_care_contact",
        "tracking_domain",
        "bank_name",
        "account_holder",
        "account_number",
        "public_company_name",
        "public_hotline",
        "public_address",
        "public_website",
      ].map((k) => f(k, "textarea"));
  return (
    <>
      <ErrorBox error={error} />
      {current && (
        <Form
          fields={fields}
          initial={{
            ...(current.profile || current.value || current),
            expected_version: current.version,
          }}
          submit={(b, k) => api(path, b, "POST", k)}
          onSaved={setCurrent}
        />
      )}{" "}
      {!home && (
        <Form
          title={t(
            "配置原始 BO / SI / SO / DN 模板",
            "Configure original BO / SI / SO / DN template",
          )}
          fields={[
            select("kind", ["BO", "SI", "SO", "DN"], true),
            f("attachment_id", "file", { required: true }),
            f("expected_version", "number", { required: true, default: 0 }),
            f("positions", "rows", {
              fields: [required("field"), required("cell")],
            }),
          ]}
          submit={async (b, k) => {
            const { positions, ...body } = b;
            return api(
              "/api/document-templates",
              {
                ...body,
                field_mapping: Object.fromEntries(
                  positions.map((r: Row) => [r.field, r.cell]),
                ),
              },
              "POST",
              k,
            );
          }}
        />
      )}
    </>
  );
}

function Migration() {
  const t = useT();
  const [batches, setBatches] = useState<Row[]>([]),
    [source, setSource] = useState<Row>(),
    [plan, setPlan] = useState<Row>(),
    [dataset, setDataset] = useState(""),
    [job, setJob] = useState<Row>(),
    [details, setDetails] = useState<Row>(),
    [error, setError] = useState<any>();
  useEffect(() => {
    api("/api/migration/batches")
      .then((d) => setBatches(rows(d)))
      .catch(setError);
  }, []);
  useEffect(() => {
    if (!job?.id || !["pending", "running"].includes(job.state)) return;
    const timer = setInterval(
      () =>
        api("/api/migration/jobs/" + job.id)
          .then(setJob)
          .catch(setError),
      2000,
    );
    return () => clearInterval(timer);
  }, [job?.id, job?.state]);
  return (
    <>
      <p>
        {t(
          "来源包快照与正式生产快照分别核对。缺失原件、未确认归属和历史审核依据保留，不补造业务事实。",
          "Review supplied snapshots separately from production. Missing originals, ownership and historical review facts remain explicit.",
        )}
      </p>
      <ErrorBox error={error} />
      <DataTable
        identity="migration-batches"
        items={batches}
        columns={[
          { key: "state" },
          { key: "source_version" },
          { key: "sha256" },
          {
            key: "issues",
            render: (r) => (
              <button
                onClick={() =>
                  api("/api/migration/batches/" + r.id + "/issues")
                    .then(setDetails)
                    .catch(setError)
                }
              >
                {t("查看待核对事项", "Review issues")}
              </button>
            ),
          },
        ]}
      />
      <RecordView data={details} />
      <section className="detail-pane">
        <h2>{t("导入原始受控ZIP", "Import controlled source ZIP")}</h2>
        <input
          type="file"
          accept=".zip"
          aria-label={t("原始来源ZIP", "Original source ZIP")}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setError(null);
            try {
              setSource(
                await api(
                  "/api/migration/sources",
                  new Blob([file], { type: "application/zip" }),
                ),
              );
            } catch (e) {
              setError(e);
            }
          }}
        />
        <label>
          {t("来源批次名称", "Dataset name")}
          <input value={dataset} onChange={(e) => setDataset(e.target.value)} />
        </label>
        <button
          disabled={!source || !dataset}
          onClick={() =>
            api(
              "/api/migration/sources/" +
                (source?.id || source?.source_id) +
                "/plan?dataset=" +
                encodeURIComponent(dataset),
            )
              .then(setPlan)
              .catch(setError)
          }
        >
          {t("预检来源计划", "Review source plan")}
        </button>
        {plan && (
          <>
            <RecordView data={plan} />
            <button
              className="primary"
              onClick={() =>
                api(
                  "/api/migration/sources/" +
                    (source?.id || source?.source_id) +
                    "/apply",
                  { dataset, plan_hash: plan.plan_hash },
                )
                  .then(setJob)
                  .catch(setError)
              }
            >
              {t("确认已审阅计划并排队", "Confirm reviewed plan & queue")}
            </button>
          </>
        )}
      </section>
      <section className="detail-pane">
        <h2>{t("完整业务备份", "Complete business backup")}</h2>
        <button
          onClick={() =>
            api("/api/migration/backups", {}).then(setJob).catch(setError)
          }
        >
          {t("建立受控备份任务", "Queue protected backup")}
        </button>
        {job && <RecordView data={job} />}
        <p>
          {t(
            "恢复使用受控部署工具进入独立空环境；团队空间未绑定时不会声称归档完成。",
            "Restore uses controlled deployment tools and an isolated empty target. An unbound team destination is not reported as archived.",
          )}
        </p>
      </section>
    </>
  );
}

export function Operations({
  kind,
  user,
  openRecord,
}: {
  kind: string;
  user: Row;
  openRecord: (kind: string, id: string) => void;
}) {
  const t = useT(),
    [result, setResult] = useState<any>(),
    [error, setError] = useState<any>();
  let content;
  if(['warehouse-report','bank-archive','opening'].includes(kind))return <Reports kind={kind} openRecord={openRecord}/>;
  if (kind === "imports" || kind === "bank-import")
    content = <ImportFlow bank={kind === "bank-import"} />;
  else if (kind === "assistant") content = <Assistant />;
  else if (kind === "home-settings" || kind === "document-settings")
    content = <Configuration home={kind === "home-settings"} />;
  else if (kind === "migration") content = <Migration />;
  else if (kind === "search")
    content = (
      <>
        <Form
          fields={[required("query")]}
          submitLabel={["查找", "Search"]}
          submit={async (b) => {
            const r = await api(
              "/api/workspace/search?query=" + encodeURIComponent(b.query),
            );
            setResult(r);
            return {};
          }}
        />
        {result && (
          <DataTable
            identity="global-search"
            items={rows(result)}
            columns={[
              { key: "kind" },
              {
                key: "reference",
                render: (r) => (
                  <button
                    className="text-button"
                    onClick={() => openRecord(r.kind, r.id)}
                  >
                    {r.reference}
                  </button>
                ),
              },
            ]}
          />
        )}
      </>
    );
  else if (kind === "dispatch")
    content = (
      <Form
        fields={[
          lookup("shipment_id", "shipments", true),
          required("occurred_on", "date"),
          f("reference"),
          f("rows", "rows", {
            fields: [
              lookup("receipt_id", "receipts", true),
              required("pieces", "number"),
              required("kg"),
              required("cbm"),
            ],
          }),
        ]}
        submitLabel={["确认批次出库", "Confirm dispatch batch"]}
        submit={async (b, k) => {
          const r = await api(
            "/api/cargo/dispatch",
            {
              ...b,
              rows: await versions(b.rows, "receipt_id", "/api/receipts/"),
            },
            "POST",
            k,
          );
          setResult(r);
          return r;
        }}
      />
    );
  else if (kind === "today")
    content = (
      <>
        <Form
          fields={[
            lookup("warehouse_id", "warehouses", true),
            required("on", "date"),
          ]}
          submitLabel={["读取核销汇总", "Read approval totals"]}
          submit={async (b) => {
            const r = await api("/api/finance/totals?" + query(b));
            setResult(r);
            return {};
          }}
        />
        <Form
          title={t("导出当日核销", "Export approvals")}
          fields={[
            lookup("warehouse_id", "warehouses", true),
            required("on", "date"),
          ]}
          submitLabel={["生成Excel", "Generate Excel"]}
          submit={async (b, k) => {
            const r = await api("/api/finance/exports/approved", b, "POST", k);
            if (r.attachment_id)
              await download(
                "/api/finance/files/" + r.attachment_id,
                "MCSTAR-approved.xlsx",
              );
            setResult(r);
            return r;
          }}
        />
      </>
    );
  else if (kind === "period")
    content = (
      <>
        <Form
          fields={[
            lookup("warehouse_id", "warehouses", true),
            lookup("party_id", "parties", true),
            required("period"),
          ]}
          submitLabel={["查看整期余额", "Read period balance"]}
          submit={async (b) => {
            const r = await api("/api/payables/period?" + query(b));
            setResult(r);
            return {};
          }}
        />
        <Form
          title={t(
            "按账单方整期付款（不强制分柜）",
            "Pay counterparty period (shipment allocation optional)",
          )}
          fields={[...paymentFields, required("period")]}
          submitLabel={["确认付款", "Confirm payment"]}
          submit={async (b, k) => {
            const r = await api("/api/payable-payments", b, "POST", k);
            setResult(r);
            return r;
          }}
        />
      </>
    );
  else if (kind === "bank-identity")
    content = (
      <ActionForm
        action={{
          key: "identity",
          title: [
            "核对同一 / 不同真实交易",
            "Review same / distinct real transactions",
          ],
          cap: "bank.identity",
          fields: [
            lookup("left_id", "entries", true),
            lookup("right_id", "entries", true),
            select("decision", ["same", "distinct"], true),
            f("expected_version", "number", { default: 0 }),
            required("source"),
            required("reason"),
          ],
          preview: async (b) => ({
            left: await api("/api/workspace/bank-entry/" + b.left_id),
            right: await api("/api/workspace/bank-entry/" + b.right_id),
            decision: b.decision,
            source: b.source,
            reason: b.reason,
          }),
          run: async (b, k) => {
            const { preview, ...data } = b;
            return api(
              "/api/legacy/bank-identities/adjudicate",
              {
                ...data,
                left_version: preview.left.version,
                right_version: preview.right.version,
                account_version: preview.left.account_version,
              },
              "POST",
              k,
            );
          },
        }}
        onSaved={setResult}
      />
    );
  else if (kind === "legacy-bank-evidence")
    content = (
      <ActionForm
        action={{
          key: "legacy-bank",
          title: [
            "补充历史银行证据（不新增收款）",
            "Supplement historical bank proof (no new cash)",
          ],
          cap: "legacy.resolve",
          fields: [
            lookup("claim_id", "claims", true),
            lookup("control_owner_party_id", "parties", true),
            f("members", "rows", {
              fields: [
                lookup("bill_id", "legacy-bills", true),
                required("amount_ngn"),
                required("source"),
              ],
            }),
            required("reason"),
            required("source"),
          ],
          preview: async (b) => {
            const claim = await api("/api/workspace/bank-claim/" + b.claim_id);
            const members = await Promise.all(
              b.members.map(async (m: Row) => {
                const bill = await api("/api/legacy/bills/" + m.bill_id);
                return {
                  ...m,
                  expected_version: bill.version,
                  invoice_version: bill.invoice?.version,
                };
              }),
            );
            return api("/api/legacy/bank-evidence/preview", {
              ...b,
              ...claim.versions,
              members,
            });
          },
          run: async (b, k) => {
            const claim = await api("/api/workspace/bank-claim/" + b.claim_id);
            const members = await Promise.all(
              b.members.map(async (m: Row) => {
                const bill = await api("/api/legacy/bills/" + m.bill_id);
                return {
                  ...m,
                  expected_version: bill.version,
                  invoice_version: bill.invoice?.version,
                };
              }),
            );
            return api(
              "/api/legacy/bank-evidence/confirm",
              { ...b, ...claim.versions, members },
              "POST",
              k,
            );
          },
        }}
        onSaved={setResult}
      />
    );
  return (
    <section>
      <h1>
        {
          (
            {
              search: t("全局查找", "Global search"),
              imports: t("箱单 / F6导入", "Packing / F6 import"),
              dispatch: t("中国仓批次出库", "China warehouse dispatch"),
              today: t("今日核销", "Today’s approvals"),
              period: t("账单方周期结算", "Counterparty period settlement"),
              "bank-import": t("导入银行流水", "Import bank statements"),
              assistant: t("110对话助手", "110 assistant"),
              "home-settings": t("首页资料", "Home profile"),
              "document-settings": t(
                "单证资料与模板",
                "Document profile & templates",
              ),
              migration: t("数据管理与备份", "Data & backups"),
              "bank-identity": t("银行交易身份复核", "Bank identity review"),
              "legacy-bank-evidence": t(
                "历史银行证据补充",
                "Historical bank evidence",
              ),
            } as Row
          )[kind]
        }
      </h1>
      <ErrorBox error={error} />
      {content}
      {result && kind !== "search" && (
        <div className="detail-pane">
          <RecordView data={result} links onLink={openRecord} />
        </div>
      )}
    </section>
  );
}
