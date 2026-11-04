import { test, expect, type Page } from "@playwright/test";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import {resolve} from 'node:path';
import { execFileSync } from "node:child_process";
test.beforeEach(async({page})=>{await page.setViewportSize({width:1440,height:1000})});
async function capture(page:Page,name:string,target?:any){
  if(!process.env.MCSTAR_FIX_SHOTS)return;
  const directory=resolve(process.env.MCSTAR_FIX_SHOTS);mkdirSync(directory,{recursive:true});const original=page.viewportSize()!;const scroll=await page.evaluate(()=>({x:scrollX,y:scrollY}));
  for(const width of [1440,390]){const height=width===1440?1000:844;await page.setViewportSize({width,height});if(target)await target.evaluate((el:HTMLElement)=>scrollTo(0,el.getBoundingClientRect().top+scrollY-90));await page.screenshot({path:resolve(directory,name+'-'+width+'.png'),animations:'disabled'});writeFileSync(resolve(directory,name+'-'+width+'.json'),JSON.stringify({name,width,height,url:page.url(),scope:'Task6 fix1 functional controls; synthetic data'},null,2))}
  await page.setViewportSize(original);await page.evaluate(({x,y})=>scrollTo(x,y),scroll);
}

async function login(page: Page, name = "admin") {
  await page.goto("/");
  await page.getByRole("button", { name: "进入系统", exact: true }).click();
  await page.getByLabel("用户名", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password-123");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
  return (await (await page.request.get("/api/auth/session")).json())
    .csrf_token;
}
async function command(
  page: Page,
  csrf: string,
  path: string,
  body: any,
  method = "POST",
) {
  const r = await page.request.fetch("/api" + path, {
    method,
    data: body,
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
  });
  expect(r.ok(), method + " " + path + " " + r.status()).toBeTruthy();
  return r.json();
}
const area = (page: Page) => page.locator(".workspace-content > div:visible");
async function exportForm(page: Page, receipt: string) {
  await nav(page, "PACKING LIST");
  await area(page).getByLabel("收据号", { exact: true }).fill(receipt);
  await area(page)
    .getByRole("checkbox", { name: "选择 " + receipt, exact: true })
    .check();
  await area(page)
    .getByRole("button", { name: "导出 / 标签", exact: true })
    .click();
  return area(page).locator("form").filter({ hasText: "导出与标签" });
}
async function queueExport(
  page: Page,
  form: any,
  kind: string,
  format = "png",
) {
  await form.getByLabel("类型", { exact: true }).selectOption(kind);
  await form.getByLabel("文件格式", { exact: true }).selectOption(format);
  const response = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/exports" &&
      r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "保存", exact: true }).click();
  const r = await response;
  expect(r.ok()).toBeTruthy();
  return r.json();
}
async function nav(page: Page, name: string) {
  await page
    .locator(".sidebar")
    .getByRole("button", { name, exact: true })
    .click();
}
async function openNamed(page: Page, name: string) {
  const a = area(page);
  await a.locator("tbody tr").first().waitFor();
  for (
    let n = 0;
    n < 20 && !(await a.getByRole("button", { name, exact: true }).count());
    n++
  ) {
    const next = a.getByRole("button", { name: "下一页", exact: true });
    if (!(await next.isEnabled())) break;
    await next.click();
    await page.waitForTimeout(180);
  }
  await a.getByRole("button", { name, exact: true }).click();
}
async function sea(page: Page, csrf: string, transport = "SEA") {
  const ref = "FIX-SEA-" + Date.now();
  const wh = await command(page, csrf, "/warehouses", { name: ref });
  const party = await command(page, csrf, "/parties", {
    warehouse_id: wh.id,
    name: ref,
  });
  const shipment = await command(page, csrf, "/shipments", {
    warehouse_id: wh.id,
    reference: ref,
    transport,
    origin: "GZ",
    destination: "LAGOS",
  });
  const doc = await command(page, csrf, "/packing/documents", {
    shipment_id: shipment.id,
    reference: ref,
    uploaded_on: "2026-11-01",
  });
  const ticket = await command(page, csrf, "/tickets", {
    document_id: doc.id,
    party_id: party.id,
    receipt: ref,
    transport,
    origin: "GZ",
    pieces: 2,
    kg: "2",
    cbm: "2",
    contact_raw: "+2347000000000",
    phone_normalized: "+2347000000000",
    phone_confirmed: true,
    mark: ref,
    item_en: "Synthetic cargo",
    destination: "LAGOS",
    destination_warehouse_id: wh.id,
    address: "Synthetic destination",
    freight_usd: "2",
    clearing_ngn: "0",
    ngn_per_usd: "1500",
    charge_components: [
      {
        component: "freight",
        currency: "USD",
        unit: transport === "AIR" ? "KG" : "CBM",
        amount: "2",
        source: "Synthetic agreed charge",
      },
      {
        component: "clearing",
        currency: "NGN",
        unit: "CBM",
        amount: "0",
        source: "Explicit synthetic all-in",
      },
    ],
  });
  return { ticket, wh, party, doc, shipment };
}
test("I01 SEA has a separate billing-arrival detail flow, not an AIR row action", async ({
  page,
}) => {
  const csrf = await login(page);
  const data = await sea(page, csrf);
  await nav(page, "PACKING LIST");
  await area(page)
    .getByLabel("收据号", { exact: true })
    .fill(data.ticket.receipt);
  await openNamed(page, data.ticket.receipt);
  await expect(
    area(page).getByRole("button", {
      name: "海运港口 / 计费到货",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    area(page)
      .locator("tbody")
      .getByRole("button", { name: "到货", exact: true }),
  ).toHaveCount(0);
  await area(page)
    .getByRole("button", { name: "海运港口 / 计费到货", exact: true })
    .click();
  const form = area(page).locator(".detail-pane form");
  await form.getByLabel("件数", { exact: true }).fill("2");
  await form.getByLabel("KG", { exact: true }).fill("2");
  await form.getByLabel("CBM", { exact: true }).fill("2");
  await form.getByLabel("实际日期", { exact: true }).fill("2026-11-04");
  await form
    .getByLabel("依据 / 来源", { exact: true })
    .fill("Synthetic actual SEA port receipt");
  await capture(page,'sea-arrival',form);
  await form.getByRole("button", { name: "保存", exact: true }).click();
  await expect(form.getByText("已保存", { exact: true })).toBeVisible();
  const ticket = await (
    await page.request.get("/api/tickets/" + data.ticket.id)
  ).json();
  expect(ticket.arrivals).toHaveLength(1);
  expect(ticket.invoice_id).toBeTruthy();
  expect(ticket.actual_release_at).toBeNull();
  const negative = await page.request.post("/api/release-orders", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
    data: {
      members: [{ ticket_id: ticket.id, expected_version: ticket.version }],
      mode: "credit",
      promise_on: "2026-12-01",
      guarantor: "Synthetic",
    },
  });
  expect(negative.status()).toBe(409);
});

test("I02 destination-only staff upload a scoped photo and complete pickup", async ({
  page,
}) => {
  const csrf = await login(page);
  const data = await sea(page, csrf, "AIR");
  const dest = await command(page, csrf, "/warehouses", {
    name: "Synthetic pickup destination " + Date.now(),
  });
  let ticket = await command(
    page,
    csrf,
    "/tickets/" + data.ticket.id,
    {
      expected_version: data.ticket.version,
      destination_warehouse_id: dest.id,
    },
    "PATCH",
  );
  ticket = await command(page, csrf, "/tickets/" + ticket.id + "/arrive", {
    expected_version: ticket.version,
    pieces: 2,
    kg: "2",
    cbm: "2",
    occurred_on: "2026-11-02",
    source: "Synthetic billing arrival",
    mode: "air",
  });
  await command(page, csrf, "/tracking/nodes", {
    target_type: "ticket",
    target_id: ticket.id,
    expected_version: ticket.version,
    node: "warehouse_arrived",
    mode: "manual",
    destination_warehouse_id: dest.id,
    occurred_at: "2026-11-03T10:00:00+08:00",
    source: "Synthetic destination receipt",
  });
  ticket = await (await page.request.get("/api/tickets/" + ticket.id)).json();
  const order = await command(page, csrf, "/release-orders", {
    members: [{ ticket_id: ticket.id, expected_version: ticket.version }],
    mode: "credit",
    promise_on: "2026-12-01",
    guarantor: "Synthetic guarantor",
  });
  const foreignUpload=await page.request.post('/api/release-orders/'+order.id+'/photos',{
    headers:{'X-CSRF-Token':csrf,'Idempotency-Key':crypto.randomUUID()},
    multipart:{ticket_id:ticket.id,expected_version:String(order.version),file:{name:'another-actor.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC','base64')}}
  });
  expect(foreignUpload.status()).toBe(201);const foreignPhoto=(await foreignUpload.json()).id;
  const user = await command(page, csrf, "/admin/users", {
    username: "pickup-" + Date.now(),
    password: "synthetic-password-123",
  });
  await command(
    page,
    csrf,
    "/admin/users/" + user.id + "/grants",
    {
      expected_version: user.version,
      capabilities: ["release.read", "release.physical"],
      warehouse_ids: [dest.id],
    },
    "PUT",
  );
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  const destinationCSRF=await login(page, user.username);
  const foreignAttempt=await page.request.post('/api/physical-releases',{headers:{'X-CSRF-Token':destinationCSRF,'Idempotency-Key':crypto.randomUUID()},data:{order_id:order.id,expected_version:order.version,ticket_id:ticket.id,pieces:2,kg:'2',cbm:'2',occurred_at:'2026-11-04T12:00:00+08:00',photo_ids:[foreignPhoto]}});
  expect(foreignAttempt.status()).toBe(403);
  expect((await page.request.get("/api/tickets/" + ticket.id)).status()).toBe(
    403,
  );
  expect(
    (await page.request.get("/api/workspace/options?kind=tickets")).status(),
  ).toBe(403);
  await nav(page, "目的仓实际提货");
  const row = area(page).locator("tbody tr").filter({ hasText: order.id });
  await expect(row).toContainText(ticket.receipt);
  await expect(row).not.toContainText("[object Object]");
  await row.getByRole("button", { name: "查看", exact: true }).click();
  await area(page)
    .getByRole("button", { name: "记录实际提货", exact: true })
    .click();
  await area(page)
    .getByLabel("本指令可提货成员", { exact: true })
    .selectOption(ticket.id);
  const form = area(page).locator(".detail-pane form");
  await form.getByLabel("实际时间", { exact: true }).fill("2026-11-04T12:00");
  await form.locator("input[type=file]").setInputFiles({
    name: "pickup.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC",
      "base64",
    ),
  });
  await expect(form.getByText(/原件已保存/)).toBeVisible();
  await capture(page,'destination-pickup',form);
  const picked = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/physical-releases" &&
      r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "保存", exact: true }).click();
  expect((await picked).ok()).toBeTruthy();
  await area(page).getByText("查看提货照片原件", { exact: true }).click();
  await expect(area(page).locator("img.original-image")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "进入系统", exact: true }).click();
  await nav(page, "目的仓实际提货");
  await area(page)
    .locator("tbody tr")
    .filter({ hasText: order.id })
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await area(page)
    .getByRole("button", { name: "记录实际提货", exact: true })
    .click();
  await area(page).getByText("查看提货照片原件", { exact: true }).click();
  await expect(area(page).locator("img.original-image")).toBeVisible();
});
test("I04 clearing a saved contact is persisted and survives reopening", async ({
  page,
}) => {
  const csrf = await login(page);
  const wh = await command(page, csrf, "/warehouses", {
    name: "A FIX clear " + Date.now(),
    contact: "old synthetic contact",
    address: "old synthetic address",
  });
  await nav(page, "仓库资料");
  await openNamed(page, wh.name);
  await area(page).getByRole("button", { name: "编辑", exact: true }).click();
  const form = area(page).locator(".detail-pane form");
  await form.getByLabel("联系人", { exact: true }).fill("");
  await form
    .getByLabel("依据 / 来源", { exact: true })
    .fill("Synthetic obsolete contact correction");
  await form.getByRole("button", { name: "保存", exact: true }).click();
  await expect(form.getByText("已保存", { exact: true })).toBeVisible();
  const current = await (
    await page.request.get("/api/warehouses/" + wh.id)
  ).json();
  expect(current.contact).toBe("");
  await area(page)
    .getByRole("button", { name: "收起详情", exact: true })
    .click();
  await openNamed(page, wh.name);
  await area(page).getByRole("button", { name: "编辑", exact: true }).click();
  await expect(
    area(page)
      .locator(".detail-pane form")
      .getByLabel("联系人", { exact: true }),
  ).toHaveValue("");
});
test("I05 grants editor starts with current capabilities and warehouses", async ({
  page,
}) => {
  const csrf = await login(page);
  const wh = await command(page, csrf, "/warehouses", {
    name: "A FIX scope " + Date.now(),
  });
  const user = await command(page, csrf, "/admin/users", {
    username: "fix-grants-" + Date.now(),
    password: "synthetic-password-123",
  });
  await command(
    page,
    csrf,
    "/admin/users/" + user.id + "/grants",
    {
      expected_version: user.version,
      capabilities: ["warehouses.read", "cargo.read"],
      warehouse_ids: [wh.id],
    },
    "PUT",
  );
  await nav(page, "账号与权限");
  const row = area(page).locator("tbody tr").filter({ hasText: user.username });
  await row.getByRole("button", { name: "查看", exact: true }).click();
  await area(page)
    .getByRole("button", { name: "分配权限与仓库", exact: true })
    .click();
  await expect(area(page).locator(".detail-pane textarea")).toHaveValue(
    /warehouses\.read/,
  );
  await expect(area(page).locator(".detail-pane textarea")).toHaveValue(
    /cargo\.read/,
  );
  await expect(
    area(page).locator(".detail-pane select[multiple]"),
  ).toHaveValues([wh.id]);
  let form = area(page).locator(".detail-pane form");
  await form.getByRole("button", { name: "核对权限增删", exact: true }).click();
  await area(page)
    .getByRole("button", { name: "确认保存授权", exact: true })
    .click();
  await expect(
    area(page).getByRole("button", { name: "确认保存授权", exact: true }),
  ).toHaveCount(0);
  await expect(form.getByLabel("操作权限", { exact: true })).toHaveValue(
    /cargo.read/,
  );
  await form
    .getByLabel("操作权限", { exact: true })
    .fill("warehouses.read\ncargo.read\ndocuments.read");
  await form.getByRole("button", { name: "核对权限增删", exact: true }).click();
  await area(page)
    .getByRole("button", { name: "确认保存授权", exact: true })
    .click();
  await expect(
    area(page).getByRole("button", { name: "确认保存授权", exact: true }),
  ).toHaveCount(0);
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get("/api/admin/users/" + user.id + "/grants")
          ).json()
        ).capabilities,
    )
    .toEqual(["cargo.read", "documents.read", "warehouses.read"]);
  await form
    .getByLabel("操作权限", { exact: true })
    .fill("warehouses.read\ndocuments.read");
  await form.getByRole("button", { name: "核对权限增删", exact: true }).click();
  await expect(
    area(page).getByRole("button", { name: "确认保存授权", exact: true }),
  ).toBeDisabled();
  await capture(page,'grant-revocation',area(page).locator('.confirmation'));
  await area(page)
    .getByLabel("我确认撤销以上权限与仓库范围", { exact: true })
    .check();
  await area(page)
    .getByRole("button", { name: "确认保存授权", exact: true })
    .click();
  await expect(
    area(page).getByRole("button", { name: "确认保存授权", exact: true }),
  ).toHaveCount(0);
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get("/api/admin/users/" + user.id + "/grants")
          ).json()
        ).capabilities,
    )
    .toEqual(["documents.read", "warehouses.read"]);
  const current = await (
    await page.request.get("/api/admin/users/" + user.id + "/grants")
  ).json();
  await command(
    page,
    csrf,
    "/admin/users/" + user.id + "/grants",
    {
      expected_version: current.version,
      capabilities: [...current.capabilities, "cargo.read"],
      warehouse_ids: [wh.id],
    },
    "PUT",
  );
  await form
    .getByLabel("操作权限", { exact: true })
    .fill("warehouses.read\ndocuments.read\nparties.read");
  await form.getByRole("button", { name: "核对权限增删", exact: true }).click();
  const conflict = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/admin/users/" + user.id + "/grants" &&
      r.request().method() === "PUT",
  );
  await area(page)
    .getByRole("button", { name: "确认保存授权", exact: true })
    .click();
  expect((await conflict).status()).toBe(409);
  await expect(area(page).locator("[role=alert]").first()).toBeVisible();
  expect(
    (
      await (
        await page.request.get("/api/admin/users/" + user.id + "/grants")
      ).json()
    ).warehouse_ids,
  ).toEqual([wh.id]);
});
test("I08 tracking switches language in place without losing query/result", async ({
  page,
}) => {
  const ids = JSON.parse(
    readFileSync("../private/ui-validation/http-acceptance.json", "utf8"),
  ).synthetic_ids;
  await login(page);
  const ticket = await (
    await page.request.get("/api/tickets/" + ids.ticket)
  ).json();
  await page.goto("/");
  await page.locator('a[href="#tracking"]').click();
  await page
    .getByLabel("收据号 / 快递单号", { exact: true })
    .fill(ticket.receipt);
  await page.getByLabel("客户电话后四位", { exact: true }).fill("0000");
  const response = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/tracking/query",
  );
  await page.getByRole("button", { name: "查询", exact: true }).click();
  expect((await response).ok()).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "EN", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(
    page.getByLabel("Receipt / courier reference", { exact: true }),
  ).toHaveValue(ticket.receipt);
  await expect(
    page.getByLabel("Last four phone digits", { exact: true }),
  ).toHaveValue("0000");
  await expect(
    page.getByText(ticket.receipt, { exact: true }).last(),
  ).toBeVisible();
  await capture(page,'tracking-language',page.locator('.public-form'));
});

