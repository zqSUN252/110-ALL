import { f, type Field, type Column } from "./ui";
import { api, type Row } from "./api";
export const modes = ["SEA", "AIR", "EXPRESS", "WAREHOUSE"];
export const lookup = (key: string, kind: string, required = false): Field =>
  f(key, "lookup", { lookup: kind, required });
export const select = (
  key: string,
  options: string[],
  required = false,
): Field => f(key, "select", { options, required });
export const required = (key: string, type: Field["type"] = "text"): Field =>
  f(key, type, { required: true });
export const specs: Field[] = [
  required("length"),
  required("width"),
  required("height"),
  { ...select("unit", ["MM", "CM", "M"], true), default: "CM" },
  required("pieces", "number"),
  required("kg"),
  f("packing"),
];
export const components: Field[] = [
  select("component", ["freight", "clearing"], true),
  select("currency", ["USD", "NGN"], true),
  select("unit", ["PCS", "KG", "CBM"], true),
  required("amount"),
  required("source"),
];
export const fx: Field = f("fx", "group", {
  fields: [
    select("direction", [
      "NATIVE",
      "NGN_PER_USD",
      "NGN_PER_CNY",
      "USD_CNY_CROSS",
    ]),
    f("rate"),
    f("ngn_per_usd"),
    f("cny_per_usd"),
  ],
});
export const receiptFields: Field[] = [
  lookup("warehouse_id", "warehouses", true),
  required("series"),
  required("number", "number"),
  required("received_on", "date"),
  select("transport", modes, true),
  lookup("party_id", "parties"),
  f("customer_phone"),
  f("supplier_phone"),
  f("item_zh"),
  f("item_en"),
  f("destination"),
  lookup("destination_warehouse_id", "warehouses"),
  f("storage_location"),
  select("origin", ["GZ", "HK"]),
  f("goods_class"),
  f("salesperson"),
  f("operator_name"),
  f("courier_company"),
  f("courier_number"),
  f("declaration_required", "bool", { default: true }),
  f("specs", "rows", { fields: specs, default: [{ unit: "CM" }] }),
];
export const ticketFields: Field[] = [
  lookup("document_id", "documents", true),
  required("receipt"),
  f("mark"),
  lookup("party_id", "parties"),
  f("contact_raw"),
  f("phone_normalized"),
  f("phone_confirmed", "bool"),
  f("item_zh"),
  f("item_en"),
  required("pieces", "number"),
  f("kg",'text',{help:['海运非KG计费原件未提供时留空；空运、特快与KG计费须填写。','Leave unknown sea weight blank for non-KG pricing. Air, express and KG pricing require weight.']}),
  required("cbm"),
  f("destination"),
  lookup("destination_warehouse_id", "warehouses"),
  f("address"),
  select("transport", modes),
  select("origin", ["GZ", "HK"]),
  f("goods_class"),
  f("received_on", "date"),
  f("freight_usd"),
  f("clearing_ngn"),
  f("ngn_per_usd"),
  f("charge_components", "rows", { fields: components }),
  lookup("native_receipt_id", "receipts"),
];
export const shipmentFields: Field[] = [
  lookup("warehouse_id", "warehouses", true),
  required("reference"),
  select("transport", modes, true),
  select("origin", ["GZ", "HK"]),
  f("destination"),
  f("clearing_on", "date"),
  lookup("clearing_party_id", "parties"),
  f("clearing_amount_ngn"),
];
export const partyFields: Field[] = [
  lookup("warehouse_id", "warehouses", true),
  required("name"),
  {...select("classification", [
    "客户",
    "供应商",
    "清关代理",
    "报关行",
    "拖车行",
    "订舱代理",
    "目的港杂费",
    "WAYBILL费用",
    "其他",
    "ONITSHA仓",
    "ABA仓",
    "ABUJA仓",
    "PORT HARCOURT仓",
    "LAGOS仓",
  ]),optionLabels:Object.fromEntries([
    ['客户','Customer'],['供应商','Supplier'],['清关代理','Clearance agent'],['报关行','Customs broker'],['拖车行','Haulage company'],['订舱代理','Booking agent'],['目的港杂费','Destination port charges'],['WAYBILL费用','Waybill charges'],['其他','Other'],['ONITSHA仓','ONITSHA warehouse'],['ABA仓','ABA warehouse'],['ABUJA仓','ABUJA warehouse'],['PORT HARCOURT仓','PORT HARCOURT warehouse'],['LAGOS仓','LAGOS warehouse']
  ].map(([zh,en])=>[zh,[zh,en] as [string,string]]))},
  f("phone"),
  f("phone_source"),
  f("address", "textarea"),
  f("source", "textarea"),
];
export const paymentFields: Field[] = [
  lookup("party_id", "parties", true),
  lookup("warehouse_id", "warehouses", true),
  required("amount"),
  select("currency", ["NGN", "USD", "CNY"], true),
  fx,
  f("paid_on", "date"),
];
export const invoiceFields: Field[] = [
  lookup("party_id", "parties", true),
  lookup("warehouse_id", "warehouses", true),
  required("reference"),
  required("amount_ngn"),
  required("uploaded_on", "date"),
  f("charge_usd"),
  f("charge_ngn"),
  f("ngn_per_usd"),
];
export const evidenceFields: Field[] = [
  f("amount"),
  select("currency", ["NGN", "USD", "CNY"]),
  f("transaction_date", "date"),
  f("payer_name"),
  f("transaction_reference"),
  f("recipient_account"),
  f("bank_name"),
  f("business_reference"),
];
export type PageSpec = {
  title: [string, string];
  endpoint: string;
  detail?: string;
  columns: Column[];
  create?: {
    title: [string, string];
    path: string;
    fields: Field[];
    initial?: Row;
    cap: string;
  };
  edit?: Field[];
  cap: string;
  frozen?: number;
  filters?: Field[];
  cursor?: boolean;
  legacy?: string;
};
const cols = (keys: string[]): Column[] => keys.map((key) => ({ key }));
export const pages: Record<string, PageSpec> = {
  receipts: {
    title: ["入库管理", "Warehouse receipts"],
    endpoint: "/api/receipts",
    detail: "/api/receipts/",
    columns: cols([
      "transport",
      "reference",
      "party_id",
      "customer_phone",
      "actions",
      "received_on",
      "item_zh",
      "item_en",
      "pieces",
      "kg",
      "cbm",
      "destination",
      "storage_location",
      "review_state",
      "remaining",
    ]),
    frozen: 4,
    filters: [
      f("receipt"),
      f("client"),
      f("item"),
      f("destination"),
      select("transport", modes),
      lookup("warehouse_id", "warehouses"),
    ],
    cap: "cargo.read",
    create: {
      title: ["新增入库", "New receipt"],
      path: "/api/receipts",
      fields: receiptFields,
      cap: "cargo.manage",
    },
    edit: receiptFields.filter(
      (f) => !["warehouse_id", "series", "number", "specs"].includes(f.key),
    ),
  },
  documents: {
    title: ["箱单文档", "Packing documents"],
    endpoint: "/api/packing/documents",
    detail: "/api/packing/documents/",
    columns: cols(["reference", "shipment_id", "uploaded_on", "state"]),
    cap: "packing.read",
    create: {
      title: ["新建箱单文档", "New packing document"],
      path: "/api/packing/documents",
      fields: [
        lookup("shipment_id", "shipments", true),
        required("reference"),
        required("uploaded_on", "date"),
      ],
      cap: "packing.manage",
    },
  },
  tickets: {
    title: ["PACKING LIST", "PACKING LIST"],
    endpoint: "/api/tickets",
    detail: "/api/tickets/",
    columns: cols([
      "transport",
      "receipt",
      "mark",
      "contact_raw",
      "actions",
      "item_zh",
      "item_en",
      "pieces",
      "kg",
      "cbm",
      "destination",
      "freight_usd",
      "clearing_ngn",
      "pricing_authority",
      "review_state",
    ]),
    frozen: 4,
    filters: [
      f("receipt"),
      f("client"),
      f("item"),
      f("destination"),
      select("transport", modes),
    ],
    cap: "packing.read",
    create: {
      title: ["新增票件", "New ticket"],
      path: "/api/tickets",
      fields: ticketFields,
      cap: "packing.manage",
    },
    edit: [
      ...ticketFields.filter((f) => f.key !== "document_id"),
      select("repricing", ["retain_totals", "quote"]),
      f("manual", "group", {
        fields: [
          select("unit", ["KG", "CBM", "PCS"]),
          select("currency", ["NGN", "USD"]),
          f("unit_price"),
          f("source"),
        ],
      }),
      f("reason"),
    ],
  },
  shipments: {
    title: ["出运管理", "Shipments"],
    endpoint: "/api/shipments",
    detail: "/api/shipments/",
    columns: cols([
      "reference",
      "transport",
      "destination",
      "state",
      "clearing_on",
      "clearing_party_id",
      "clearing_amount_ngn",
    ]),
    filters: [select("transport", modes), f("state"), f("destination")],
    cap: "shipments.read",
    create: {
      title: ["新建出运单", "New shipment"],
      path: "/api/shipments",
      fields: shipmentFields,
      cap: "shipments.manage",
    },
    edit: shipmentFields.filter((f) => f.key !== "warehouse_id"),
  },
  parties: {
    title: ["客户与账单方", "Customers & counterparties"],
    endpoint: "/api/parties",
    detail: "/api/parties/",
    columns: cols(["name", "phone", "classification", "address", "source"]),
    cap: "parties.read",
    filters: [lookup("warehouse_id", "warehouses", true), f("query")],
    create: {
      title: ["新增单位", "New counterparty"],
      path: "/api/parties",
      fields: partyFields,
      cap: "parties.manage",
    },
    edit: partyFields.filter((f) => f.key !== "warehouse_id"),
    cursor: true,
  },
  warehouses: {
    title: ["仓库资料", "Warehouse directory"],
    endpoint: "/api/warehouses",
    detail: "/api/warehouses/",
    columns: cols(["name", "address", "contact", "source"]),
    cap: "warehouses.read",
    create: {
      title: ["新增仓库", "New warehouse"],
      path: "/api/warehouses",
      fields: [
        { ...required("name"), title: ["仓库名称", "Warehouse name"] },
        f("address", "textarea"),
        f("contact", "textarea"),
        f("source"),
      ],
      cap: "warehouses.manage",
    },
    edit: [
      { ...required("name"), title: ["仓库名称", "Warehouse name"] },
      f("address", "textarea"),
      f("contact", "textarea"),
      f("source"),
    ],
  },
  rates: {
    title: ["收费标准", "Rate cards"],
    endpoint: "/api/price-cards",
    detail: "/api/price-cards/",
    columns: cols([
      "transport",
      "origin",
      "goods_class",
      "destination",
      "unit",
      "currency",
      "unit_price",
      "clearing_per_cbm_ngn",
      "valid_from",
      "valid_to",
    ]),
    filters: [f("destination"), f("on", "date")],
    cap: "pricing.read",
    create: {
      title: ["新增价目卡", "New rate card"],
      path: "/api/price-cards",
      fields: [
        select("transport", ["SEA", "AIR", "EXPRESS"], true),
        select("origin", ["GZ", "HK"], true),
        required("goods_class"),
        required("destination"),
        select("unit", ["KG", "CBM", "PCS"], true),
        select("currency", ["USD", "NGN"], true),
        required("unit_price"),
        f("clearing_per_cbm_ngn"),
        required("valid_from", "date"),
        required("valid_to", "date"),
        required("source"),
      ],
      cap: "pricing.manage",
    },
  },
  invoices: {
    title: ["应收账款", "Receivables"],
    endpoint: "/api/workspace/finance",
    detail: "/api/invoices/",
    columns: cols([
      "reference",
      "party_name",
      "destination",
      "pieces",
      "cbm",
      "customer_phone",
      "amount_ngn",
      "historical_received_ngn",
      "allocated_ngn",
      "discount_ngn",
      "outstanding_ngn",
      "due_on",
      "state",
    ]),
    cap: "finance.read",
    cursor: true,
    filters: [
      f("query"),
      {...select('settlement',['unpaid','partial','settled']),title:['付款范围','Payment scope']},
      lookup("warehouse_id", "warehouses"),
      lookup("party_id", "parties"),
    ],
    create: {
      title: ["新建应收", "New receivable"],
      path: "/api/invoices",
      fields: invoiceFields,
      cap: "finance.invoice",
    },
  },
  payments: {
    title: ["收款与核销", "Payments & allocation"],
    endpoint: "/api/finance/records/payments",
    detail: "/api/payments/",
    columns: cols([
      "id",
      "party_id",
      "amount",
      "currency",
      "amount_ngn",
      "allocated_ngn",
      "available_ngn",
      "paid_on",
      "approved_at",
      "state",
      "bank_state",
    ]),
    cap: "finance.read",
    cursor: true,
    filters: [
      lookup("warehouse_id", "warehouses"),
      lookup("party_id", "parties"),
    ],
    create: {
      title: ["登记新收款", "Record new payment"],
      path: "/api/payments",
      fields: paymentFields,
      cap: "finance.payment",
    },
  },
  payables: {
    title: ["按帐单方结算", "Settle by counterparty"],
    endpoint: "/api/workspace/finance",
    detail: "/api/invoices/",
    columns: cols([
      "reference",
      "party_id",
      "period",
      "amount_ngn",
      "historical_received_ngn",
      "allocated_ngn",
      "outstanding_ngn",
    ]),
    cap: "payables.read",
    cursor: true,
    filters: [
      f('query'),f('period'),{...select('settlement',['unpaid','partial','settled']),title:['付款范围','Payment scope']},
      lookup("warehouse_id", "warehouses"),
      lookup("party_id", "parties"),
    ],
    create: {
      title: ["新建应付", "New payable"],
      path: "/api/invoices",
      fields: [...invoiceFields.map(field=>field.key==='uploaded_on'?{...field,title:['入账日期','Accounting date'] as [string,string]}:field), f("period")],
      initial: { kind: "AP" },
      cap: "payables.manage",
    },
  },
  evidence: {
    title: ["水单审核", "Evidence review"],
    endpoint: "/api/finance/records/evidence",
    detail: "/api/evidence/",
    columns: cols([
      "id",
      "business_reference",
      "payer_name",
      "amount",
      "currency",
      "transaction_date",
      "state",
      "account_id",
    ]),
    cap: "evidence.read",
    cursor: true,
    filters: [lookup("warehouse_id", "warehouses")],
    create: {
      title: ["添加水单", "Add evidence"],
      path: "/api/evidence",
      fields: [
        lookup("warehouse_id", "warehouses", true),
        lookup("account_id", "accounts"),
        f("fields", "group", { fields: evidenceFields }),
        f("images", "rows", {
          fields: [f("attachment_id", "file", { required: true }), f("source")],
        }),
      ],
      cap: "evidence.write",
    },
  },
  accounts: {
    title: ["银行流水", "Bank accounts & statements"],
    endpoint: "/api/finance/records/bank-accounts",
    detail: '/api/bank/accounts/',
    columns: cols([
      "bank_name",
      "account_number",
      "currency",
      "scope",
      "revision",
    ]),
    cap: "bank.read",
    filters: [lookup("warehouse_id", "warehouses", true)],
    cursor: true,
    create: {
      title: ["新增银行账户", "New bank account"],
      path: "/api/bank/accounts",
      fields: [
        lookup("warehouse_id", "warehouses", true),
        required("bank_name"),
        required("account_number"),
        select("currency", ["NGN", "USD", "CNY"], true),
      ],
      cap: "bank.manage",
    },
  },
  release: {
    title: ["目的仓实际提货", "Destination pickup"],
    endpoint: "/api/release-orders",
    columns: [{key:"id"}, {key:"eligible",title:["当前指令资格","Current instruction eligibility"]}, {key:"members",title:["授权成员 / 剩余件数","Authorized members / remaining pieces"],render:(row)=>row.members?.map((m:Row)=>`${m.receipt} · ${m.remaining.pieces}`).join(" / ")}],
    cap: "release.read",
  },
  f6: {
    title: ["F6仓储数据分析", "F6 warehouse data analysis"],
    endpoint: "/api/f6",
    columns: cols([
      "receipt",
      "received_on",
      "mark",
      "contact_raw",
      "destination",
      "storage_location",
      "pieces",
      "kg",
      "cbm",
    ]),
    filters: [f('query'),lookup('warehouse_id','warehouses'),{...select('sort',['receipt','received_on','destination','mark']),default:'receipt',title:['排序','Sort by']}],
    cap: "cargo.read",
  },
  mbl: {
    title: ["MBL / BO / SI / SO / DN", "MBL / BO / SI / SO / DN"],
    endpoint: "/api/workspace/mbl",
    detail: "/api/mbl/",
    columns: cols([
      "number",
      "shipment_id",
      "vessel",
      "voyage",
      "port_loading",
      "port_discharge",
      "sailing_on",
    ]),
    cap: "documents.read",
    create: {
      title: ["建立 MBL", "Create MBL"],
      path: "/api/mbl",
      initial: { expected_version: 0 },
      fields: [
        lookup("shipment_id", "shipments", true),
        ...[
          "number",
          "shipper",
          "consignee",
          "notify_party",
          "vessel",
          "voyage",
          "port_loading",
          "port_discharge",
          "final_destination",
          "description",
          "marks",
          "packages",
          "gross_kg",
          "cbm",
          "freight_terms",
          "booking_no",
          "sailing_on",
        ].map((k) =>
          f(
            k,
            k === "sailing_on" ? "date" : k === "packages" ? "number" : "text",
          ),
        ),
      ],
      cap: "documents.manage",
    },
  },
  users: {
    title: ["账号与权限", "Accounts & permissions"],
    endpoint: "/api/admin/users",
    columns: cols(["username", "active", "version"]),
    cap: "users.read",
    create: {
      title: ["新增账号", "New account"],
      path: "/api/admin/users",
      fields: [required("username"), required("password", "password")],
      cap: "users.manage",
    },
    edit: [f("username"), f("active", "bool"), f("password", "password")],
  },
  announcements: {
    title: ["公告", "Announcements"],
    endpoint: "/api/announcements",
    columns: cols(["title_zh", "title_en", "body_zh", "active", "updated_at"]),
    cap: "",
    create: {
      title: ["发布公告", "Publish announcement"],
      path: "/api/announcements",
      fields: [
        required("title_zh"),
        required("title_en"),
        f("body_zh", "textarea"),
        f("body_en", "textarea"),
        f("active", "bool", { default: true }),
      ],
      cap: "announcements.manage",
    },
    edit: [
      f("title_zh"),
      f("title_en"),
      f("body_zh", "textarea"),
      f("body_en", "textarea"),
      f("active", "bool"),
    ],
  },
  audit: {
    title: ["责任追溯", "Audit history"],
    endpoint: "/api/admin/audit",
    columns: cols([
      "created_at",
      "actor_id",
      "action",
      "entity_type",
      "entity_id",
    ]),
    cap: "audit.read",
  },
  connections: {
    title: ["账号连接", "Account connections"],
    endpoint: "/api/connections",
    columns: cols([
      "role",
      "session",
      "status",
      "online",
      "boundary_current",
      "diagnostic",
      "recovery_enabled",
    ]),
    cap: "connections.read",
    create: {
      title: ["登记接入边界", "Register connection boundary"],
      path: "/api/connections",
      initial: { expected_version: 0 },
      fields: [
        select("role", ["main", "assistant", "capture"], true),
        required("session"),
        required("instance"),
        required("generation"),
        f("recovery_enabled", "bool", { default: false }),
        f("alert_actor_id"),
        f("alert_recipient"),
      ],
      cap: "connections.manage",
    },
  },
  messages: {
    title: ["来源消息", "Source messages"],
    endpoint: "/api/channels/messages",
    columns: cols(["id", "state", "received_at", "chat_id", "sender", "text"]),
    cap: "channels.read",
    cursor: true,
  },
  automations: {
    title: ["摘要与复读", "Summary & echo"],
    endpoint: "/api/channels/automations",
    columns: cols(["kind", "source_group", "destination", "paused", "history"]),
    cap: "channels.read",
    create: {
      title: ["配置摘要 / 复读", "Configure summary / echo"],
      path: "/api/channels/automations",
      initial: { expected_version: 0, scope: "live" },
      fields: [
        select("kind", ["summary", "echo"], true),
        lookup("warehouse_id", "warehouses", true),
        required("source_group"),
        required("destination"),
        f("paused", "bool", { default: true }),
      ],
      cap: "channels.manage",
    },
  },
};
for (const [kind, title, columns] of [
  [
    "parties",
    ["来源客户", "Source customers"],
    ["name", "phone", "classification", "native_id"],
  ],
  [
    "shipments",
    ["来源出运单", "Source shipments"],
    [
      "source_no",
      "source_bl",
      "physical_reference",
      "transport",
      "destination",
      "native_id",
    ],
  ],
  [
    "tickets",
    ["来源箱单票件", "Source packing rows"],
    [
      "receipt",
      "consignee",
      "contact",
      "destination",
      "pieces",
      "kg",
      "cbm",
      "freight_usd",
      "clearing_ngn",
      "native_id",
    ],
  ],
  [
    "bills",
    ["来源账款", "Source bill controls"],
    [
      "reference",
      "kind",
      "amount",
      "received",
      "discount",
      "outstanding",
      "relation_state",
      "native_id",
    ],
  ],
  [
    "slips",
    ["历史水单", "Historical slips"],
    ["id", "amount_ngn", "currency", "approved_at", "source_state"],
  ],
  [
    "ledger",
    ["历史核销依据", "Historical ledger observations"],
    ["id", "amount_ngn", "currency", "approved_at", "source_state"],
  ],
  [
    "f6",
    ["来源F6记录", "Source F6 records"],
    ["receipt", "customer", "destination", "pieces", "kg", "cbm"],
  ],
] as [string, [string, string], string[]][]) {
  pages["legacy-" + kind] = {
    title,
    endpoint: "/api/legacy/" + kind,
    detail: "/api/legacy/" + kind + "/",
    columns: cols(columns),
    cap: "legacy.global_read",
    legacy: kind,
    filters: [f("query")],
  };
}
export function clean(value: Row): Row {
  return Object.fromEntries(
    Object.entries(value)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => [
        k,
        Array.isArray(v)
          ? v.map((r) => (typeof r === "object" && r ? clean(r) : r))
          : typeof v === "object" && v
            ? clean(v)
            : v,
      ]),
  );
}
export function formInitial(fields: Field[], row: Row): Row {
  return Object.fromEntries(
    fields
      .map((f) => {
        const value=row[f.key];
        return [f.key,f.type==='rows'&&Array.isArray(value)?value.map(item=>formInitial(f.fields||[],item)):f.type==='group'&&value&&typeof value==='object'?formInitial(f.fields||[],value):value];
      })
      .filter(([, v]) => v !== undefined),
  );
}
export async function versions(items: Row[], field: string, path: string) {
  return Promise.all(
    items.map(async (r) => ({
      ...r,
      expected_version: (await api(path + r[field])).version,
    })),
  );
}
