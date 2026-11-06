import { useEffect, useState } from "react";
import {
  Ship,
  Plane,
  Package,
  Warehouse,
  FileCheck,
  Search,
  Menu,
  LogOut,
  Bell,
  ArrowRight,
} from "lucide-react";
import { api, setCSRF, rows, type Row } from "./api";
import { LangContext, useT, type Language } from "./i18n";
import { ErrorBox, Form, RecordView, DataTable, Fields, f } from "./ui";
import { EntityPage } from "./EntityPage";
import {
  pages,
  required,
  lookup,
  select,
  paymentFields,
  versions,
} from "./catalog";
import { Operations } from "./Operations";
import {ExportJob} from './ExportJob';

function Welcome({
  enter,
  language,
  setLanguage,
  tracking,
}: {
  enter: () => void;
  language: Language;
  setLanguage: (v: Language) => void;
  tracking: () => void;
}) {
  const t = useT(),
    [profile, setProfile] = useState<Row>({});
  useEffect(() => {
    api("/api/public/profile")
      .then(setProfile)
      .catch(() => {});
  }, []);
  const services = [
    {
      Icon: Ship,
      title: t(
        "海运整柜 / 拼箱",
        "Sea cargo · FCL / LCL",
        "Fret maritime · FCL / LCL",
      ),
      body: t(
        "中国至尼日利亚海运物流。",
        "Sea cargo logistics from China to Nigeria.",
        "Logistique maritime de la Chine au Nigeria.",
      ),
    },
    {
      Icon: Plane,
      title: t("空运专线", "Air cargo", "Fret aérien"),
      body: t(
        "空运货物与运单管理。",
        "Air cargo and waybill services.",
        "Services de fret aérien et de lettres de transport.",
      ),
    },
    {
      Icon: Package,
      title: t("EXPRESS 特快", "EXPRESS", "EXPRESS"),
      body: t(
        "2–3天小件特快派送，实时报价。",
        "2–3 day express parcel delivery. Live quotations.",
        "Livraison express de petits colis en 2–3 jours. Devis en temps réel.",
      ),
    },
    {
      Icon: Warehouse,
      title: t("中国仓集货", "China consolidation", "Groupage en Chine"),
      body: t(
        "收货、集货与出运资料衔接。",
        "Receiving, consolidation and shipment documentation.",
        "Réception, groupage et documents d’expédition.",
      ),
    },
    {
      Icon: FileCheck,
      title: t("报关清关", "Customs clearance", "Dédouanement"),
      body: t(
        "报关及目的港清关资料服务。",
        "Export and destination customs documentation.",
        "Documents de douane à l’exportation et à destination.",
      ),
    },
    {
      Icon: Search,
      title: t("全程可追踪", "Shipment tracking", "Suivi des marchandises"),
      body: t(
        "凭单号及客户号码验证，查询已记录节点。",
        "Verify your reference and phone digits to view recorded milestones.",
        "Vérifiez la référence et les chiffres de votre téléphone pour consulter les étapes enregistrées.",
      ),
    },
  ];
  return (
    <main className="welcome">
      <div className="welcome-inner">
        <header className="welcome-header">
          <div className="brand">
            <img src="/logo.png" alt="MC STAR LOGISTICS" />
            <div>
              <h1>MC STAR LOGISTICS</h1>
              <p>
                {t(
                  "中国 ⇄ 尼日利亚物流",
                  "China ⇄ Nigeria logistics",
                  "Logistique Chine ⇄ Nigeria",
                )}
              </p>
            </div>
          </div>
          <div className="language-actions">
            {(["zh", "en", "fr"] as Language[]).map((l) => (
              <button
                key={l}
                className={language === l ? "selected" : ""}
                onClick={() => setLanguage(l)}
              >
                {l === "zh" ? "中文" : l.toUpperCase()}
              </button>
            ))}
            <button className="primary" onClick={enter}>
              {t("进入系统", "Enter workspace", "Accéder au système")}
              <ArrowRight size={17} />
            </button>
          </div>
        </header>
        <section className="welcome-intro">
          <h2>
            {t(
              "从中国到尼日利亚，连接每一票货物。",
              "Connecting every shipment from China to Nigeria.",
              "Relier chaque expédition de la Chine au Nigeria.",
            )}
          </h2>
          <p>
            {t(
              "海运、空运与EXPRESS，收货、单证及货物跟踪。",
              "Sea cargo, air cargo and EXPRESS. Receiving, documents and shipment tracking.",
              "Fret maritime, aérien et EXPRESS. Réception, documents et suivi des marchandises.",
            )}
          </p>
        </section>
        <div className="route-band" aria-hidden="true">
          <div className="route-point">
            <span className="route-city">{t("中国", "China", "Chine")}</span>
          </div>
          <div className="route-track">
            <span className="route-node"><Ship size={16} /></span>
            <span className="route-line" />
            <span className="route-node"><Plane size={16} /></span>
          </div>
          <div className="route-point route-point-end">
            <span className="route-city">{t("尼日利亚", "Nigeria", "Nigéria")}</span>
          </div>
        </div>
        <section className="services">
          {services.map(({ Icon, title, body }) => (
            <article key={title}>
              <Icon size={25} />
              <h3>{title}</h3>
              <p>{body}</p>
            </article>
          ))}
        </section>
        {profile.hero_large && (
          <img
            className="hero-original"
            src={profile.hero_large}
            alt={t(
              "公司宣传原图",
              "Company original photograph",
              "Photographie originale de l’entreprise",
            )}
          />
        )}
        <section className="public-directory">
          {["CN", "NG"].map((country) => {
            const entries = (profile.warehouses || []).filter(
              (w: Row) => w.country === country,
            );
            return entries.length ? (
              <div key={country}>
                <h2>
                  {country === "CN"
                    ? t("中国仓", "China warehouses", "Entrepôts en Chine")
                    : t(
                        "尼日利亚仓与办公室",
                        "Nigeria warehouses & offices",
                        "Entrepôts et bureaux au Nigeria",
                      )}
                </h2>
                {entries.map((w: Row, i: number) => (
                  <article key={i}>
                    <h3>{w.name}</h3>
                    <p>{w.address}</p>
                    <p>{w.receiving_hours}</p>
                    <p>{w.contacts}</p>
                  </article>
                ))}
              </div>
            ) : null;
          })}
          <a
            href="#tracking"
            className="tracking-card"
            onClick={(e) => {
              e.preventDefault();
              tracking();
            }}
          >
            <Search size={28} />
            <h2>{t("客户自助查询", "Track your cargo", "Suivi client")}</h2>
            <p>
              {t(
                "输入收据号 / 快递单号与客户电话后四位。",
                "Enter your receipt / courier reference and last four phone digits.",
                "Saisissez votre référence et les quatre derniers chiffres du téléphone.",
              )}
            </p>
            <span>
              {t("打开货物跟踪", "Open shipment tracking", "Ouvrir le suivi")} →
            </span>
          </a>
        </section>
        {(profile.hotline || profile.openshipment) && (
          <section className="public-contacts">
            {profile.hotline && (
              <div>
                <h3>{t('服务热线','Hotline','Service clientèle')}</h3>
                <p>
                  {typeof profile.hotline === "string"
                    ? profile.hotline
                    : JSON.stringify(profile.hotline)}
                </p>
              </div>
            )}
            {profile.openshipment && (
              <div>
                <h3>{t('货运咨询','Openshipment','Renseignements sur les expéditions')}</h3>
                <p>
                  {typeof profile.openshipment === "string"
                    ? profile.openshipment
                    : JSON.stringify(profile.openshipment)}
                </p>
              </div>
            )}
          </section>
        )}
        {profile.hero_small && (
          <img
            className="hero-original small-hero"
            src={profile.hero_small}
            alt={t(
              "公司宣传原图",
              "Company original photograph",
              "Photographie originale de l’entreprise",
            )}
          />
        )}
      </div>
    </main>
  );
}
function Tracking({ back,language,setLanguage }: { back: () => void;language:Language;setLanguage:(v:Language)=>void }) {
  const t = useT();
  const [result, setResult] = useState<Row>(),
    [register, setRegister] = useState(false),
    [challenge, setChallenge] = useState<Row>(),
    [customerCSRF, setCustomerCSRF] = useState(""),
    [documents, setDocuments] = useState<Row>(),
    [selected,setSelected]=useState(new Set<string>()),
    [exportId,setExportId]=useState(''),[offset,setOffset]=useState(0);
  useEffect(()=>{if(!register)return;let live=true;api('/api/customer/session').then(async session=>{const d=await api('/api/customer/documents?offset='+offset);if(live){setCustomerCSRF(session.csrf_token);setDocuments(d)}}).catch(()=>{});return()=>{live=false}},[register,offset]);
  return (
    <main className="public-form">
      <button onClick={back}>{t("返回首页", "Back to home")}</button>
      <div className="language-switch" aria-label={t('语言','Language')}>
        <button aria-pressed={language==='zh'} onClick={()=>setLanguage('zh')}>中文</button>
        <button aria-pressed={language==='en'} onClick={()=>setLanguage('en')}>EN</button>
      </div>
      <img src="/logo.png" alt="MC STAR LOGISTICS" />
      <h1>{t("客户自助查询", "Track your cargo")}</h1>
      <Form
        fields={[
          {
            ...required("reference"),
            title: ["收据号 / 快递单号", "Receipt / courier reference"],
          },
          {
            ...required("last4"),
            title: ["客户电话后四位", "Last four phone digits"],
          },
        ]}
        submitLabel={["查询", "Track"]}
        successLabel={["查询完成", "Query complete"]}
        submit={async (b) => {
          const r = await api("/api/tracking/query", {
            ...b,
            language: document.documentElement.lang === "zh" ? "zh" : "en",
          });
          setResult(r);
          return r;
        }}
      />
      {result && <RecordView data={result} />}
      <hr />
      <button onClick={() => setRegister(!register)}>
        {t("验证本人手机号 / 本人箱单", "Verify your phone / My packing lists")}
      </button>
      {register && (
        <section>
          <Form
            fields={[required("phone")]}
            submitLabel={["发送验证码", "Send verification code"]}
            submit={async (b) => {
              const r = await api("/api/customer/verification", b);
              setChallenge(r);
              return r;
            }}
          />
          {challenge && (
            <Form
              fields={[required("code")]}
              submitLabel={["验证号码归属", "Verify ownership"]}
              submit={async (b) => {
                const r = await api("/api/customer/register", {
                  challenge_id: challenge.challenge_id || challenge.id,
                  ...b,
                });
                setCustomerCSRF(r.csrf_token);
                setDocuments(await api("/api/customer/documents"));
                return r;
              }}
            />
          )}
          {documents && (
            <>
              <DataTable items={documents.rows||[]} selectable selected={selected} onSelection={setSelected} columns={['receipt','mark','item_en','pieces','kg','cbm','destination','freight_usd','clearing_ngn'].map(key=>({key}))}/>
              <div className="toolbar"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-50))}>{t('上一页','Previous')}</button><button disabled={(documents.rows||[]).length<50} onClick={()=>setOffset(offset+50)}>{t('下一页','Next')}</button><button onClick={async()=>{await api('/api/customer/logout',{},'POST',undefined,customerCSRF);setDocuments(undefined);setSelected(new Set());setCustomerCSRF('');setExportId('')}}>{t('退出客户账号','Sign out of customer account')}</button></div>
              <Form
                fields={[
                  select("format", ["xlsx", "pdf", "png"], true),
                ]}
                submitLabel={["导出本人箱单", "Export my packing list"]}
                submit={async (b) => {
                  const r=await api("/api/customer/exports", {
                    ...b,
                    kind: "packing",
                    ticket_ids:(documents.rows||[]).filter((r:Row)=>selected.has(r.id)&&r.source_type==='ticket').map((r:Row)=>r.id),
                    receipt_ids:(documents.rows||[]).filter((r:Row)=>selected.has(r.id)&&r.source_type==='receipt').map((r:Row)=>r.id),
                  }, 'POST', undefined, customerCSRF);
                  setExportId(r.id);return r;
                }}
              />
              {exportId&&<ExportJob identity={exportId} customer/>}
            </>
          )}
        </section>
      )}
    </main>
  );
}
const menuTitles: Record<string, [string, string]> = {
  search: ["查找", "Search"],
  parties: ["客户 / 供应商", "Customers / suppliers"],
  f6: ["F6 仓储数据分析", "F6 warehouse data"],
  mbl: ["MBL/SO", "MBL/SO"],
  shipments: ["出运单管理", "Shipments"],
  audit: ["操作日志", "Operation log"],
  payables: ["应付账款", "Payables"],
};
const nav: [string, [string, string], string[]][] = [
  ["common", ["常用", "Common"], ["dashboard", "search", "parties"]],
  [
    "operations",
    ["操作部", "Operations"],
    ["f6", "receipts", "documents", "dispatch", "imports", "warehouse-report", "rates"],
  ],
  [
    "documentation",
    ["单证部", "Documentation"],
    ["tickets", "mbl", "shipments", "audit", "document-settings"],
  ],
  [
    "finance",
    ["财务", "Finance"],
    [
      "invoices",
      "payables",
      "today",
      "payments",
      "period",
      "evidence",
      "release",
      'opening',
    ],
  ],
  [
    "assistant",
    ["110", "110"],
    [
      "accounts",
      "bank-import",
      "assistant",
      "connections",
      "messages",
      "automations",
      "bank-identity",
      "legacy-bank-evidence",
      'bank-archive',
    ],
  ],
  [
    "directory",
    ["资料", "Directory"],
    [
      "warehouses",
      "legacy-parties",
      "legacy-shipments",
      "legacy-tickets",
      "legacy-bills",
      "legacy-slips",
      "legacy-ledger",
      "legacy-f6",
    ],
  ],
  [
    "admin",
    ["管理", "Administration"],
    ["users", "announcements", "home-settings", "migration"],
  ],
];
export const extraTitles: Record<string, [string, string]> = {
  'warehouse-report':['入库分析与库存导出','Inbound analysis & stock export'],
  'bank-archive':['银行核验归档','Bank review archive'],
  opening:['期初余额','Opening balances'],
  dashboard: ["工作台", "Workspace"],
  search: ["全局查找", "Global search"],
  imports: ["箱单 / F6导入", "Packing / F6 import"],
  dispatch: ["中国仓批次出库", "China warehouse dispatch"],
  today: ["今日核销", "Today’s approvals"],
  period: ["账单方周期结算", "Counterparty period settlement"],
  "bank-import": ["导入银行流水", "Import bank statements"],
  assistant: ["110对话助手", "110 assistant"],
  "bank-identity": ["银行交易身份复核", "Bank identity review"],
  "legacy-bank-evidence": ["历史银行证据补充", "Historical bank evidence"],
  "home-settings": ["首页资料", "Home profile"],
  "document-settings": ["单证资料与模板", "Document profile & templates"],
  migration: ["数据管理与备份", "Data & backups"],
};
const extraCapabilities: Record<string,string>={imports:'cargo.import',dispatch:'cargo.manage',today:'finance.read',period:'payables.read','bank-import':'bank.import',assistant:'assistant.read','bank-identity':'bank.identity','legacy-bank-evidence':'legacy.resolve','home-settings':'documents.configure','document-settings':'documents.configure',migration:'*','warehouse-report':'cargo.read','bank-archive':'bank.export',opening:'finance.opening'};
function Dashboard({
  user,
  navigate,
}: {
  user: Row;
  navigate: (s: string) => void;
}) {
  const t = useT(),[summary,setSummary]=useState<Row>({}),[error,setError]=useState<any>();
  useEffect(()=>{let active=true;api('/api/workspace/summary').then(r=>{if(active)setSummary(r.counts)}).catch(e=>{if(active)setError(e)});return()=>{active=false}},[]);
  const queues = [
    {
      key: "daily",
      title: ["日常队列", "Daily queues"] as [string, string],
      pages: ["receipts", "documents", "invoices", "evidence"],
    },
    {
      key: "sources",
      title: ["来源与出库", "Sources & release"] as [string, string],
      pages: ["legacy-bills", "legacy-tickets", "release"],
    },
  ];
  return (
    <section>
      <h1>{t("工作台", "Workspace")}</h1>
      <ErrorBox error={error}/>
      <div className="dashboard-board">
        {queues.map((queue) => {
          const visible = queue.pages.filter(
            (k) =>
              user.capabilities.includes("*") ||
              user.capabilities.includes(pages[k].cap),
          );
          const values = visible.map((k) => Number(summary[k] || 0));
          const max = Math.max(1, ...values);
          const total = values.reduce((sum, value) => sum + value, 0);
          return (
            <section className="queue-panel" key={queue.key}>
              <h2>
                {t(...queue.title)}
                <span className="queue-total">{total.toLocaleString()}</span>
              </h2>
              <div className="queue-list">
                {visible.map((k) => {
                  const value = Number(summary[k] || 0);
                  return (
                    <button className="queue-row" key={k} onClick={() => navigate(k)}>
                      <span className="queue-label">{t(...pages[k].title)}</span>
                      <span className="queue-meter" aria-hidden="true">
                        <span style={{ width: `${(value / max) * 100}%` }} />
                      </span>
                      {summary[k] !== undefined && (
                        <span className="queue-count">{value.toLocaleString()}</span>
                      )}
                      <ArrowRight size={16} />
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}
export default function App() {
  const [language, setLanguage] = useState<Language>("zh"),
    [screen, setScreen] = useState("home"),
    [user, setUser] = useState<Row | null>(null),
    [page, setPage] = useState("dashboard"),
    [visited, setVisited] = useState(new Set(["dashboard"])),
    [error, setError] = useState<any>(),
    [menu, setMenu] = useState(false);
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  useEffect(() => {
    if (!user) return;
    let live = true;
    const check = async () => {
      try {
        const current = await api('/api/auth/session');
        if (!live) return;
        const authority = (u: Row) => JSON.stringify([u.id, [...u.capabilities].sort(), [...u.warehouse_ids].sort()]);
        if (authority(current.user) !== authority(user)) {
          setVisited(new Set(['dashboard'])); setPage('dashboard'); setUser(current.user);
        }
        setCSRF(current.csrf_token);
      } catch (e: any) {
        if (live && e.status === 401) {
          setCSRF(''); setUser(null); setVisited(new Set(['dashboard'])); setPage('dashboard'); setScreen('login');
        }
      }
    };
    const timer = setInterval(check, 30000);
    window.addEventListener('focus', check); window.addEventListener('authority-check', check);
    return () => { live = false; clearInterval(timer); window.removeEventListener('focus', check); window.removeEventListener('authority-check', check); };
  }, [user]);
  const enter = async () => {
    setError(null);
    try {
      const r = await api("/api/auth/session");
      setUser(r.user);
      setCSRF(r.csrf_token);
      if (language === "fr") setLanguage("en");
      setScreen("workspace");
    } catch (e: any) {
      if (e.status === 401) setScreen("login");
      else setError(e);
    }
  };
  const navigate = (kind: string) => {
    setPage(kind);
    setVisited((v) => new Set([...v, kind]));
    setMenu(false);
  };
  const openRecord = (kind: string, id: string) => {
    navigate(kind);
    setTimeout(
      () =>
        window.dispatchEvent(
          new CustomEvent("open-record", { detail: { kind, id } }),
        ),
      30,
    );
  };
  return (
    <LangContext.Provider value={language}>
      {screen === "home" ? (
        <>
          <Welcome
            enter={enter}
            language={language}
            setLanguage={setLanguage}
            tracking={() => setScreen("tracking")}
          />
          <ErrorBox error={error} />
        </>
      ) : screen === "tracking" ? (
        <Tracking back={() => setScreen("home")} language={language} setLanguage={setLanguage}/>
      ) : screen === "login" ? (
        <div className="login">
          <button onClick={() => setScreen("home")}>
            {language === "zh" ? "返回首页" : "Back to home"}
          </button>
          <img src="/logo.png" alt="MC STAR LOGISTICS" />
          <h1>
            {language === "zh"
              ? "进入 MC STAR LOGISTICS"
              : "Sign in to MC STAR LOGISTICS"}
          </h1>
          <Form
            fields={[required("username"), required("password", "password")]}
            submitLabel={["登录", "Sign in"]}
            submit={async (body) => {
              const r = await api("/api/auth/login", body);
              setCSRF(r.csrf_token);
              const current = await api("/api/auth/session");
              setUser(current.user);
              setCSRF(current.csrf_token);
              setVisited(new Set(["dashboard"]));
              setPage("dashboard");
              setScreen("workspace");
              return r;
            }}
          />
        </div>
      ) : (
        user && (
          <div className="workspace" key={JSON.stringify([user.id,user.capabilities,user.warehouse_ids])}>
            <header className="app-header">
              <button
                aria-label={language === "zh" ? "打开导航" : "Open navigation"}
                onClick={() => setMenu(!menu)}
              >
                <Menu size={20} />
              </button>
              <div className="brand">
                <img src="/logo.png" alt="MC STAR LOGISTICS" />
                <strong>MC STAR LOGISTICS</strong>
              </div>
              <div className="account-controls">
                <button
                  onClick={() => navigate("announcements")}
                  aria-label={language === "zh" ? "公告" : "Announcements"}
                >
                  <Bell size={18} />
                </button>
                <select
                  aria-label={language === "zh" ? "语言" : "Language"}
                  value={language}
                  onChange={(e) => setLanguage(e.target.value as Language)}
                >
                  <option value="zh">中文</option>
                  <option value="en">EN</option>
                </select>
                <span>{user.username}</span>
                <button
                  aria-label={language === "zh" ? "退出登录" : "Sign out"}
                  onClick={async () => {
                    try {
                      await api("/api/auth/logout", {});
                    } finally {
                      setCSRF("");
                      setUser(null);
                      setVisited(new Set(["dashboard"]));
                      setPage("dashboard");
                      setScreen("home");
                    }
                  }}
                >
                  <LogOut size={18} />
                </button>
              </div>
            </header>
            <aside className={menu ? "sidebar open" : "sidebar"}>
              {nav.map(([id, title, items]) => (
                <section key={id}>
                  <h2>{title[language === "zh" ? 0 : 1]}</h2>
                  {id === "common" && (
                    <button onClick={() => setScreen("home")}>
                      {language === "zh" ? "首页" : "Home"}
                    </button>
                  )}
                  {items
                    .filter(
                      (k) =>
                        !(pages[k]?.cap||extraCapabilities[k]) ||
                        user.capabilities.includes("*") ||
                        user.capabilities.includes(pages[k]?.cap||extraCapabilities[k]),
                    )
                    .map((k) => (
                      <button
                        key={k}
                        className={page === k ? "active" : ""}
                        onClick={() => navigate(k)}
                      >
                        {
                          (menuTitles[k] || pages[k]?.title || extraTitles[k])[
                            language === "zh" ? 0 : 1
                          ]
                        }
                      </button>
                    ))}
                </section>
              ))}
            </aside>
            <main className="workspace-content">
              {[...visited].map((k) => (
                <div key={k} hidden={page !== k}>
                  {k === "dashboard" ? (
                    <Dashboard user={user} navigate={navigate} />
                  ) : pages[k] ? (
                    <EntityPage kind={k} user={user} openRecord={openRecord} />
                  ) : (
                    <Operations kind={k} user={user} openRecord={openRecord} />
                  )}
                </div>
              ))}
            </main>
          </div>
        )
      )}
    </LangContext.Provider>
  );
}