test("I06 ready PNG copy revalidates the current source at the server", async ({
  page,
  browser,
}) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const csrf = await login(page);
  const data = await sea(page, csrf);
  await nav(page, "PACKING LIST");
  await area(page)
    .getByLabel("收据号", { exact: true })
    .fill(data.ticket.receipt);
  await area(page)
    .getByRole("checkbox", { name: "选择 " + data.ticket.receipt, exact: true })
    .check();
  await area(page)
    .getByRole("button", { name: "导出 / 标签", exact: true })
    .click();
  const form = area(page).locator("form").filter({ hasText: "导出与标签" });
  await form.getByLabel("类型", { exact: true }).selectOption("label_plain");
  await form.getByLabel("文件格式", { exact: true }).selectOption("png");
  const queued = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/exports" &&
      r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "保存", exact: true }).click();
  const job = await (await queued).json();
  const copy = area(page).getByRole("button", {
    name: "复制图片",
    exact: true,
  });
  await expect(copy).toBeEnabled({ timeout: 20000 });
  const second = await browser.newContext({
    baseURL: process.env.MCSTAR_TEST_URL || "http://127.0.0.1:8014",
  });
  const other = await second.newPage();
  const otherCSRF = await login(other);
  await command(
    other,
    otherCSRF,
    "/tickets/" + data.ticket.id,
    {
      expected_version: data.ticket.version,
      mark: "Changed by another synthetic session",
    },
    "PATCH",
  );
  await second.close();
  const direct = await page.request.get(
    "/api/exports/" + job.id + "/clipboard",
  );
  expect(direct.status()).toBe(409);
  let checked = false;
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/exports/" + job.id + "/clipboard")
      checked = true;
  });
  await copy.click();
  await expect.poll(() => checked).toBe(true);
  await expect(
    area(page).getByText("导出后来源已有变更，请重新生成文件。", {
      exact: true,
    }),
  ).toBeVisible();
});

