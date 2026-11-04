import { useContext, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  X,
  Upload,
  Search,
} from "lucide-react";
import { api, ApiError, fileBlob, rows, upload, type Row } from "./api";
import { LangContext, useT, label, valueText, errorText } from "./i18n";

export function ErrorBox({ error }: { error: any }) {
  const lang = useContext(LangContext);
  if (!error) return null;
  return (
    <div role="alert" className="error">
      <p>{errorText(error, lang)}</p>
      {error.fields?.length > 0 && (
        <ul>
          {error.fields.map((f: any, i: number) => (
            <li key={i}>
              {f.location
                ?.slice(1)
                .map((k: string) => label(k, lang))
                .join(" › ")}
            </li>
          ))}
        </ul>
      )}
      {error.details && <RecordView data={error.details} />}
    </div>
  );
}
export function Empty() {
  const t = useT();
  return (
    <p className="empty">
      {t(
        "当前范围没有记录。可调整筛选，或使用新增 / 导入录入真实资料。",
        "No records in this scope. Adjust the filters or add/import actual records.",
      )}
    </p>
  );
}
export function RecordView({
  data,
  links,
  onLink,
}: {
  data: any;
  links?: boolean;
  onLink?: (kind: string, id: string) => void;
}) {
  const lang = useContext(LangContext);
  if (data == null) return <span>{valueText(null, lang)}</span>;
  if (Array.isArray(data))
    return (
      <div className="records">
        {data.map((r, i) => (
          <div key={r?.id || i} className="record">
            <RecordView data={r} links={links} onLink={onLink} />
          </div>
        ))}
      </div>
    );
  if (typeof data !== "object")
    return <span className="preserve">{valueText(data, lang)}</span>;
  return (
    <dl className="facts">
      {Object.entries(data)
        .filter(
          ([key]) =>
            ![
              "object_path",
              "csrf_token",
              "nonce",
              "request_hash",
              "password_hash",
            ].includes(key),
        )
        .map(([key, v]) => (
          <div key={key} className={typeof v === "object" ? "wide" : ""}>
            <dt>{label(key, lang)}</dt>
            <dd>
              {links &&
              typeof v === "string" &&
              [
                "invoice_id",
                "party_id",
                "shipment_id",
                "payment_id",
                "native_receipt_id",
                "document_id",
                "ticket_id",
              ].includes(key) ? (
                <button
                  className="text-button"
                  onClick={() =>
                    onLink?.(
                      (
                        {
                          invoice_id: "invoices",
                          party_id: "parties",
                          shipment_id: "shipments",
                          payment_id: "payments",
                          native_receipt_id: "receipts",
                          document_id: "documents",
                          ticket_id: "tickets",
                        } as Row
                      )[key],
                      v,
                    )
                  }
                >
                  {v}
                </button>
              ) : (
                <RecordView data={v} links={links} onLink={onLink} />
              )}
            </dd>
          </div>
        ))}
    </dl>
  );
}

