import { useContext, useEffect, useRef, useState } from "react";
import { Plus, ArrowLeft, RefreshCw, X } from "lucide-react";
import { api, rows, query, type Row } from "./api";
import { pages, formInitial, clean, lookup, required, select } from "./catalog";
import {
  DataTable,
  ErrorBox,
  Fields,
  Form,
  Pager,
  RecordView,
  FileViewer,
  f,
} from "./ui";
import { LangContext, useT } from "./i18n";
import {HoldReview} from './HoldReview';
import {PartyLedger} from './PartyLedger';
import {BankEntries} from './BankEntries';
import {BillingGroups} from './BillingGroups';
import {ContactLinks} from './ContactLinks';
import {
  actionsFor,
  ActionForm,
  Notice,
  ExportPanel,
  CopyMarks,
  type Action,
} from "./workflows";

function RouteRail({ detail }: { detail: Row }) {
  const t = useT();
  const steps = [
    ["收据", "Receipt", Boolean(detail.receipt)],
    ["到货", "Arrived", Boolean((detail.arrivals || []).length)],
    ["计费", "Billed", Boolean(detail.invoice_id)],
    ["放货", "Released", Boolean(detail.actual_release_at)],
  ] as const;
  return (
    <ol className="route-rail" aria-label={t("货运进度", "Cargo route")}>
      {steps.map(([zh, en, done]) => (
        <li key={zh} className={done ? "done" : ""}>
          <span className="route-dot" aria-hidden="true" />
          <span className="route-label">{t(zh, en)}</span>
        </li>
      ))}
    </ol>
  );
}

