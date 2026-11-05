import { useContext, useEffect, useRef, useState } from "react";
import { api, download, fileBlob, rows, ApiError, type Row } from "./api";
import { ErrorBox, Form, RecordView, FileViewer, f, type Field } from "./ui";
import { LangContext, useT } from "./i18n";
import {ExportJob} from './ExportJob';
import {copyCurrentPNG} from './clipboard';
import {NativeArrivalForm,RecoverDocument} from './NativeSources';
import {GrantsForm,PickupForm} from './WorkflowForms';
export {CopyMarks} from './WorkflowForms';
import {
  lookup,
  select,
  required,
  specs,
  components,
  fx,
  paymentFields,
  evidenceFields,
  versions,
  clean,
} from "./catalog";

export type Action = {
  key: string;
  title: [string, string];
  cap: string;
  fields: Field[];
  initial?: Row;
  run: (body: Row, key: string) => Promise<Row>;
  preview?: (body: Row) => Promise<Row>;
  load?: () => Promise<Row>;
  record?: Row;
  entityKind?:string;
};
export function ActionForm({
  action,
  onSaved,
}: {
  action: Action;
  onSaved: (r: Row) => void;
}) {
  if (action.key === 'grants') return <GrantsForm action={action} onSaved={onSaved}/>;
  if (action.key === 'physical') return <PickupForm action={action} onSaved={onSaved}/>;
  if (action.entityKind==='documents'&&action.key==='recover')return <RecoverDocument action={action} onSaved={onSaved}/>;
  if (action.record?.native_receipt_id&&['arrive','sea-arrive','arrival-sources'].includes(action.key))return <NativeArrivalForm action={action} onSaved={onSaved}/>;
  return <CommandForm action={action} onSaved={onSaved}/>;
}
function CommandForm({action,onSaved}:{action:Action;onSaved:(r:Row)=>void}) {
  const t = useT();
  const [pending, setPending] = useState<{ body: Row; preview: Row } | null>(
      null,
    ),
    [result, setResult] = useState<Row | null>(null),
    [error, setError] = useState<any>(),
    [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());
  return (
    <section className="workflow">
      <Form
        key={action.key}
        title={t(...action.title)}
        fields={action.fields}
        initial={action.initial}
        submitLabel={
          action.preview ? ["核对变更预览", "Review change preview"] : undefined
        }
        successLabel={action.preview?['预览已就绪，请确认后执行','Preview ready; confirm to apply']:undefined}
        submit={async (body, k) => {
          if (action.preview) {
            const preview = await action.preview(clean(body));
            setPending({ body: clean(body), preview });
            return {};
          }
          const r = await action.run(clean(body), k);
          setResult(r);
          onSaved(r);
          return r;
        }}
      />
      {pending && (
        <section className="confirmation">
          <h3>
            {t("确认前请核对全部影响", "Review all effects before confirming")}
          </h3>
          <RecordView data={pending.preview} />
          <ErrorBox error={error} />
          <button
            className="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const r = await action.run(
                  { ...pending.body, preview: pending.preview },
                  key.current,
                );
                setResult(r);
                setPending(null);
                key.current = crypto.randomUUID();
                onSaved(r);
              } catch (e) {
                setError(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            {t("确认执行上述变更", "Confirm these changes")}
          </button>
        </section>
      )}
      {result && (
        <section>
          <h3>{t("服务端结果", "Saved result")}</h3>
          <RecordView data={result} />
          {action.key === 'translate' && <TranslationReview initial={result} onSaved={onSaved}/>}
          {result.export_id&&<ExportJob identity={result.export_id}/>}
        </section>
      )}
    </section>
  );
}

function TranslationReview({initial,onSaved}:{initial:Row;onSaved:(row:Row)=>void}) {
  const t=useT(),[candidate,setCandidate]=useState(initial),[error,setError]=useState<any>();
  const refresh=async()=>{try{setCandidate(await api('/api/translations/'+initial.id))}catch(e){setError(e)}};
  useEffect(()=>{if(!['pending','running'].includes(candidate.status))return;const timer=setInterval(()=>void refresh(),2000);return()=>clearInterval(timer)},[candidate.id,candidate.status]);
  return <section><ErrorBox error={error}/><RecordView data={candidate}/>
    <button onClick={()=>void refresh()}>{t('刷新英文候选','Refresh English candidate')}</button>
    {candidate.status==='unavailable'&&<p>{t('翻译模型未启用。可在入库资料中人工填写英文品名。','Translation is disabled. Enter the English description in receipt details.')}</p>}
    {candidate.status==='ready'&&<Form initial={{expected_version:candidate.version,receipt_version:candidate.source_version}} fields={[]} submitLabel={['确认采用此英文候选','Accept this English candidate']} submit={async(b,k)=>{const r=await api('/api/translations/'+candidate.id+'/accept',b,'POST',k);setCandidate(r);onSaved(r);return r}}/>}
  </section>
}