test("I07 copy history exposes cancellation for the original persisted copy", async ({
  page,
}) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const csrf = await login(page);
  const data = await sea(page, csrf);
  await command(page, csrf, "/tracking/nodes", {
    target_type: "shipment",
    target_id: data.shipment.id,
    expected_version: data.shipment.version,
    node: "loading",
    mode: "manual",
    occurred_at: "2026-11-01T08:00:00+08:00",
    source: "Synthetic actual loading",
  });
  await nav(page, "PACKING LIST");
  await area(page)
    .getByLabel("收据号", { exact: true })
    .fill(data.ticket.receipt);
  await area(page)
    .getByRole("button", { name: "装柜通知", exact: true })
    .click();
  const copy = area(page).getByRole("button", {
    name: "复制图片",
    exact: true,
  });
  await expect(copy).toBeEnabled();
  await copy.click();
  await expect(
    area(page).getByText("已复制并保存标记；尚未投递。", { exact: true }),
  ).toBeVisible();
  const ticketRow = area(page)
    .locator("tbody tr")
    .filter({ hasText: data.ticket.receipt });
  await expect(ticketRow).toHaveClass(/loading-notified/);
  const originalMarks = await (
    await page.request.get(
      "/api/tickets/" + data.ticket.id + "/notification-marks",
    )
  ).json();
  expect(originalMarks).toHaveLength(1);
  await area(page)
    .locator(".notice-panel")
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  const document = await (
    await page.request.get("/api/packing/documents/" + data.doc.id)
  ).json();
  await command(page, csrf, "/packing/documents/" + data.doc.id + "/split", {
    expected_version: document.version,
    ticket_ids: [data.ticket.id],
    reference: "Synthetic moved original member",
  });
  await area(page).getByRole("button", { name: "刷新", exact: true }).click();
  await expect(ticketRow).toHaveClass(/loading-notified/);
  await openNamed(page, data.ticket.receipt);
  await capture(page,'loading-mark',ticketRow);
  await area(page)
    .getByRole("button", { name: "复制记录", exact: true })
    .click();
  await area(page)
    .getByRole("button", { name: "撤销此复制标记", exact: true })
    .click();
  const form = area(page).locator(".detail-pane form");
  await form
    .getByLabel("原因", { exact: true })
    .fill("Synthetic wrong original copy");
  await capture(page,'copy-cancellation',form);
  const cancelled = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname ===
      "/api/notification-copies/" + originalMarks[0].copy_id + "/cancel",
  );
  await form
    .getByRole("button", { name: "确认撤销原复制标记", exact: true })
    .click();
  const result = await (await cancelled).json();
  expect(result.ticket_ids).toEqual([data.ticket.id]);
  await expect(ticketRow).not.toHaveClass(/loading-notified/);
  await expect(
    area(page).getByText("已撤销复制标记", { exact: false }).first(),
  ).toBeVisible();
  const history = await (
    await page.request.get(
      "/api/tickets/" + data.ticket.id + "/notification-marks?history=true",
    )
  ).json();
  expect(history[0].copy_id).toBe(originalMarks[0].copy_id);
  expect(history[0].cancelled_at).toBeTruthy();
});