export type Column = {
  key: string;
  title?: [string, string];
  width?: number;
  render?: (r: Row) => ReactNode;
};
export function DataTable({
  items,
  columns,
  frozen = 0,
  identity = "table",
  onSelect,
  selected,
  selectable,
  onSelection,
}: {
  items: Row[];
  columns: Column[];
  frozen?: number;
  identity?: string;
  onSelect?: (r: Row) => void;
  selected?: Set<string>;
  selectable?: boolean;
  onSelection?: (s: Set<string>) => void;
}) {
  const lang = useContext(LangContext),
    t = useT();
  const scroller = useRef<HTMLDivElement>(null),
    bottom = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const [scrollVisible, setScrollVisible] = useState(false);
  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    const measure = () => {setViewportWidth(node.clientWidth);const rect=node.getBoundingClientRect();setScrollVisible(rect.top < innerHeight - 100 && rect.bottom > 100);};
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    window.addEventListener('scroll',measure,true);window.addEventListener('resize',measure);
    return () => {observer.disconnect();window.removeEventListener('scroll',measure,true);window.removeEventListener('resize',measure);};
  }, []);
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    try {
      return JSON.parse(localStorage.getItem("columns:" + identity) || "{}");
    } catch {
      return {};
    }
  });
  const size = (c: Column) => widths[c.key] || c.width || 150;
  const begin = (e: React.PointerEvent, key: string, start: number) => {
    e.preventDefault();
    const x = e.clientX;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    const move = (event: PointerEvent) =>
      setWidths((w) => ({
        ...w,
        [key]: Math.max(70, start + event.clientX - x),
      }));
    const up = () => {
      target.removeEventListener("pointermove", move as EventListener);
      target.removeEventListener("pointerup", up);
    };
    target.addEventListener("pointermove", move as EventListener);
    target.addEventListener("pointerup", up, { once: true });
  };
  useEffect(() => {
    localStorage.setItem("columns:" + identity, JSON.stringify(widths));
  }, [widths, identity]);
  const left = (index: number) =>
    columns.slice(0, index).reduce((v, c) => v + size(c), selectable ? 40 : 0);
  // On narrow screens the frozen group can slide left before pinning, leaving
  // room to read the remaining columns without shrinking or hiding any field.
  const frozenTail = frozen ? size(columns[frozen-1]) : 0;
  const frozenArea = viewportWidth ? Math.min(viewportWidth, Math.max(frozenTail + (selectable ? 40 : 0), viewportWidth - 200)) : 0;
  const frozenOverflow = viewportWidth ? Math.max(0, left(frozen) - frozenArea) : 0;
  const sticky = (c: Column, i: number, header = false) => ({
    width: size(c),
    minWidth: size(c),
    maxWidth: size(c),
    ...(i < frozen
      ? { position: "sticky" as const, left: left(i) - frozenOverflow, zIndex: header ? 6 : 2 }
      : {}),
  });
  return (
    <div className="table-shell">
      <div className="table-hint">
        {t(
          "拖动表头边缘调节列宽；横向滚动查看全部列。",
          "Drag a header edge to resize; scroll horizontally for all columns.",
        )}
      </div>
      <div
        className="table-wrap"
        ref={scroller}
        onScroll={() => {
          if (bottom.current && scroller.current)
            bottom.current.scrollLeft = scroller.current.scrollLeft;
        }}
      >
        <table
          style={{
            width: columns.reduce((v, c) => v + size(c), selectable ? 40 : 0),
          }}
        >
          <thead>
            <tr>
              {selectable && (
                <th className="select-cell">
                  <input
                    type="checkbox"
                    aria-label={t("选择当前页", "Select this page")}
                    checked={
                      items.length > 0 &&
                      items.every((r) => selected?.has(r.id))
                    }
                    onChange={(e) =>
                      onSelection?.(
                        e.target.checked
                          ? new Set(items.map((r) => r.id))
                          : new Set(),
                      )
                    }
                  />
                </th>
              )}
              {columns.map((c, i) => (
                <th key={c.key} style={sticky(c, i, true)}>
                  {c.title ? t(...c.title) : label(c.key, lang)}
                  <button
                    className="resize"
                    aria-label={
                      t("调整列宽", "Resize column") + " " + label(c.key, lang)
                    }
                    onPointerDown={(e) => begin(e, c.key, size(c))}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((row, index) => (
              <tr
                key={row.id || index}
                className={[
                  selected?.has(row.id) ? "selected" : "",
                  row.color === "red"
                    ? "ar-red"
                    : row.color === "yellow"
                      ? "ar-yellow"
                      : "",
                  row.loading_notified ? "loading-notified" : "",
                ].join(" ")}
              >
                {selectable && (
                  <td className="select-cell">
                    <input
                      type="checkbox"
                      aria-label={
                        t("选择", "Select") +
                        " " +
                        (row.reference || row.receipt || index + 1)
                      }
                      checked={selected?.has(row.id) || false}
                      onChange={(e) => {
                        const s = new Set(selected);
                        if (e.target.checked) s.add(row.id);
                        else s.delete(row.id);
                        onSelection?.(s);
                      }}
                    />
                  </td>
                )}
                {columns.map((c, i) => (
                  <td key={c.key} style={sticky(c, i)}>
                    {c.render ? (
                      c.render(row)
                    ) : ["reference", "receipt", "name"].includes(c.key) &&
                      onSelect ? (
                      <button
                        className="text-button"
                        onClick={() => onSelect(row)}
                      >
                        {valueText(row[c.key], lang)}
                      </button>
                    ) : typeof row[c.key] === "object" &&
                      row[c.key] !== null ? (
                      <span className="table-object">{Object.entries(row[c.key]).map(([key,value])=><span key={key}>{!Array.isArray(row[c.key]) && label(key,lang)+': '}{valueText(value,lang)}</span>)}</span>
                    ) : (
                      valueText(row[c.key], lang)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <Empty />}
      </div>
      <div
        className="floating-scroll"
        style={{visibility:scrollVisible?'visible':'hidden'}}
        ref={bottom}
        onScroll={() => {
          if (bottom.current && scroller.current)
            scroller.current.scrollLeft = bottom.current.scrollLeft;
        }}
        aria-label={t("表格水平滚动", "Table horizontal scroll")}
      >
        <div
          style={{
            width: columns.reduce((v, c) => v + size(c), selectable ? 40 : 0),
            height: 1,
          }}
        />
      </div>
    </div>
  );
}
export function Pager({
  offset,
  count,
  total,
  onChange,
}: {
  offset: number;
  count: number;
  total?: number;
  onChange: (n: number) => void;
}) {
  const t = useT();
  return (
    <div className="pager">
      <span>
        {count ? offset + 1 : 0}–{offset + count}
        {total !== undefined ? " / " + total : ""}
      </span>
      <button
        disabled={!offset}
        onClick={() => onChange(Math.max(0, offset - 50))}
      >
        <ChevronLeft size={16} />
        {t("上一页", "Previous")}
      </button>
      <button disabled={count < 50} onClick={() => onChange(offset + 50)}>
        {t("下一页", "Next")}
        <ChevronRight size={16} />
      </button>
    </div>
  );
}

export type Field = {
  key: string;
  title?: [string, string];
  type?:
    | "text"
    | "date"
    | "datetime-local"
    | "number"
    | "password"
    | "textarea"
    | "bool"
    | "select"
    | "lookup"
    | "file"
    | "rows"
    | "strings"
    | "group";
  required?: boolean;
  options?: string[];
  optionLabels?: Record<string,[string,string]>;
  lookup?: string;
  fields?: Field[];
  default?: any;
  help?: [string, string];
  multi?: boolean;
};
export const f = (
  key: string,
  type?: Field["type"],
  extra: Partial<Field> = {},
): Field => ({ key, type, ...extra });
export function Picker({
  kind,
  value,
  onChange,
  title,
  required = false,
  multi = false,
}: {
  kind: string;
  value: any;
  onChange: (v: any) => void;
  title: string;
  required?: boolean;
  multi?: boolean;
}) {
  const [search, setSearch] = useState(""),
    [options, setOptions] = useState<Row[]>([]),
    [error, setError] = useState<any>();
  const t = useT();
  useEffect(() => {
    let live = true;
    const timer = setTimeout(
      () =>
        api(
          "/api/workspace/options?kind=" +
            encodeURIComponent(kind) +
            "&query=" +
            encodeURIComponent(search),
        )
          .then((d) => {
            if (live) {
              setOptions(rows(d));
              setError(null);
            }
          })
          .catch((e) => {
            if (live) setError(e);
          }),
      180,
    );
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [kind, search]);
  return (
    <div className="picker">
      <div className="search-input">
        <Search size={15} />
        <input
          aria-label={t("搜索", "Search") + " " + title}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t(
            "按单号 / 名称 / 电话搜索",
            "Search reference / name / phone",
          )}
        />
      </div>
      <select
        aria-label={title}
        required={required}
        value={value || (multi ? [] : "")}
        multiple={multi}
        onChange={(e) =>
          onChange(
            multi
              ? Array.from(e.target.selectedOptions).map((o) => o.value)
              : e.target.value,
          )
        }
      >
        {!multi && <option value="">{t("请选择", "Select")}</option>}
        {value && !multi && !options.some((o) => o.id === value) && (
          <option value={value}>{value}</option>
        )}
        {multi && Array.isArray(value) && value.filter(id => !options.some(o => o.id === id)).map(id => (
          <option key={id} value={id}>{id}</option>
        ))}
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label} {o.version ? " · v" + o.version : ""}
          </option>
        ))}
      </select>
      <ErrorBox error={error} />
    </div>
  );
}
export function Fields({
  fields,
  value,
  onChange,
  prefix = "",
}: {
  fields: Field[];
  value: Row;
  onChange: (v: Row) => void;
  prefix?: string;
}) {
  const lang = useContext(LangContext),
    t = useT();
  const [uploadError, setUploadError] = useState<any>(),
    [uploading, setUploading] = useState(false);
  const set = (key: string, v: any) => onChange({ ...value, [key]: v });
  return (
    <div className="form-grid">
      {fields.map((field) => {
        const title = field.title ? t(...field.title) : label(field.key, lang);
        const v = value[field.key] ?? "";
        const id = prefix + field.key;
        return (
          <div
            className={
              ["textarea", "rows", "strings", "group"].includes(field.type || "")
                ? "field wide"
                : "field"
            }
            key={field.key}
          >
            <label htmlFor={id}>
              {title}
              {field.required && <span aria-hidden="true"> *</span>}
            </label>
            {field.type === "group" ? (
              <Fields
                fields={field.fields || []}
                value={v || {}}
                onChange={(updated) => set(field.key, updated)}
                prefix={id + "."}
              />
            ) : field.type === "lookup" ? (
              <Picker
                kind={field.lookup || field.key}
                value={v}
                onChange={(v) => set(field.key, v)}
                title={title}
                required={field.required}
                multi={field.multi}
              />
            ) : field.type === "rows" ? (
              <div className="repeatable">
                {(Array.isArray(v) ? v : []).map((r, i) => (
                  <fieldset key={i}>
                    <legend>
                      {title} {i + 1}
                    </legend>
                    <Fields
                      fields={field.fields || []}
                      value={r}
                      onChange={(updated) =>
                        set(
                          field.key,
                          v.map((old: any, n: number) =>
                            n === i ? updated : old,
                          ),
                        )
                      }
                      prefix={id + i + "."}
                    />
                    <button
                      type="button"
                      onClick={() =>
                        set(
                          field.key,
                          v.filter((_: any, n: number) => n !== i),
                        )
                      }
                    >
                      <X size={15} />
                      {t("移除此行", "Remove row")}
                    </button>
                  </fieldset>
                ))}
                <button
                  type="button"
                  onClick={() =>
                    set(field.key, [
                      ...(Array.isArray(v) ? v : []),
                      Object.fromEntries(
                        (field.fields || [])
                          .filter((f) => f.default !== undefined)
                          .map((f) => [f.key, f.default]),
                      ),
                    ])
                  }
                >
                  <Plus size={16} />
                  {t("增加明细", "Add row")}
                </button>
              </div>
            ) : field.type === "bool" ? (
              <select
                id={id}
                  aria-label={title}
                value={v === true ? "true" : v === false ? "false" : ""}
                onChange={(e) => set(field.key, e.target.value === "true")}
              >
                <option value="">
                  {t("未选 / 不要", "Not selected / No")}
                </option>
                <option value="true">{t("要 / 是", "Yes")}</option>
                <option value="false">{t("不要 / 否", "No")}</option>
              </select>
            ) : field.type === "select" ? (
              <select
                id={id}
                  aria-label={title}
                value={v}
                required={field.required}
                onChange={(e) => set(field.key, e.target.value)}
              >
                <option value="">{t("请选择", "Select")}</option>
                {field.options?.map((o) => (
                  <option key={o} value={o}>
                    {field.optionLabels?.[o]?t(...field.optionLabels[o]):valueText(o, lang)}
                  </option>
                ))}
              </select>
            ) : field.type === "textarea" || field.type === "strings" ? (
              <textarea
                id={id}
                  aria-label={title}
                value={Array.isArray(v) ? v.join("\n") : v}
                required={field.required}
                rows={3}
                onChange={(e) =>
                  set(
                    field.key,
                    field.type === "strings"
                      ? e.target.value.split("\n").filter(Boolean)
                      : e.target.value,
                  )
                }
              />
            ) : field.type === "file" ? (
              <>
                <input
                  id={id}
                    aria-label={title}
                  type="file"
                  multiple={field.multi}
                  disabled={uploading}
                  onChange={async (e) => {
                    setUploading(true);
                    setUploadError(null);
                    try {
                      const result = [];
                      for (const file of Array.from(e.target.files || []))
                        result.push(await upload(file));
                      set(
                        field.key,
                        field.multi ? result.map((r) => r.id) : result[0]?.id,
                      );
                    } catch (e) {
                      setUploadError(e);
                    } finally {
                      setUploading(false);
                    }
                  }}
                />
                <button type="button" disabled={uploading} onClick={async()=>{
                  setUploading(true);setUploadError(null);
                  try {
                    const images: Row[]=[];
                    for(const item of await navigator.clipboard.read()) {
                      const type=item.types.find(type=>['image/png','image/jpeg','image/webp'].includes(type));
                      if(type){const blob=await item.getType(type);images.push(await upload(new File([blob],'clipboard-original.'+type.split('/')[1],{type})))}
                      if(images.length&&!field.multi)break;
                    }
                    if(!images.length)throw new ApiError(422,'clipboard_image_missing',null);
                    set(field.key,field.multi?[...(Array.isArray(v)?v:[]),...images.map(image=>image.id)]:images[0].id);
                  }catch(e){setUploadError(e)}finally{setUploading(false)}
                }}>{t('从剪贴板添加原图','Add original image from clipboard')}</button>
                {v && (
                  <span>
                    {t("原件已保存", "Original uploaded")}:{" "}
                    {Array.isArray(v) ? v.length : 1}
                  </span>
                )}
                <ErrorBox error={uploadError} />
              </>
            ) : (
              <input
                id={id}
                  aria-label={title}
                type={field.type || "text"}
                required={field.required}
                value={v}
                step={field.type === "number" ? "any" : undefined}
                autoComplete={
                  field.type === "password" ? "new-password" : "off"
                }
                onChange={(e) =>
                  set(
                    field.key,
                    field.type === "number" && e.target.value !== ""
                      ? Number(e.target.value)
                      : e.target.value,
                  )
                }
              />
            )}{" "}
            {field.help && <small>{t(...field.help)}</small>}
          </div>
        );
      })}
    </div>
  );
}
export function Form({
  fields,
  initial = {},
  submit,
  onSaved,
  title,
  submitLabel,
  transform,
}: {
  fields: Field[];
  initial?: Row;
  submit: (data: Row, key: string) => Promise<Row>;
  onSaved?: (r: Row) => void;
  title?: string;
  submitLabel?: [string, string];
  transform?: (v: Row) => Row;
}) {
  const t = useT();
  const [value, setValue] = useState<Row>(() => ({
      ...Object.fromEntries(
        fields
          .filter((f) => f.default !== undefined)
          .map((f) => [f.key, f.default]),
      ),
      ...initial,
    })),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<any>(),
    [saved, setSaved] = useState(false);
  const retry = useRef({ body: "", key: crypto.randomUUID() });
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        setSaved(false);
        const body = transform
          ? transform(value)
          : Object.fromEntries(
              Object.entries(value).filter(([, v]) => v !== ""),
            );
        const json = JSON.stringify(body);
        if (retry.current.body !== json)
          retry.current = { body: json, key: crypto.randomUUID() };
        try {
          const result = await submit(body, retry.current.key);
          setSaved(true);
          if (result.version && Object.prototype.hasOwnProperty.call(value,'expected_version'))
            setValue((v) => ({ ...v, expected_version: result.version }));
          // A second click on an unchanged creation form replays the same
          // command. Only changed input (or an explicit new form) creates a key.
          onSaved?.(result);
        } catch (e) {
          setError(e);
        } finally {
          setBusy(false);
        }
      }}
    >
      {title && <h3>{title}</h3>}
      <Fields
        fields={fields}
        value={value}
        onChange={(v) => {
          setValue(v);
          setSaved(false);
        }}
      />
      <ErrorBox error={error} />
      <div className="form-actions">
        <button className="primary" type="submit" disabled={busy}>
          {busy
            ? t("正在保存…", "Saving…")
            : submitLabel
              ? t(...submitLabel)
              : t("保存", "Save")}
        </button>
        {saved && <span role="status">{t("已保存", "Saved")}</span>}
      </div>
    </form>
  );
}
export function FileViewer({ path, name }: { path: string; name?: string }) {
  const [url, setURL] = useState(""),
    [type, setType] = useState(""),
    [error, setError] = useState<any>();
  const t = useT();
  useEffect(() => {
    let current = "",
      alive = true;
    fileBlob(path)
      .then((b) => {
        current = URL.createObjectURL(b);
        if (alive) {
          setURL(current);
          setType(b.type);
        }
      })
      .catch(setError);
    return () => {
      alive = false;
      if (current) URL.revokeObjectURL(current);
    };
  }, [path]);
  return (
    <>
      <ErrorBox error={error} />
      {url && (
        <>
          <a href={url} download={name || "original"}>
            {t("下载原件", "Download original")}
          </a>
          {type.startsWith("image/") ? (
            <img
              className="original-image"
              src={url}
              alt={name || t("原始凭证", "Original evidence")}
            />
          ) : type === "application/pdf" ? (
            <iframe className="pdf-viewer" src={url} title={name || "PDF"} />
          ) : (
            <p>
              {t(
                "此原始文件请下载后查看。",
                "Download this original file to view it.",
              )}
            </p>
          )}
        </>
      )}
    </>
  );
}