export function actionsFor(kind: string, row: Row): Action[] {
  const v = { expected_version: row.version };
  const send =
    (suffix: string, method = "POST") =>
    (body: Row, key: string) =>
      api("/api/" + kind + "/" + row.id + suffix, body, method, key);
  const action = (
    key: string,
    title: [string, string],
    cap: string,
    fields: Field[],
    run: Action["run"],
    initial: Row = v,
    preview?: Action["preview"],
  ): Action => ({ key, title, cap, fields, run, initial, preview });
  const result: Action[] = [];
  if (kind === "receipts") {
    result.push(
      action(
        "review",
        ["主管复核", "Supervisor review"],
        "cargo.review",
        [select("decision", ["approved", "rejected"], true), f("comment")],
        send("/review"),
      ),
      action(
        "repack",
        ["改包 / 压包", "Repack cargo"],
        "cargo.manage",
        [
          f("specs", "rows", { fields: specs }),
          required("occurred_on", "date"),
          required("reason"),
        ],
        send("/repack"),
        {
          ...v,
          specs: row.specs?.map((r: Row) =>
            Object.fromEntries(specs.map((f) => [f.key, r[f.key]])),
          ),
        },
      ),
      action(
        "merge",
        ["合并来源收据", "Merge source receipts"],
        "cargo.manage",
        [
          f("sources", "rows", {
            fields: [lookup("receipt_id", "receipts", true)],
          }),
          required("occurred_on", "date"),
          required("reason"),
        ],
        async (b, k) =>
          send("/merge")(
            {
              ...b,
              sources: await versions(
                b.sources,
                "receipt_id",
                "/api/receipts/",
              ),
            },
            k,
          ),
      ),
      action(
        "state",
        ["作废 / 恢复", "Void / recover receipt"],
        "cargo.manage",
        [select("state", ["active", "void"], true), required("reason")],
        send("/state"),
      ),
      action(
        "quote",
        ["确认收费依据", "Confirm rate basis"],
        "pricing.manage",
        [
          f("manual", "group", {
            fields: [
              select("unit", ["KG", "CBM", "PCS"]),
              select("currency", ["USD", "NGN"]),
              f("unit_price"),
              f("source"),
            ],
          }),
          fx,
        ],
        send("/quote"),
      ),
      action(
        "attach",
        ["绑定货堆照片", "Attach cargo photo"],
        "cargo.manage",
        [f("attachment_id", "file", { required: true })],
        send("/attachments"),
      ),
      action(
        "translate",
        ["生成英文候选", "Request English candidate"],
        "cargo.manage",
        [],
        send("/translation"),
      ),
    );
  }
  if (kind === "tickets") {
    if(row.native_receipt_id&&row.arrivals?.length)result.push(action('arrival-sources',['核对历史到货出库来源','Reconcile historic arrival outbound sources'],'packing.arrive',[
      {...select('arrival_id',row.arrivals.map((r:Row)=>r.id),true),optionLabels:Object.fromEntries(row.arrivals.map((r:Row)=>[r.id,[r.occurred_on+' · '+r.pieces+' PCS',r.occurred_on+' · '+r.pieces+' PCS']]))},required('reason')
    ],send('/arrival-sources'),v));
    result.push(
      action(
        "review",
        ["主管复核", "Supervisor review"],
        "packing.review",
        [select("decision", ["approved", "rejected"], true)],
        send("/review"),
      ),
      action(
        "attach",
        ["绑定原件", "Attach original"],
        "packing.manage",
        [f("attachment_id", "file", { required: true })],
        send("/attachments"),
      ),
      action(
        "void",
        ["作废此票", "Void this ticket"],
        "packing.manage",
        [required("reason")],
        send("/void"),
        v,
        () => api("/api/tickets/" + row.id + "/void-preview"),
      ),
    );
    if (["AIR", "EXPRESS"].includes(row.transport))
      result.push(
        action(
          "arrive",
          ["空运 / EXPRESS分批到货", "AIR / EXPRESS batch arrival"],
          "packing.arrive",
          [
            required("pieces", "number"),
            required("kg"),
            required("cbm"),
            required("occurred_on", "date"),
            required("source"),
          ],
          send("/arrive"),
          { ...v, mode: "air" },
        ),
      );
    if (row.transport === 'SEA') result.push(action('sea-arrive',['海运港口 / 计费到货','SEA port / billing arrival'],'packing.arrive',[
      required('pieces','number'),f('kg','text',{help:['仅非KG计费且原件未提供重量时保留未知。','Keep unknown weight only when the source omits it and all charges use non-KG units.']}),required('cbm'),required('occurred_on','date'),required('source')
    ],send('/arrive'),{...v,mode:'sea',kg:row.kg===null?null:undefined}));
    result.push(
      action(
        "release",
        ["确认放货指令", "Confirm release instruction"],
        "release.issue",
        [
          select("mode", ["paid", "credit", "waiver"], true),
          f("promise_on", "date"),
          f("guarantor"),
          f("reason"),
          { ...lookup("ticket_ids", "tickets", true), multi: true },
        ],
        async (b, k) => {
          const members = await Promise.all(
            b.ticket_ids.map(async (id: string) => ({
              ticket_id: id,
              expected_version: (await api("/api/tickets/" + id)).version,
            })),
          );
          const { ticket_ids, ...body } = b;
          return api("/api/release-orders", { ...body, members }, "POST", k);
        },
        { ticket_ids: [row.id] },
      ),
    );
  }
  if (kind === "documents") {
    result.push(
      action(
        "split",
        ["拆分选定票件", "Split selected tickets"],
        "packing.manage",
        [
          { ...lookup("ticket_ids", "tickets", true), multi: true },
          required("reference"),
        ],
        (b, k) =>
          api("/api/packing/documents/" + row.id + "/split", b, "POST", k),
      ),
      ...(["void", "recover"] as const).map((mode) =>
        action(
          mode,
          mode === "void"
            ? ["作废整份箱单", "Void document"]
            : ["恢复整份箱单", "Recover document"],
          "packing.manage",
          [required("reason")],
          (b, k) =>
            api("/api/packing/documents/" + row.id + "/" + mode, b, "POST", k),
          v,
          () =>
            api("/api/packing/documents/" + row.id + "/" + mode + "-preview"),
        ),
      ),
    );
  }
  if (["invoices", "payables"].includes(kind)) {
    const path = "/api/invoices/" + row.id;
    result.push(
      action(
        "discount",
        ["减免", "Discount"],
        "finance.discount",
        [required("amount_ngn"), f("reason"), f("waiver", "bool")],
        (b, k) => api(path + "/discounts", b, "POST", k),
      ),
      action(
        "adjust",
        ["调整账单", "Adjust invoice"],
        kind === "payables" ? "payables.manage" : "finance.adjust",
        [required("amount_ngn"), f("discount_total_ngn"), required("reason")],
        (b, k) => api(path + "/adjust", b, "POST", k),
        v,
        (b) =>
          api(
            path +
              "/adjust-preview?amount_ngn=" +
              encodeURIComponent(b.amount_ngn) +
              (b.discount_total_ngn
                ? "&discount_total_ngn=" +
                  encodeURIComponent(b.discount_total_ngn)
                : ""),
          ),
      ),
      action(
        "register",
        ["登记付款凭证（不计入实收）", "Register proof (no cash posting)"],
        "release.register",
        [
          lookup("payment_id", "payments"),
          lookup("case_id", "evidence"),
          required("source"),
        ],
        async (b, k) => {
          const source = await api(
            "/api/" +
              (b.payment_id
                ? "payments/" + b.payment_id
                : "evidence/" + b.case_id),
          );
          return api(
            "/api/payment-registrations",
            { ...b, invoice_id: row.id, source_version: source.version },
            "POST",
            k,
          );
        },
      ),
    );
  }
  if (kind === "payments") {
    result.push(
      action(
        "post",
        ["审核与核销分配", "Approve & allocate"],
        "finance.post",
        [
          select("approval", ["manual", "bank"], true),
          lookup("case_id", "evidence"),
          f("allocations", "rows", {
            fields: [
              lookup("invoice_id", "invoices", true),
              required("amount_ngn"),
            ],
          }),
          f("cross_party", "group", {
            fields: [
              f("source"),
              f("beneficiaries", "rows", {
                fields: [
                  lookup("invoice_id", "invoices"),
                  lookup("party_id", "parties"),
                ],
              }),
            ],
          }),
        ],
        async (b, k) => {
          const allocations = await versions(
            b.allocations,
            "invoice_id",
            "/api/invoices/",
          );
          return send("/post")(
            {
              ...b,
              allocations,
              ...(b.case_id
                ? {
                    case_version: (await api("/api/evidence/" + b.case_id))
                      .version,
                  }
                : {}),
            },
            k,
          );
        },
        v,
        async (b) => {
          const invoices = await Promise.all(
            (b.allocations || []).map((a: Row) =>
              api("/api/invoices/" + a.invoice_id),
            ),
          );
          return {
            payment: await api("/api/payments/" + row.id),
            case: b.case_id ? await api('/api/evidence/'+b.case_id) : null,
            invoices,
            allocations: b.allocations,
            approval: b.approval,
          };
        },
      ),
    );
    // Preview is displayed, not submitted as an unsupported extra field.
    const post = result[result.length - 1];
    const submit = post.run;
    post.run = (b, k) => {
      const { preview, ...body } = b;
      return api('/api/payments/'+row.id+'/post',{
        ...body,
        allocations:(body.allocations||[]).map((a:Row)=>({...a,expected_version:preview.invoices.find((i:Row)=>i.id===a.invoice_id).version})),
        ...(body.case_id?{case_version:preview.case.version}:{}),
      },'POST',k);
    };
    result.push(
      action(
        "bank-evidence",
        ["补充银行核验", "Supplement bank evidence"],
        "finance.post",
        [lookup("case_id", "evidence", true)],
        async (b, k) =>
          send("/bank-evidence")(
            {
              ...b,
              case_version: (await api("/api/evidence/" + b.case_id)).version,
            },
            k,
          ),
      ),
      action(
        "reverse",
        ["冲正付款", "Reverse payment"],
        "finance.reverse",
        [
          required("reason"),
          f("confirmed", "bool", { required: true }),
          select("claim_resolution", ["disputed", "released"]),
        ],
        send("/reverse"),
      ),
    );
  }
  if (kind === "evidence") {
    result.push(
      action('source-assessment',['核对补证来源与银行依据','Assess supplementary source and bank basis'],'evidence.review',[
        f('attachment_id','file',{required:true}), f('message_id'), required('source'),
        select('verdict',['corroborates','contradicts','unknown'],true),required('reason')
      ],async(b,k)=>api('/api/evidence/'+row.id+'/images',{expected_version:b.expected_version,images:[{attachment_id:b.attachment_id,source:b.source,...(b.message_id?{message_id:b.message_id}:{}),source_assessment:{verdict:b.verdict,reason:b.reason,fields:b.preview.fields,source_fingerprint:b.preview.source_fingerprint}}]},'POST',k),v,
      b=>api('/api/evidence/'+row.id+'/source-assessment-preview?'+new URLSearchParams({attachment_id:b.attachment_id,...(b.message_id?{message_id:b.message_id}:{})}))),
      action(
        "review",
        ["人工核对水单", "Review evidence"],
        "evidence.review",
        [
          select("action", ["correct", "defer", "retract", "reject"], true),
          f("fields", "group", { fields: evidenceFields }),
          f("reason"),
        ],
        send("/review"),
        {
          ...v,
          fields:
            row.fields ||
            Object.fromEntries(evidenceFields.map((f) => [f.key, row[f.key]])),
        },
      ),
      action(
        "images",
        ["补充多张原图", "Add original images"],
        "evidence.write",
        [
          f("images", "rows", {
            fields: [
              f("attachment_id", "file", { required: true }),
              f("source"),
            ],
          }),
        ],
        send("/images"),
      ),
      action(
        "account",
        ["选择核对银行账户", "Bind bank account"],
        "evidence.review",
        [lookup("account_id", "accounts"), required("reason")],
        (b, k) =>
          send("/bank-account")({ ...b, account_id: b.account_id || null }, k),
      ),
      action(
        "recognize",
        ["识别候选", "Recognize candidates"],
        "evidence.recognize",
        [],
        send("/recognize"),
      ),
      action(
        "match",
        ["确定性银行核对", "Compare bank candidates"],
        "bank.review",
        [],
        (b, k) => api("/api/bank/cases/" + row.id + "/match", b, "POST", k),
      ),
      action(
        "bank-review",
        ["确认银行证据", "Confirm bank evidence"],
        "bank.review",
        [
          select(
            "action",
            [
              "confirm",
              "confirm_manual",
              "defer",
              "retract",
              "release",
              "resolve",
              "resolve_manual",
            ],
            true,
          ),
          lookup("entry_id", "entries"),
          f("reason"),
        ],
        async (b, k) =>
          api(
            "/api/bank/cases/" + row.id + "/review",
            {
              ...b,
              ...(b.entry_id
                ? {
                    entry_version: (
                      await api("/api/workspace/bank-entry/" + b.entry_id)
                    ).version,
                  }
                : {}),
            },
            "POST",
            k,
          ),
      ),
    );
  }
  if (kind === "parties" || kind === "accounts")
    result.push(
      action(
        "membership",
        ["维护仓库参与范围", "Warehouse participation"],
        kind === "parties" ? "parties.manage" : "bank.manage",
        [
          lookup("warehouse_id", "warehouses", true),
          f("active", "bool", { default: true }),
          required("source"),
        ],
        (b, k) =>
          api(
            "/api/" +
              (kind === "parties" ? "parties/" : "bank/accounts/") +
              row.id +
              "/warehouses",
            b,
            "POST",
            k,
          ),
      ),
    );
  if (kind === "users")
    result.push(
      {...action(
        "grants",
        ["分配权限与仓库", "Assign capabilities & warehouses"],
        "users.manage",
        [
          f("capabilities", "strings", {
            help: [
              "每行一项权限；星号代表完整管理员。",
              "One capability per line; * grants full administrator access.",
            ],
          }),
          { ...lookup("warehouse_ids", "warehouses"), multi: true },
        ],
        (b, k) =>
          api(
            "/api/admin/users/" + row.id + "/grants",
            {
              expected_version: b.expected_version,
              capabilities: b.capabilities || [],
              warehouse_ids: b.warehouse_ids || [],
            },
            "PUT",
            k,
          ),
      ), initial:{...v,user_id:row.id},load:()=>api('/api/admin/users/'+row.id+'/grants')},
    );
  if (kind === "connections")
    result.push(
      action(
        "operation",
        ["连接诊断 / 控制", "Connection diagnostics / control"],
        "connections.manage",
        [
          select(
            "kind",
            ["inspect", "diagnose", "qr", "connect", "recover"],
            true,
          ),
        ],
        send("/operations"),
      ),
    );
  if (kind === "messages")
    result.push(
      action(
        "origin",
        ["复核消息来源", "Review message origin"],
        "channels.review",
        [select("decision", ["human", "bot"], true), required("reason")],
        (b, k) =>
          api("/api/channels/messages/" + row.id + "/origin", b, "POST", k),
      ),
    );
  if (kind === "release")
    result.push(
      action(
        "physical",
        ["记录实际提货", "Record physical pickup"],
        "release.physical",
        [
          lookup("ticket_id", "tickets", true),
          required("pieces", "number"),
          f("kg"),
          required("cbm"),
          required("occurred_at", "datetime-local"),
          f("photo_ids", "file", { multi: true, required: true }),
        ],
        (b, k) =>
          api(
            "/api/physical-releases",
            { ...b, occurred_at: new Date(b.occurred_at).toISOString() },
            "POST",
            k,
          ),
        { ...v, order_id: row.id },
      ),
    );
  if (kind === "mbl")
    result.push(
      action(
        "original",
        ["上传原始单证", "Attach original document"],
        "documents.manage",
        [
          select("kind", ["BO", "SI", "SO", "DN"], true),
          f("attachment_id", "file", { required: true }),
        ],
        send("/attachments"),
      ),
      action(
        "export",
        ["按原模板生成单证", "Generate from original template"],
        "documents.export",
        [select("kind", ["BO", "SI", "SO", "DN"], true)],
        send("/exports"),
      ),
    );
  if (kind === "legacy-parties")
    result.push(
      action(
        "resolve",
        ["确认客户归属", "Confirm customer ownership"],
        "legacy.resolve",
        [lookup("warehouse_id", "warehouses", true), required("source")],
        (b, k) =>
          api("/api/legacy/parties/" + row.id + "/resolve", b, "POST", k),
      ),
    );
  if (kind === "legacy-shipments")
    result.push(
      action(
        "resolve",
        ["确认出运归属", "Confirm shipment ownership"],
        "legacy.resolve",
        [
          lookup("warehouse_id", "warehouses", true),
          required("uploaded_on", "date"),
          f("physical_reference"),
          required("source"),
        ],
        (b, k) =>
          api("/api/legacy/shipments/" + row.id + "/resolve", b, "POST", k),
      ),
    );
  if (kind === "legacy-bills") {
    result.push(
      action(
        "resolve",
        ["确认账款归属及日期", "Confirm bill ownership & dates"],
        "legacy.resolve",
        [
          lookup("party_id", "parties", true),
          lookup("warehouse_id", "warehouses", true),
          ...(row.kind === "AR"
            ? [
                required("uploaded_on", "date"),
                f("due_on", "date"),
                f("dates_source"),
              ]
            : [f("period")]),
          required("source"),
        ],
        (b, k) => api("/api/legacy/bills/" + row.id + "/resolve", b, "POST", k),
      ),
      action(
        "members",
        ["确认完整来源关系", "Confirm complete source membership"],
        "legacy.resolve",
        [
          { ...lookup("ticket_ids", "legacy-tickets"), multi: true },
          f("standalone", "bool"),
          lookup("shipment_id", "legacy-shipments"),
          required("source"),
          required("reason"),
        ],
        async (b, k) => {
          const member_versions = Object.fromEntries(
            await Promise.all(
              (b.ticket_ids || []).map(async (id: string) => [
                id,
                (await api("/api/legacy/tickets/" + id)).version,
              ]),
            ),
          );
          return api(
            "/api/legacy/bills/" + row.id + "/members",
            {
              ...b,
              ticket_ids: b.ticket_ids || [],
              member_versions,
              ...(b.shipment_id
                ? {
                    shipment_version: (
                      await api("/api/legacy/shipments/" + b.shipment_id)
                    ).version,
                  }
                : {}),
            },
            "POST",
            k,
          );
        },
      ),
    );
    if (row.native_id && row.invoice) {
      if (row.kind === "AP")
        result.push(
          action(
            "period",
            ["确认应付结算周期", "Confirm payable period"],
            "legacy.resolve",
            [required("period"), required("source")],
            (b, k) =>
              api("/api/legacy/bills/" + row.id + "/period", b, "POST", k),
            { ...v, invoice_version: row.invoice.version },
          ),
        );
      const groupFields = [
        select("action", ["revise", "void", "recover"], true),
        f("members", "rows", {
          fields: [
            lookup("ticket_id", "tickets", true),
            f("changes", "group", {
              fields: [
                f("pieces", "number"),
                f("kg"),
                f("cbm"),
                f("freight_usd"),
                f("clearing_ngn"),
                f("ngn_per_usd"),
                f("charge_components", "rows", { fields: components }),
                f("destination"),
                f("contact_raw"),
              ],
            }),
          ],
        }),
        f("amount_ngn"),
        f("discount_total_ngn"),
        f("discount_source"),
        lookup('party_id','parties'),
        f('owner_evidence','group',{fields:[lookup('old_party_id','parties'),lookup('new_party_id','parties'),{...lookup('warehouse_ids','warehouses'),multi:true},f('source')]}),
        f("refund_new_allocations", "bool"),
        required("source"),
        required("reason"),
      ];
      const prepare = async (b: Row) => {
        const result: Row={...b,members:await versions(b.members || [],'ticket_id','/api/tickets/')};
        if(b.owner_evidence?.old_party_id && b.owner_evidence?.new_party_id) {
          const old=await api('/api/parties/'+b.owner_evidence.old_party_id),next=await api('/api/parties/'+b.owner_evidence.new_party_id);
          result.owner_evidence={...b.owner_evidence,old_party_version:old.version,new_party_version:next.version};
        }else delete result.owner_evidence;
        return result;
      };
      result.push(
        action(
          "group",
          [
            "整组账单修订 / 作废 / 恢复",
            "Revise / void / recover whole bill group",
          ],
          "legacy.resolve",
          groupFields,
          async (b, k) =>
            api(
              "/api/legacy/bills/" + row.id + "/group-confirm",
              await prepare(b),
              "POST",
              k,
            ),
          {
            ...v,
            invoice_version: row.invoice.version,
            application_version: row.application?.version,
            members: [],
          },
          async (b) =>
            api(
              "/api/legacy/bills/" + row.id + "/group-preview",
              await prepare(b),
            ),
        ),
      );
    }
  }
  return result.map(a=>({...a,record:row,entityKind:kind}));
}