test("I06 new PNG jobs discard delayed old bytes and failed/non-PNG jobs cannot copy prior PNG", async ({
  page,
}) => {
  test.setTimeout(90000);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const csrf = await login(page);
  const data = await sea(page, csrf);
  const form = await exportForm(page, data.ticket.receipt);
  let releaseOld!: () => void;
  const gate = new Promise<void>((resolve) => (releaseOld = resolve));
  let held = false,
    oldPath = "";
  await page.route("**/api/exports/*/clipboard", async (route) => {
    if (!oldPath) {
      oldPath = new URL(route.request().url()).pathname;
      held = true;
      await gate;
    }
    await route.continue();
  });
  const a = await queueExport(page, form, "packing");
  await expect.poll(() => held, { timeout: 20000 }).toBe(true);
  const b = await queueExport(page, form, "label_plain");
  const copy = area(page).getByRole("button", {
    name: "复制图片",
    exact: true,
  });
  await expect(copy).toBeEnabled({ timeout: 20000 });
  releaseOld();
  await page.waitForTimeout(300);
  await copy.click();
  await expect(
    area(page).getByText("已复制当前任务图片", { exact: true }),
  ).toBeVisible();
  const pixels = await page.evaluate(async (identity) => {
    async function digest(blob: Blob) {
      const im = await createImageBitmap(blob);
      const canvas = document.createElement("canvas");
      canvas.width = im.width;
      canvas.height = im.height;
      const context = canvas.getContext("2d")!;
      context.drawImage(im, 0, 0);
      const rgba = context.getImageData(0, 0, im.width, im.height).data;
      const hash = Array.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", rgba)),
      )
        .map((n) => n.toString(16).padStart(2, "0"))
        .join("");
      return { width: im.width, height: im.height, hash };
    }
    return {
      expected: await digest(
        await (await fetch("/api/exports/" + identity + "/clipboard")).blob(),
      ),
      actual: await digest(
        await (await navigator.clipboard.read())[0].getType("image/png"),
      ),
    };
  }, b.id);
  expect(pixels.actual).toEqual(pixels.expected);
  expect(a.id).not.toBe(b.id);
  await page.unroute("**/api/exports/*/clipboard");
  await page.route("**/api/exports/*/clipboard", (route) =>
    route.abort("failed"),
  );
  await queueExport(page, form, "label_fee");
  await expect(
    area(page).getByRole("button", { name: "下载文件", exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await expect(copy).toBeDisabled();
  await expect(area(page).locator("[role=alert]").first()).toBeVisible();
  await page.unroute("**/api/exports/*/clipboard");
  let pngRequests = 0;
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.endsWith("/clipboard")) pngRequests++;
  });
  for (const format of ["pdf", "xlsx"]) {
    await queueExport(page, form, "packing", format);
    await expect(
      area(page).getByRole("button", { name: "下载文件", exact: true }),
    ).toBeVisible({ timeout: 20000 });
    await expect(copy).toBeDisabled();
  }
  expect(pngRequests).toBe(0);
  await capture(page,'export-non-png',form);
});