export function EntityPage({
  kind,
  user,
  openRecord,
}: {
  kind: string;
  user: Row;
  openRecord: (kind: string, id: string) => void;
}) {
  const spec = pages[kind],
    t = useT();
  const [data, setData] = useState<any>([]),
    [error, setError] = useState<any>(),
    [busy, setBusy] = useState(false),
    [filters, setFilters] = useState<Row>({}),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState(new Set<string>()),
    [detail, setDetail] = useState<Row | null>(null),
    [create, setCreate] = useState(false),
    [tab, setTab] = useState("details"),
    [notice, setNotice] = useState<string | null>(null),
    [exporting, setExporting] = useState(false),
    [exportPreset, setExportPreset] = useState('packing');
  const [history, setHistory] = useState<string[]>([]);
  const [aux, setAux] = useState<any>(),
    [action, setAction] = useState<Action | null>(null);
  const [copiedList,setCopiedList]=useState(false);
  const lang = useContext(LangContext);
  const requestSequence = useRef(0);
  const filterSignature = JSON.stringify(filters) + ':' + offset;
  const latestFilter = useRef(filterSignature);
  latestFilter.current = filterSignature;
  const allowed = (cap: string) =>
    !cap ||
    user.capabilities?.includes("*") ||
    user.capabilities?.includes(cap);
  const load = async () => {
    const sequence = ++requestSequence.current;
    const signature = filterSignature;
    const current = () => sequence === requestSequence.current && signature === latestFilter.current;
    if ((spec.endpoint.startsWith('/api/finance/records/') || kind==='parties') && !filters.warehouse_id) {
      setData([]); return;
    }
    setBusy(true);
    setError(null);
    try {
      const params = {
        ...filters,
        ...(kind==='invoices'||kind==='payables'?{kind:kind==='payables'?'AP':'AR'}:{}),
        limit: 50,
        ...(spec.cursor ? { after: history[offset / 50 - 1] } : { offset }),
      };
      const result = await api(spec.endpoint + "?" + query(params));
      if (current()) setData(result);
    } catch (e) {
      if (current()) setError(e);
    } finally {
      if (current()) setBusy(false);
    }
  };
  useEffect(() => {
    const timer = setTimeout(() => void load(), 220);
    return () => clearTimeout(timer);
  }, [JSON.stringify(filters), offset]);
  useEffect(()=>{
    if(spec.endpoint.startsWith('/api/finance/records/')||kind==='parties')
      api('/api/warehouses').then(d=>{const first=rows(d)[0];if(first)setFilters(v=>v.warehouse_id?v:{...v,warehouse_id:first.id})}).catch(()=>{});
  },[kind]);
  const open = async (row: Row) => {
    setError(null);
    setTab("details");
    setAux(null);
    setAction(null);
    try {
      setDetail(spec.detail ? await api(spec.detail + row.id) : row);
    } catch (e) {
      setError(e);
    }
  };
  const update = async (result: Row) => {
    if (detail && spec.detail) {
      const current = await api(spec.detail + detail.id);
      setDetail(current);
    } else if (detail && result.id === detail.id)
      setDetail({ ...detail, ...result });
    void load();
  };
  useEffect(() => {
    const listener = (e: Event) => {
      const d = (e as CustomEvent).detail;
      if (d.kind === kind) void open({ id: d.id });
    };
    window.addEventListener("open-record", listener);
    return () => window.removeEventListener("open-record", listener);
  }, [kind]);
  const editFields =
    spec.edit ||
    (kind === "rates" || kind === "mbl" ? spec.create?.fields : undefined);
  const editPath =
    kind === "users" ? "/api/admin/users/" : spec.detail || spec.endpoint + "/";
  const editableInitial=detail&&editFields?formInitial(editFields,detail):{};
  const changedValues=(values:Row)=>Object.fromEntries(Object.entries(values).filter(([name,value])=>name==='expected_version'||JSON.stringify(value)!==JSON.stringify(editableInitial[name])));
  const editing: Action | undefined =
    detail && editFields
      ? {
          key: "edit-" + detail.id,
          title: ["编辑资料", "Edit details"],
          cap: spec.create?.cap || "",
          fields: editFields,
          initial: {
            ...editableInitial,
            expected_version: detail.version,
          },
          preview:
            kind === "tickets"
              ? (b) => {
                  const { expected_version, ...changes } = changedValues(b);
                  return api(
                    "/api/tickets/" + detail.id + "/change-preview",
                    changes,
                  );
                }
              : kind==='shipments'&&detail.clearing_invoice_id ? async(b)=>{
                  const changes=changedValues(b);
                  return changes.clearing_amount_ngn!==undefined
                    ? {finance_preview:await api('/api/invoices/'+detail.clearing_invoice_id+'/adjust-preview?'+query({amount_ngn:changes.clearing_amount_ngn}))}
                    : {finance_preview:null};
                } : undefined,
          run: async (b, k) => {
            const { preview, ...values } = b;
            const dirty = changedValues(values);
            if ((kind === "tickets"||kind==='shipments') && preview?.finance_preview)
              dirty.finance_preview = preview.finance_preview;
            return kind==='mbl'?api('/api/mbl',{...clean(values),shipment_id:detail.shipment_id},'POST',k):api(editPath + detail.id, clean(dirty), "PATCH", k);
          },
        }
      : undefined;
  let columns = spec.columns.map((c) =>
    c.key === 'phone' && kind==='parties' ? {...c,render:(r:Row)=><ContactLinks record={r}/>} : c.key === "actions"
      ? {
          ...c,
          width: 330,
          render: (r: Row) => (
            <div className="row-actions">
              {kind === "receipts" ? (
                <>
                  <button onClick={() => setNotice(r.id)}>
                    {t("收货通知", "Receipt notice")}
                  </button>
                  <button
                    onClick={() => {
                      setSelected(new Set([r.id]));
                      setExportPreset('label_fee');
                      setExporting(true);
                    }}
                  >
                    {t("收费标签", "Fee label")}
                  </button>
                  <button
                    onClick={() => {
                      setSelected(new Set([r.id]));
                      setExportPreset('label_plain');
                      setExporting(true);
                    }}
                  >
                    {t("普通标签", "Plain label")}
                  </button>
                  <button
                    onClick={() => {
                      setSelected(new Set([r.id]));
                      setExportPreset('label_confirmation');
                      setExporting(true);
                    }}
                  >
                    {t("确认标签", "Confirmation label")}
                  </button>
                </>
              ) : (
                <>
                <button onClick={() => setNotice(r.id)}>
                  {t("装柜通知", "Loading notice")}
                </button>
                {r.loading_notified&&<button className="loading-mark" onClick={async()=>{await open(r);setTab('marks');try{setAux(await api('/api/tickets/'+r.id+'/notification-marks?history=true'))}catch(e){setError(e)}}}>{t('已通知装柜','Loading notice copied')}<X size={14}/></button>}
                </>
              )}
            </div>
          ),
        }
      : c.key.endsWith("_id")
        ? {
            ...c,
            render: (r: Row) =>
              r[c.key] ? (
                <button
                  className="text-button"
                  onClick={() =>
                    openRecord(
                      (
                        {
                          party_id: "parties",
                          shipment_id: "shipments",
                          invoice_id: "invoices",
                          native_id:
                            kind === "legacy-bills"
                              ? "invoices"
                              : kind.replace("legacy-", ""),
                        } as Row
                      )[c.key] || "tickets",
                      r[c.key],
                    )
                  }
                >
                  {c.key==='party_id' ? r.party_name || r[c.key] : r[c.key]}
                </button>
              ) : null,
          }
        : c,
  );
  if (!columns.some((c) => ["reference", "receipt", "name"].includes(c.key)))
    columns = [
      {
        key: "open",
        title: ["详情", "Details"],
        width: 90,
        render: (r) => (
          <button className="text-button" onClick={() => open(r)}>
            {t("查看", "Open")}
          </button>
        ),
      },
      ...columns,
    ];
  const list = rows(data);
  const attachments = detail?.images || detail?.attachments || [];
  return (
    <section className="entity-page">
      <div className="page-heading">
        <h1>{t(...spec.title)}</h1>
        <div className="toolbar">
          {kind==='f6'&&<button disabled={!selected.size} onClick={async()=>{try{await navigator.clipboard.writeText(list.filter(r=>selected.has(r.id)).map(r=>spec.columns.map(c=>r[c.key]??'').join('\t')).join('\n'));setCopiedList(true)}catch(e){setError(e);setCopiedList(false)}}}>{copiedList?t('已复制所选文字','Selected text copied'):t('复制所选列表文字','Copy selected list text')}</button>}
          <button onClick={load} disabled={busy}>
            <RefreshCw size={16} />
            {t("刷新", "Refresh")}
          </button>
          {spec.create && allowed(spec.create.cap) && (
            <button
              className="primary"
              onClick={() => {
                setCreate(true);
                setDetail(null);
              }}
            >
              <Plus size={16} />
              {t(...spec.create.title)}
            </button>
          )}
          {["receipts", "tickets", "documents"].includes(kind) && (
            <button onClick={() => setExporting((v) => !v)}>
              {t("导出 / 标签", "Export / labels")}
            </button>
          )}
        </div>
      </div>
      {spec.legacy && (
        <p className="source-note">
          {t(
            "来源包快照。归属、日期与银行事实按原始证据逐项确认。",
            "Supplied snapshot. Confirm ownership, dates and bank facts from source evidence.",
          )}
        </p>
      )}
      <ErrorBox error={error} />
      {data.totals&&<RecordView data={data.totals}/>}
      {create && spec.create && (
        <div className="detail-pane">
          <div className="section-heading">
            <h2>{t(...spec.create.title)}</h2>
            <button onClick={() => setCreate(false)}>
              {t("关闭", "Close")}
            </button>
          </div>
          <Form
            fields={spec.create.fields}
            initial={spec.create.initial}
            submit={(body, key) =>
              api(spec.create!.path, clean(body), "POST", key)
            }
            onSaved={() => void load()}
          />
        </div>
      )}
      {spec.filters && (
        <div className="filters">
          <Fields
            fields={spec.filters}
            value={filters}
            onChange={(v) => {
              setFilters(v);
              setOffset(0);
            }}
          />
        </div>
      )}
      {busy && (
        <div className="loading" role="status">
          {t("正在读取…", "Loading…")}
        </div>
      )}
      <div className={"workbench" + (detail ? " has-detail" : "")}>
        <div className="workbench-list">
      <DataTable
        identity={user.id + ":" + kind}
        columns={columns}
        items={list}
        frozen={spec.frozen}
        onSelect={open}
        selected={selected}
        selectable={["receipts", "tickets", "documents",'f6'].includes(kind)}
        onSelection={setSelected}
      />
      <Pager
        offset={offset}
        count={list.length}
        total={data.total}
        onChange={(n) => {
          if (spec.cursor && n > offset)
            setHistory((h) => {
              const next = [...h];
              next[offset / 50] =
                data.next ||
                data.next_id ||
                data.next_cursor ||
                list.at(-1)?.id;
              return next;
            });
          setOffset(n);
        }}
      />
      {exporting && (
        <ExportPanel
          key={exportPreset+':'+[...selected].join(',')}
          preset={exportPreset}
          kind={kind === "receipts" ? "receipts" : "tickets"}
          ids={[...selected]}
          documentId={kind === "documents" ? [...selected][0] : undefined}
        />
      )}
      {['invoices','payables'].includes(kind)&&filters.party_id&&<PartyLedger partyId={filters.party_id} user={user} openRecord={openRecord} ticketsOnly/>}
      {notice && (
        <Notice kind={kind} identity={notice} onClose={() => setNotice(null)} onCopied={()=>void load()}/>
      )}
        </div>
      {detail && (
        <div className="workbench-detail">
        <div className="detail-pane">
          {kind==='parties'&&<PartyLedger partyId={detail.id} user={user} openRecord={openRecord}/>}
          <div className="section-heading">
            <h2>
              {detail.reference ||
                detail.receipt ||
                detail.name ||
                detail.username ||
                t("单据详情", "Record details")}
            </h2>
            <button
              onClick={() => {
                setDetail(null);
                setAction(null);
                setAux(null);
              }}
            >
              <ArrowLeft size={16} />
              {t("收起详情", "Close details")}
            </button>
          </div>
          <div className="waybill">
            <div className="waybill-main">
              <span className="waybill-kicker">{t("MC STAR 货运记录", "MC STAR cargo record")}</span>
              <strong className="waybill-id">
                {detail.reference || detail.receipt || detail.name || detail.username || detail.id}
              </strong>
            </div>
            <span className={`waybill-state state-${String(detail.state || "unknown").toLowerCase()}`}>
              {detail.state || t("未标记", "Unmarked")}
            </span>
          </div>
          {kind === "tickets" && <RouteRail detail={detail} />}
          <div className="tabs">
            <button
              className={tab === "details" ? "active" : ""}
              onClick={() => {
                setTab("details");
                setAction(null);
              }}
            >
              {t("资料与原始依据", "Details & source")}
            </button>
            {editing && allowed(editing.cap) && (
              <button
                onClick={() => {
                  setTab("edit");
                  setAction(editing);
                }}
              >
                {t("编辑", "Edit")}
              </button>
            )}
            {actionsFor(kind, detail)
              .filter((a) => allowed(a.cap))
              .map((a) => (
                <button
                  key={a.key}
                  className={action?.key === a.key ? "active" : ""}
                  onClick={() => {
                    setTab(a.key);
                    setAction(a);
                  }}
                >
                  {t(...a.title)}
                </button>
              ))}
            {kind==='release' && allowed('documents.notify') && <button onClick={()=>setNotice(detail.id)}>{t('放货通知','Release notice')}</button>}
            {["tickets", "shipments"].includes(kind) && (
              <button
                onClick={async () => {
                  setTab("nodes");
                  setAction(null);
                  setAux(null);
                  if (kind === "tickets")
                    try {
                      setAux(
                        await api("/api/tickets/" + detail.id + "/timeline"),
                      );
                    } catch (e) {
                      setError(e);
                    }
                }}
              >
                {t("物流节点", "Tracking nodes")}
              </button>
            )}
            {["tickets", "receipts"].includes(kind) && (
              <>
                <button
                  onClick={async () => {
                    setTab("holds");
                    setAction(null);
                    try {
                      setAux(
                        await api("/api/" + kind + "/" + detail.id + "/holds"),
                      );
                    } catch (e) {
                      setError(e);
                    }
                  }}
                >
                  {t("厂家控货", "Manufacturer hold")}
                </button>
                <button onClick={() => setNotice(detail.id)}>
                  {t("通知", "Notice")}
                </button>
                <button
                  onClick={async () => {
                    setTab("marks");
                    setAction(null);
                    try {
                      setAux(
                        await api(
                          "/api/" +
                            kind +
                            "/" +
                            detail.id +
                            "/notification-marks?history=true",
                        ),
                      );
                    } catch (e) {
                      setError(e);
                    }
                  }}
                >
                  {t("复制记录", "Copy history")}
                </button>
              </>
            )}
            {kind === "accounts" && (
              <button
                onClick={async () => {
                  setTab("bank");
                  setAction(null);
                  try {
                    setAux(
                      await api(
                        "/api/workspace/bank-entries?account_id=" + detail.id,
                      ),
                    );
                  } catch (e) {
                    setError(e);
                  }
                }}
              >
                {t("流水明细", "Statement entries")}
              </button>
            )}
            {kind==='documents'&&<button onClick={async()=>{setTab('history');setAction(null);try{setAux(await api('/api/packing/documents/'+detail.id+'/history'))}catch(e){setError(e)}}}>{t('替换修订历史','Replacement history')}</button>}
            {kind==='documents'&&allowed('packing.manage')&&<button onClick={()=>{setTab('billing');setAction(null)}}>{t('客户应收分组','Customer billing groups')}</button>}
          </div>
          {action ? (
            <ActionForm key={action.key} action={action} onSaved={update} />
          ) : tab==='billing'? <BillingGroups documentId={detail.id} onSaved={update}/> : tab === "nodes" ? (
            <>
              <Form
                fields={[
                  select(
                    "node",
                    [
                      "packing_created",
                      "packing_sent",
                      "loading",
                      "departed",
                      "arrived_port",
                      "customs_cleared",
                      "warehouse_arrived",
                    ],
                    true,
                  ),
                  select("mode", ["manual", "follow"], true),
                  f("occurred_at", "datetime-local"),
                  lookup("destination_warehouse_id", "warehouses"),
                  required("source"),
                ]}
                initial={{ expected_version: detail.version, mode: "manual" }}
                submit={(b, k) =>
                  api(
                    "/api/tracking/nodes",
                    {
                      ...b,
                      target_type: kind === "shipments" ? "shipment" : "ticket",
                      target_id: detail.id,
                      ...(b.occurred_at
                        ? { occurred_at: new Date(b.occurred_at).toISOString() }
                        : {}),
                    },
                    "POST",
                    k,
                  )
                }
                onSaved={update}
              />
              {aux && <RecordView data={aux} />}
            </>
          ) : tab === "holds" ? (
            aux&&<HoldReview data={aux} onSaved={async r=>{await update(r);setAux(await api('/api/'+kind+'/'+detail.id+'/holds'))}}/>
          ) : tab==='marks' ? <CopyMarks marks={rows(aux)} canCancel={allowed('documents.notify')} onSaved={async r=>{await update(r);setAux(await api('/api/'+kind+'/'+detail.id+'/notification-marks?history=true'))}}/> : tab==='bank' ? <BankEntries key={detail.id} accountId={detail.id}/> : aux ? (
            <RecordView data={aux} />
          ) : (
            <>
              <RecordView data={detail} links onLink={openRecord} />
              {kind==='documents'&&(detail.revisions||[]).filter((r:Row)=>r.batch_id).map((revision:Row)=><details key={revision.id}><summary>{t('查看此次导入原件','View this import original')} · {revision.created_at}</summary><FileViewer path={'/api/packing/documents/'+detail.id+'/original/'+revision.batch_id}/></details>)}
              {attachments.map((a: Row, i: number) => (
                <details key={a.id || i}>
                  <summary>
                    {t("查看原件", "View original")} {i + 1}
                  </summary>
                  <FileViewer
                    path={
                      kind === 'mbl'? '/api/mbl/'+detail.id+'/files/'+a.attachment_id : kind === "evidence"
                        ? "/api/finance/files/" + a.attachment_id
                        : "/api/" +
                          kind +
                          "/" +
                          detail.id +
                          "/attachments/" +
                          (a.attachment_id || a.id)
                    }
                    name={a.filename}
                  />
                </details>
              ))}
            </>
          )}
        </div>
        </div>
      )}
      </div>
    </section>
  );
}
