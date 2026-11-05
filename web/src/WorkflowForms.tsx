import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { api, type Row } from "./api";
import { ErrorBox, Form, RecordView, FileViewer } from "./ui";
import { useT } from "./i18n";
import { required } from "./catalog";
import type { Action } from "./workflows";
export function CopyMarks({
  marks,
  canCancel,
  onSaved,
}: {
  marks: Row[];
  canCancel: boolean;
  onSaved: (r: Row) => void;
}) {
  const t = useT(),
    [selected, setSelected] = useState<string>();
  return (
    <section>
      <h3>{t("复制记录", "Copy history")}</h3>
      {!marks.length && <p>{t("尚无复制记录", "No copy records yet")}</p>}
      {marks.map((mark) => (
        <section key={mark.copy_id}>
          <p
            className={
              !mark.cancelled_at && mark.kind === "loading"
                ? "loading-mark"
                : ""
            }
          >
            {mark.cancelled_at
              ? t("已撤销复制标记", "Copy marker cancelled")
              : mark.kind === "loading"
                ? t("已通知装柜", "Loading notice copied")
                : t("已复制通知", "Notice copied")}{" "}
            · {mark.operator_name||mark.actor_id} · {mark.copied_at}
          </p>
          {mark.cancelled_at ? (
            <RecordView data={mark} />
          ) : (
            canCancel && (
              <button onClick={() => setSelected(mark.copy_id)}>
                <X size={15} />
                {t("撤销此复制标记", "Cancel this copy marker")}
              </button>
            )
          )}
          {selected === mark.copy_id && !mark.cancelled_at && (
            <Form
              fields={[required("reason")]}
              initial={{ expected_version: mark.version }}
              submitLabel={[
                "确认撤销原复制标记",
                "Confirm cancellation of original copy marker",
              ]}
              submit={async (body, key) => {
                const r = await api(
                  "/api/notification-copies/" + mark.copy_id + "/cancel",
                  body,
                  "POST",
                  key,
                );
                setSelected(undefined);
                onSaved(r);
                return r;
              }}
            />
          )}
        </section>
      ))}
    </section>
  );
}
export function PickupForm({
  action,
  onSaved,
}: {
  action: Action;
  onSaved: (r: Row) => void;
}) {
  const t = useT(),
    [order, setOrder] = useState<Row>(),
    [ticket, setTicket] = useState(""),
    [error, setError] = useState<any>();
  const orderId = action.initial!.order_id;
  const refresh = async () => {
    try {
      setOrder(await api("/api/release-orders/" + orderId + "/eligibility"));
      setError(null);
    } catch (e) {
      setOrder(undefined);
      setTicket("");
      setError(e);
    }
  };
  useEffect(() => {
    let live = true;
    api("/api/release-orders/" + orderId + "/eligibility")
      .then((r) => {
        if (live) setOrder(r);
      })
      .catch((e) => {
        if (live) setError(e);
      });
    return () => {
      live = false;
    };
  }, [orderId]);
  const member = order?.members?.find((m: Row) => m.ticket_id === ticket);
  const fields = action.fields
    .filter((f) => f.key !== "ticket_id")
    .map((field) =>
      field.key === "photo_ids"
        ? {
            ...field,
            upload: async (file: File) => {
              const data = new FormData();
              data.append("file", file);
              data.append("ticket_id", ticket);
              data.append("expected_version", String(order!.version));
              return api("/api/release-orders/" + orderId + "/photos", data);
            },
          }
        : field,
    );
  return (
    <section className="workflow">
      <h3>{t(...action.title)}</h3>
      <ErrorBox error={error} />
      <button onClick={() => void refresh()}>
        {t("刷新当前可提货成员", "Refresh eligible pickup members")}
      </button>
      {order && !order.eligible && (
        <p role="alert">
          {t(
            "指令来源已变更，请重新确认放货指令。",
            "Instruction sources changed. Re-confirm the release instruction.",
          )}
        </p>
      )}
      {order?.eligible && (
        <>
          <label htmlFor="pickup-member">
            {t("本指令可提货成员", "Eligible members of this instruction")}
          </label>
          <select
            id="pickup-member"
            value={ticket}
            onChange={(e) => setTicket(e.target.value)}
          >
            <option value="">{t("请选择", "Select")}</option>
            {order.members
              .filter((m: Row) => Number(m.remaining.pieces) > 0)
              .map((m: Row) => (
                <option key={m.ticket_id} value={m.ticket_id}>
                  {m.receipt} · {t("剩余件数", "Remaining pieces")}{" "}
                  {m.remaining.pieces}
                </option>
              ))}
          </select>
          {member && Number(member.remaining.pieces) > 0 && (
            <>
              <RecordView data={{ remaining: member.remaining }} />
              <Form
                key={ticket + ":" + order.version}
                fields={fields}
                initial={{
                  order_id: orderId,
                  expected_version: order.version,
                  ticket_id: ticket,
                  ...member.remaining,
                }}
                submit={async (body, key) => {
                  const r = await action.run(body, key);
                  await refresh();
                  onSaved(r);
                  return r;
                }}
              />
            </>
          )}
        </>
      )}
      {(order?.physical_releases || []).map((picked: Row) => (
        <section key={picked.id}>
          <h3>{t("实际提货记录", "Physical pickup record")}</h3>
          <RecordView data={picked} />
          {picked.photo_ids.map((id: string) => (
            <details key={id}>
              <summary>
                {t("查看提货照片原件", "View original pickup photo")}
              </summary>
              <FileViewer
                path={"/api/physical-releases/" + picked.id + "/photos/" + id}
              />
            </details>
          ))}
        </section>
      ))}
    </section>
  );
}
export function GrantsForm({
  action,
  onSaved,
}: {
  action: Action;
  onSaved: (r: Row) => void;
}) {
  const t = useT(),
    [current, setCurrent] = useState<Row>(),
    [pending, setPending] = useState<Row>(),
    [ack, setAck] = useState(false),
    [error, setError] = useState<any>(),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false);
  const key = useRef(crypto.randomUUID());
  useEffect(() => {
    let live = true;
    action.load!()
      .then((r) => {
        if (live) setCurrent(r);
      })
      .catch((e) => {
        if (live) setError(e);
      });
    return () => {
      live = false;
    };
  }, [action.initial?.user_id]);
  const removed = (name: string) =>
    current?.[name]?.filter((v: string) => !pending?.[name]?.includes(v)) || [];
  const added = (name: string) =>
    pending?.[name]?.filter((v: string) => !current?.[name]?.includes(v)) || [];
  const revoking =
    removed("capabilities").length + removed("warehouse_ids").length > 0;
  return (
    <section className="workflow">
      <ErrorBox error={error} />
      <button
        onClick={async () => {
          setError(null);
          try {
            setCurrent(await action.load!());
            setPending(undefined);
            setAck(false);
            setSaved(false);
          } catch (e) {
            setError(e);
          }
        }}
      >
        {t("重新读取当前授权", "Reload current grants")}
      </button>
      {!current ? (
        <p role="status">{t("正在读取当前授权…", "Loading current grants…")}</p>
      ) : (
        <Form
          key={current.version}
          title={t(...action.title)}
          fields={action.fields}
          initial={{
            expected_version: current.version,
            capabilities: current.capabilities,
            warehouse_ids: current.warehouse_ids,
          }}
          submitLabel={["核对权限增删", "Review grant changes"]}
          successLabel={[
            "预览已就绪，请确认后执行",
            "Preview ready; confirm to apply",
          ]}
          submit={async (body) => {
            setPending(body);
            setAck(false);
            setSaved(false);
            key.current = crypto.randomUUID();
            return {};
          }}
        />
      )}
      {pending && (
        <section className="confirmation">
          <h3>
            {t(
              "确认权限与仓库范围变更",
              "Confirm capability and warehouse changes",
            )}
          </h3>
          <p>
            {t("新增权限", "Add capabilities")}:{" "}
            {added("capabilities").join(", ") || "—"}
          </p>
          <p>
            {t("撤销权限", "Revoke capabilities")}:{" "}
            {removed("capabilities").join(", ") || "—"}
          </p>
          <p>
            {t("新增仓库范围", "Add warehouse scopes")}:{" "}
            {added("warehouse_ids").join(", ") || "—"}
          </p>
          <p>
            {t("撤销仓库范围", "Revoke warehouse scopes")}:{" "}
            {removed("warehouse_ids").join(", ") || "—"}
          </p>
          {revoking && (
            <label className="grant-revocation-confirmation">
              <input
                type="checkbox"
                checked={ack}
                onChange={(e) => setAck(e.target.checked)}
              />
              {t(
                "我确认撤销以上权限与仓库范围",
                "I confirm revoking the capabilities and warehouse scopes above",
              )}
            </label>
          )}
          <button
            className="primary"
            disabled={busy || (revoking && !ack)}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const r = await action.run(pending, key.current);
                setCurrent(await action.load!());
                setPending(undefined);
                setSaved(true);
                onSaved(r);
              } catch (e) {
                setError(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            {t("确认保存授权", "Confirm grant changes")}
          </button>
        </section>
      )}
      {saved && <p role="status">{t("已保存", "Saved")}</p>}
    </section>
  );
}