test("I06 delayed manual refresh cannot replace a newer ready export job", async ({page})=>{
  test.setTimeout(60000);
  const csrf=await login(page),data=await sea(page,csrf),form=await exportForm(page,data.ticket.receipt);
  const a=await queueExport(page,form,'packing');
  const copy=area(page).getByRole('button',{name:'复制图片',exact:true});
  await expect(copy).toBeEnabled({timeout:20000});
  let releaseRefresh!:()=>void,held=false;
  const gate=new Promise<void>(resolve=>{releaseRefresh=resolve});
  const oldStatus='**/api/exports/'+a.id;
  await page.route(oldStatus,async route=>{held=true;await gate;await route.continue()});
  await area(page).getByRole('button',{name:'刷新任务状态',exact:true}).click();
  await expect.poll(()=>held).toBe(true);
  const b=await queueExport(page,form,'label_plain');
  await expect(copy).toBeEnabled({timeout:20000});
  await expect(area(page).getByText(b.id,{exact:true})).toBeVisible();
  const late=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/exports/'+a.id);
  releaseRefresh();await late;await page.waitForTimeout(200);
  await expect(area(page).getByText(b.id,{exact:true})).toBeVisible();
  await expect(area(page).getByText(a.id,{exact:true})).toHaveCount(0);
  await page.unroute(oldStatus);
});