export function Notice({
  kind,
  identity,
  onClose,
  onCopied,
}: {
  kind: string;
  identity: string;
  onClose: () => void;
  onCopied?: () => void;
}) {
  const t = useT();
  const [notice, setNotice] = useState<Row>(),
    [blob, setBlob] = useState<Blob>(),
    [error, setError] = useState<any>(),
    [state, setState] = useState(""),
    [markerPending, setMarkerPending] = useState(false),
    [choice, setChoice] = useState(
      kind === "receipts"
        ? "receipt"
        : kind === "release"
          ? "release"
          : "loading",
    );
  const token = useRef(crypto.randomUUID());
  const prepare = async () => {
    setError(null);
    setBlob(undefined);
    setState("");
    setNotice(undefined); setMarkerPending(false);
    try {
      const payload =
        kind === "release"
          ? { kind: "release", order_id: identity, ticket_ids: (await api('/api/release-orders/'+identity+'/eligibility')).members.map((m: Row)=>m.ticket_id) }
          : kind === "receipts"
            ? { kind: choice, receipt_ids: [identity] }
            : {
                kind: choice,
                entry: choice === "loading" ? "packing-row" : undefined,
                ticket_ids: [identity],
              };
      const n = await api("/api/notifications/render", payload);
      const png = await fileBlob("/api/notifications/" + n.id + "/image");
      if (png.type !== "image/png" || png.size < 8)
        throw new ApiError(422, "invalid_image", null);
      setNotice(n);
      setBlob(png);
      token.current = crypto.randomUUID();
    } catch (e) {
      setError(e);
    }
  };
  useEffect(() => {
    void prepare();
  }, [identity, choice]);
  const saveMarker = async () => {
    if (!notice) return;
    try {
      await api('/api/notifications/'+notice.id+'/copy-events', {expected_version:notice.version,clipboard_success:true}, 'POST', token.current);
      setMarkerPending(false); setError(null);
      setState(t('已复制并保存标记；尚未投递。','Copied and recorded; not delivered.'));
      onCopied?.();
    } catch(e) { setError(e); setMarkerPending(true); setState(t('内容已复制，标记尚未保存。可重试标记。','Content copied; marker not saved. Retry the marker.')); }
  };
  const copy = async (image: boolean) => {
    if (!notice || !blob) return;
    setError(null);
    try {
      await api('/api/notifications/'+notice.id+'/current');
      if (image)
        await navigator.clipboard.write([
          new ClipboardItem({ "image/png": blob }),
        ]);
      else await navigator.clipboard.writeText(notice.text);
      setState(t("已复制，正在保存标记…", "Copied; saving marker…"));
      await saveMarker();
    } catch (e) {
      setError(e);
      setState(
        t(
          "复制失败，请保留当前内容并检查剪贴板权限。",
          "Copy failed. Keep this content and check clipboard permissions.",
        ),
      );
    }
  };
  return (
    <section className="notice-panel">
      <div className="section-heading">
        <h2>{t("通知文字与图片", "Notification text & image")}</h2>
        <button onClick={onClose}>{t("关闭", "Close")}</button>
      </div>
      {kind !== "release" && (
        <select
          aria-label={t("通知类型", "Notice type")}
          value={choice}
          onChange={(e) => setChoice(e.target.value)}
        >
          {(kind === "receipts"
            ? ["receipt", "rates"]
            : ["loading", "arrival", "hold", "remove_hold", "rates"]
          ).map((k) => (
            <option key={k} value={k}>
              {
                (
                  {
                    receipt: t("收货通知", "Receipt notice"),
                    rates: t("收费标准", "Rate notice"),
                    loading: t("装柜通知", "Loading notice"),
                    arrival: t("到货通知", "Arrival notice"),
                    hold: t("控货到货通知", "Held cargo notice"),
                    remove_hold: t("解控通知", "Hold removal notice"),
                  } as Row
                )[k]
              }
            </option>
          ))}
        </select>
      )}
      <ErrorBox error={error} />
      {notice && (
        <textarea
          readOnly
          className="notice-text"
          aria-label={t("通知原文", "Notification text")}
          value={notice.text}
        />
      )}
      <div className="toolbar">
        <button disabled={!blob} onClick={() => copy(false)}>
          {t("复制文字", "Copy text")}
        </button>
        <button disabled={!blob} onClick={() => copy(true)}>
          {t("复制图片", "Copy image")}
        </button>
        <button onClick={prepare}>
          {t("重新生成当日通知", "Regenerate for today")}
        </button>
        {markerPending && <button onClick={saveMarker}>{t('重试保存复制标记','Retry copy marker')}</button>}
      </div>
      <p role="status">{state}</p>
    </section>
  );
}