test("I06 a ready PNG cannot be copied after another session revokes export access", async ({
  page,
  browser,
}) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  const csrf = await login(page);
  const data = await sea(page, csrf);
  const user = await command(page, csrf, "/admin/users", {
    username: "exporter-" + Date.now(),
    password: "synthetic-password-123",
  });
  const caps = [
    "documents.read",
    "documents.export",
    "packing.read",
    "finance.read",
    "warehouses.read",
  ];
  const grants = await command(
    page,
    csrf,
    "/admin/users/" + user.id + "/grants",
    {
      expected_version: user.version,
      capabilities: caps,
      warehouse_ids: [data.wh.id],
    },
    "PUT",
  );
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await login(page, user.username);
  const form = await exportForm(page, data.ticket.receipt);
  const job = await queueExport(page, form, "label_plain");
  const copy = area(page).getByRole("button", {
    name: "复制图片",
    exact: true,
  });
  await expect(copy).toBeEnabled({ timeout: 20000 });
  const otherContext = await browser.newContext({
    baseURL: process.env.MCSTAR_TEST_URL || "http://127.0.0.1:8014",
  });
  const other = await otherContext.newPage();
  const token = await login(other);
  await command(
    other,
    token,
    "/admin/users/" + user.id + "/grants",
    {
      expected_version: grants.version,
      capabilities: caps.filter((c) => c !== "documents.export"),
      warehouse_ids: [data.wh.id],
    },
    "PUT",
  );
  await otherContext.close();
  const denied = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/exports/" + job.id + "/clipboard",
  );
  await copy.click();
  expect((await denied).status()).toBe(403);
  await expect(copy).toBeDisabled();
  await expect(area(page).locator("[role=alert]").first()).toBeVisible();
});

test("I03 payment and same-state claim pickers select beyond fifty without downloading the whole set", async ({
  page,
}) => {
  test.setTimeout(60000);
  const fixture = JSON.parse(
    execFileSync(
      "../.venv/Scripts/python.exe",
      [
        "../tools/review_browser_fixture.py",
        readFileSync(
          "../private/ui-validation/latest-profile.txt",
          "utf8",
        ).trim(),
      ],
      { encoding: "utf8" },
    ),
  );
  await login(page);
  await nav(page, "应收账款");
  await area(page)
    .getByLabel("搜索 所属仓库", { exact: true })
    .fill(fixture.prefix);
  await area(page)
    .getByLabel("所属仓库", { exact: true })
    .selectOption(fixture.warehouse);
  await area(page).getByLabel("查找内容", { exact: true }).fill(fixture.prefix);
  await openNamed(page, fixture.prefix);
  await area(page)
    .getByRole("button", { name: "登记付款凭证（不计入实收）", exact: true })
    .click();
  const form = area(page).locator(".detail-pane form");
  await form.getByLabel("搜索 付款", { exact: true }).fill(fixture.prefix);
  const picker = form
    .locator(".picker")
    .filter({ has: page.locator('select[aria-label="付款"]') });
  await expect(
    picker.getByRole("button", { name: "下一组选项", exact: true }),
  ).toBeEnabled();
  await picker.getByRole("button", { name: "下一组选项", exact: true }).click();
  await form
    .getByLabel("付款", { exact: true })
    .selectOption(fixture.payments.at(-1));
  await capture(page,'payment-page-two',form);
  await picker.getByRole("button", { name: "上一组选项", exact: true }).click();
  await expect(form.getByLabel("付款", { exact: true })).toHaveValue(
    fixture.payments.at(-1),
  );
  await form
    .getByLabel("依据 / 来源", { exact: true })
    .fill("Synthetic later payment proof");
  await form.getByRole("button", { name: "保存", exact: true }).click();
  await expect(form.getByText("已保存", { exact: true })).toBeVisible();
  await nav(page, "历史银行证据补充");
  await area(page)
    .getByLabel("搜索 银行占用", { exact: true })
    .fill(fixture.prefix);
  const claims = area(page)
    .locator(".picker")
    .filter({ has: page.locator('select[aria-label="银行占用"]') });
  await expect(
    claims.getByRole("button", { name: "下一组选项", exact: true }),
  ).toBeEnabled();
  await claims.getByRole("button", { name: "下一组选项", exact: true }).click();
  await area(page)
    .getByLabel("银行占用", { exact: true })
    .selectOption(fixture.claims.at(-1));
  await expect(area(page).getByLabel("银行占用", { exact: true })).toHaveValue(
    fixture.claims.at(-1),
  );
  await area(page)
    .getByLabel("搜索 银行占用", { exact: true })
    .fill(fixture.claims.at(-1));
  await expect(
    area(page).getByLabel("银行占用", { exact: true }).locator("option"),
  ).toHaveCount(2);
});

test("I01 SEA unknown non-KG weight stays null and port nodes cannot create warehouse stock", async ({
  page,
}) => {
  const csrf = await login(page);
  const data = await sea(page, csrf);
  let ticket = await command(
    page,
    csrf,
    "/tickets/" + data.ticket.id,
    {
      expected_version: data.ticket.version,
      kg: null,
      repricing: "retain_totals",
      reason: "Original non-KG source omits weight",
    },
    "PATCH",
  );
  await command(page, csrf, "/tracking/nodes", {
    target_type: "ticket",
    target_id: ticket.id,
    expected_version: ticket.version,
    node: "arrived_port",
    mode: "manual",
    occurred_at: "2026-11-03T10:00:00+08:00",
    source: "Synthetic SEA port observation",
  });
  ticket = await (await page.request.get("/api/tickets/" + ticket.id)).json();
  expect(ticket.arrivals).toEqual([]);
  expect(ticket.invoice_id).toBeNull();
  const before = await page.request.post("/api/tracking/nodes", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
    data: {
      target_type: "ticket",
      target_id: ticket.id,
      expected_version: ticket.version,
      node: "warehouse_arrived",
      mode: "manual",
      destination_warehouse_id: data.wh.id,
      occurred_at: "2026-11-03T12:00:00+08:00",
      source: "Synthetic invalid pre-arrival receipt",
    },
  });
  expect(before.status()).toBe(409);
  expect((await before.json()).error.code).toBe("warehouse_coverage");
  await nav(page, "PACKING LIST");
  await area(page).getByLabel("收据号", { exact: true }).fill(ticket.receipt);
  await openNamed(page, ticket.receipt);
  await area(page)
    .getByRole("button", { name: "海运港口 / 计费到货", exact: true })
    .click();
  const form = area(page).locator(".detail-pane form");
  await form.getByLabel("件数", { exact: true }).fill("2");
  await expect(form.getByLabel("KG", { exact: true })).toHaveValue("");
  await form.getByLabel("CBM", { exact: true }).fill("2");
  await form.getByLabel("实际日期", { exact: true }).fill("2026-11-04");
  await form
    .getByLabel("依据 / 来源", { exact: true })
    .fill("Synthetic non-KG SEA original with unknown weight");
  await form.getByRole("button", { name: "保存", exact: true }).click();
  await expect(form.getByText("已保存", { exact: true })).toBeVisible();
  const after = await (
    await page.request.get("/api/tickets/" + ticket.id)
  ).json();
  expect(after.arrivals[0].kg).toBeNull();
  expect(after.invoice_id).toBeTruthy();
});

test("I08 English classification options keep their original stored values", async ({
  page,
}) => {
  const csrf = await login(page);
  const wh = await command(page, csrf, "/warehouses", {
    name: "A FIX English category " + Date.now(),
  });
  await page.getByLabel("语言", { exact: true }).selectOption("en");
  await nav(page, "Customers & counterparties");
  await area(page)
    .getByRole("button", { name: "New counterparty", exact: true })
    .click();
  const form = area(page).locator(".detail-pane form");
  await form.getByLabel("Search Warehouse id", { exact: true }).fill(wh.name);
  await form.getByLabel("Warehouse id", { exact: true }).selectOption(wh.id);
  await form
    .getByLabel("Name", { exact: true })
    .fill("Synthetic untranslated party 原文");
  const classification = form.getByLabel("Classification", { exact: true });
  await expect(classification.locator('option[value="客户"]')).toHaveText(
    "Customer",
  );
  await expect(classification.locator('option[value="清关代理"]')).toHaveText(
    "Clearance agent",
  );
  await classification.selectOption("供应商");
  await capture(page,'english-classification',form);
  const saved = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/parties" &&
      r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "Save", exact: true }).click();
  const result = await (await saved).json();
  expect(result.classification).toBe("供应商");
  expect(result.name).toBe("Synthetic untranslated party 原文");
});