export function ExportPanel({
  kind,
  ids,
  documentId,
  preset = 'packing',
}: {
  kind: "receipts" | "tickets";
  ids: string[];
  documentId?: string;
  preset?: string;
}) {
  const t = useT();
  const [result, setResult] = useState<Row>(),
    [prepared, setPrepared] = useState<{id:string;source:string;blob:Blob}>(),
    [error, setError] = useState<any>();
  const source=JSON.stringify({kind,ids,documentId});
  const active=useRef({epoch:0,id:'',source});
  const currentSource=useRef(source);currentSource.current=source;
  const [copied,setCopied]=useState(false);
  useEffect(()=>{active.current={epoch:active.current.epoch+1,id:'',source};setResult(undefined);setPrepared(undefined);setError(null);setCopied(false);return()=>{active.current.epoch++}},[source]);
  useEffect(() => {
    if (!result?.id || result.ready || ['ready','completed','failed'].includes(result.status)) return;
    let live = true;
    const expected={...active.current};
    const timer = setInterval(
      () =>
        api("/api/exports/" + result.id)
          .then((r) => {
            if (live && active.current.epoch===expected.epoch && active.current.id===expected.id && currentSource.current===expected.source) {
              setResult(r);
            }
          })
          .catch((e) => {
            if (live && active.current.epoch===expected.epoch && active.current.id===expected.id && currentSource.current===expected.source) setError(e);
          }),
      1500,
    );
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [result?.id, result?.status]);
  useEffect(()=>{
    setPrepared(undefined);
    if(!result||result.format!=='png'||!(result.ready||['ready','completed'].includes(result.status)))return;
    let live=true;const expected={...active.current};
    fileBlob('/api/exports/'+result.id+'/clipboard').then(blob=>{
      if(live&&active.current.epoch===expected.epoch&&active.current.id===result.id&&currentSource.current===expected.source){setPrepared({id:result.id,source:expected.source,blob});setError(null)}
    }).catch(e=>{if(live&&active.current.epoch===expected.epoch){setPrepared(undefined);setError(e)}});
    return()=>{live=false};
  },[result?.id,result?.status,result?.format,source]);
  return (
    <section>
      <Form
        title={t("导出与标签", "Export & labels")}
        fields={[
          select(
            "kind",
            ["packing", "label_fee", "label_plain", "label_confirmation"],
            true,
          ),
          select("format", ["xlsx", "pdf", "png"], true),
        ]}
        initial={{ kind: preset, format: preset==='packing'?'xlsx':'png' }}
        submit={async (b, k) => {
          const expected={epoch:active.current.epoch+1,id:'',source};active.current=expected;
          setPrepared(undefined);setResult(undefined);setError(null);setCopied(false);
          const r = await api(
            "/api/exports",
            {
              ...b,
              ...(documentId
                ? { document_id: documentId }
                : kind === "receipts"
                  ? { receipt_ids: ids }
                  : { ticket_ids: ids }),
            },
            "POST",
            k,
          );
          if(active.current.epoch===expected.epoch&&currentSource.current===expected.source){active.current={...expected,id:r.id};setResult(r)}
          return r;
        }}
      />
      <ErrorBox error={error} />
      {result && (
        <>
          <RecordView data={result} />
          <div className="toolbar">
            <button
              onClick={async () => {
                const expected={...active.current};
                try {
                  const refreshed=await api("/api/exports/" + result.id);
                  if(active.current.epoch===expected.epoch&&active.current.id===expected.id&&currentSource.current===expected.source)setResult(refreshed);
                } catch (e) {
                  if(active.current.epoch===expected.epoch&&active.current.id===expected.id&&currentSource.current===expected.source)setError(e);
                }
              }}
            >
              {t("刷新任务状态", "Refresh job")}
            </button>
            {(result.status === "ready" ||
              result.ready ||
              result.status === "completed") && (
              <>
                <button
                  onClick={() =>
                    download(
                      "/api/exports/" + result.id + "/file",
                      "MCSTAR." + (result.format || "xlsx"),
                    ).catch(setError)
                  }
                >
                  {t("下载文件", "Download file")}
                </button>
                <button
                  disabled={!prepared||prepared.id!==result.id||prepared.source!==source||result.format!=='png'}
                  onClick={async () => {
                    const expected={...active.current};
                    setError(null);setCopied(false);
                    try {
                      // Construct ClipboardItem during the user gesture; the promise
                      // obtains fresh, currently authorized bytes for this exact job.
                      await copyCurrentPNG('/api/exports/'+result.id+'/clipboard',()=>active.current.epoch===expected.epoch&&active.current.id===expected.id&&currentSource.current===expected.source);
                      if(active.current.epoch===expected.epoch)setCopied(true);
                    } catch (e) {
                      if(active.current.epoch===expected.epoch){setPrepared(undefined);setError(e)}
                    }
                  }}
                >
                  {t("复制图片", "Copy image")}
                </button>
              </>
            )}
          </div>
          {copied&&<p role="status">{t('已复制当前任务图片','Current job image copied')}</p>}
        </>
      )}
    </section>
  );
}