test("I04 clearing phone and unknown SEA weight never removes a manufacturer hold or writes zero", async ({
  page,
}) => {
  const csrf = await login(page);
  const data = await sea(page, csrf);
  await command(
    page,
    csrf,
    "/tickets/" + data.ticket.id,
    {
      expected_version: data.ticket.version,
      contact_raw: "ON HOLD BY synthetic supplier",
      phone_normalized: null,
      phone_confirmed: false,
    },
    "PATCH",
  );
  await nav(page, "PACKING LIST");
  await area(page)
    .getByLabel("收据号", { exact: true })
    .fill(data.ticket.receipt);
  await openNamed(page, data.ticket.receipt);
  await area(page).getByRole("button", { name: "编辑", exact: true }).click();
  const form = area(page).locator(".detail-pane form");
  await form.getByLabel("客户电话原文", { exact: true }).fill("");
  await form.getByLabel("KG", { exact: true }).fill("");
  await form
    .getByLabel("重新计价决定", { exact: true })
    .selectOption("retain_totals");
  await form
    .getByLabel("原因", { exact: true })
    .fill(
      "Source weight unknown; preserve agreed non-KG totals and independent hold",
    );
  await form.getByRole("button", { name: "核对变更预览", exact: true }).click();
  const changed = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/tickets/" + data.ticket.id &&
      r.request().method() === "PATCH",
  );
  await area(page)
    .getByRole("button", { name: "确认执行上述变更", exact: true })
    .click();
  expect((await changed).ok()).toBeTruthy();
  const saved = await (
    await page.request.get("/api/tickets/" + data.ticket.id)
  ).json();
  expect(saved.kg).toBeNull();
  expect(saved.contact_raw).toBe("");
  expect(saved.phone_normalized).toBeNull();
  expect(
    (
      await (
        await page.request.get("/api/tickets/" + data.ticket.id + "/holds")
      ).json()
    ).ticket.active,
  ).toBe(true);
});

test("I04 nested evidence dates and explicit bank-account clearing survive reload", async ({
  page,
}) => {
  const csrf = await login(page);
  const data = await sea(page, csrf);
  const account = await command(page, csrf, "/bank/accounts", {
    warehouse_id: data.wh.id,
    bank_name: "Synthetic",
    account_number: "CLEAR-" + Date.now(),
    currency: "NGN",
  });
  const user = (await (await page.request.get("/api/auth/session")).json())
    .user;
  const uploaded = await page.request.post("/api/attachments", {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID() },
    multipart: {
      owner_type: "user",
      owner_id: user.id,
      file: {
        name: "synthetic.png",
        mimeType: "image/png",
        buffer: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC",
          "base64",
        ),
      },
    },
  });
  expect(uploaded.status()).toBe(201);
  const evidence = await command(page, csrf, "/evidence", {
    warehouse_id: data.wh.id,
    account_id: account.id,
    fields: {
      transaction_date: "2026-11-01",
      amount: "10",
      currency: "NGN",
      business_reference: data.ticket.receipt,
    },
    images: [
      {
        attachment_id: (await uploaded.json()).id,
        source: "Synthetic original evidence",
      },
    ],
  });
  await nav(page, "水单审核");
  await area(page)
    .getByLabel("搜索 所属仓库", { exact: true })
    .fill(data.wh.name);
  await area(page)
    .getByLabel("所属仓库", { exact: true })
    .selectOption(data.wh.id);
  const row = area(page).locator("tbody tr").filter({ hasText: evidence.id });
  await row.getByRole("button", { name: "查看", exact: true }).click();
  await area(page)
    .getByRole("button", { name: "人工核对水单", exact: true })
    .click();
  let form = area(page).locator(".detail-pane form");
  await form.getByLabel("处理动作", { exact: true }).selectOption("correct");
  await form.getByLabel("银行交易日期", { exact: true }).fill("");
  await form
    .getByLabel("原因", { exact: true })
    .fill("Original date cannot be confirmed");
  await form.getByRole("button", { name: "保存", exact: true }).click();
  await expect(form.getByText("已保存", { exact: true })).toBeVisible();
  expect(
    (await (await page.request.get("/api/evidence/" + evidence.id)).json())
      .transaction_date,
  ).toBeNull();
  await area(page)
    .getByRole("button", { name: "选择核对银行账户", exact: true })
    .click();
  form = area(page).locator(".detail-pane form");
  await form
    .getByLabel("原因", { exact: true })
    .fill("Original account cannot be confirmed");
  await form.getByRole("button", { name: "保存", exact: true }).click();
  await expect(form.getByText("已保存", { exact: true })).toBeVisible();
  const saved = await (
    await page.request.get("/api/evidence/" + evidence.id)
  ).json();
  expect(saved.account_id).toBeNull();
  expect(saved.reviews.some((r: any) => r.fields.account_id === null)).toBe(
    true,
  );
  await area(page)
    .getByRole("button", { name: "人工核对水单", exact: true })
    .click();
  await expect(
    area(page)
      .locator(".detail-pane form")
      .getByLabel("银行交易日期", { exact: true }),
  ).toHaveValue("");
});
