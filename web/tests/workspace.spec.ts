import { test, expect } from "@playwright/test";
import { readFileSync } from 'node:fs';

async function signIn(page: any, name='admin') {
  await page.goto('/'); await page.getByRole('button',{name:'进入系统',exact:true}).click();
  await page.getByLabel('用户名',{exact:true}).fill(name); await page.getByLabel('密码',{exact:true}).fill('synthetic-password-123');
  await page.getByRole('button',{name:'登录',exact:true}).click(); await expect(page.getByText(name,{exact:true}).first()).toBeVisible();
}

test("fresh open welcomes; login reload restores CSRF; form save preserves focus and detail", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "MC STAR LOGISTICS", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("密码")).toHaveCount(0);
  await page.getByRole("button", { name: "进入系统", exact: true }).click();
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password-123");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("admin", { exact: true }).first()).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "MC STAR LOGISTICS", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "进入系统", exact: true }).click();
  await page.getByRole("button", { name: "仓库资料", exact: true }).click();
  await page.getByRole("button", { name: "新增仓库", exact: true }).click();
  await page
    .getByLabel("仓库名称", { exact: true })
    .fill("SYNTHETIC browser warehouse");
  const createdResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/warehouses'&&r.request().method()==='POST');
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const createdId=(await(await createdResponse).json()).id;
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  await expect(page.getByLabel("仓库名称", { exact: true })).toHaveValue(
    "SYNTHETIC browser warehouse",
  );
  const repeatedResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/warehouses'&&r.request().method()==='POST');
  await page.getByRole('button',{name:'保存',exact:true}).click();const repeated=await repeatedResponse;expect(repeated.status()).toBe(200);expect((await repeated.json()).id).toBe(createdId);
});

test('background refresh preserves dirty input and focus; sign-out clears actor state', async({page})=>{
  await signIn(page);
  await page.getByRole('button',{name:'仓库资料',exact:true}).click();
  await page.getByRole('button',{name:'新增仓库',exact:true}).click();
  const name=page.getByLabel('仓库名称',{exact:true}); await name.fill('SYNTHETIC private draft');
  await name.focus(); await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(name).toBeFocused(); await expect(name).toHaveValue('SYNTHETIC private draft');
  await page.getByRole('button',{name:'刷新',exact:true}).click();
  await name.focus(); await expect(name).toHaveValue('SYNTHETIC private draft');
  await page.getByRole('button',{name:'退出登录',exact:true}).click();
  await signIn(page,'restricted');
  await expect(page.getByRole('button',{name:'仓库资料',exact:true})).toHaveCount(0);
  await expect(page.getByDisplayValue ? page.getByDisplayValue('SYNTHETIC private draft') : page.locator('input[value="SYNTHETIC private draft"]')).toHaveCount(0);
  const denied=await page.request.get('/api/warehouses'); expect(denied.status()).toBe(403);
});

test('loading notice uses actual PNG clipboard bytes; failure never saves a copy event',async({page})=>{
  const evidence=JSON.parse(readFileSync('../private/ui-validation/http-acceptance.json','utf-8'));
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  await signIn(page);
  const ticket=await (await page.request.get('/api/tickets/'+evidence.synthetic_ids.ticket)).json();
  await page.getByRole('button',{name:'PACKING LIST',exact:true}).click();
  await page.getByLabel('收据号',{exact:true}).fill(ticket.receipt);
  await page.getByRole('button',{name:ticket.receipt,exact:true}).click();
  await page.getByRole('button',{name:'物流节点',exact:true}).click();
  const nodeForm=page.locator('.detail-pane form');
  await nodeForm.locator('select').first().selectOption('loading');
  await nodeForm.locator('input[type="datetime-local"]').fill('2026-11-02T10:00');
  await nodeForm.getByLabel('依据 / 来源',{exact:true}).fill('Synthetic actual loading evidence');
  await nodeForm.getByRole('button',{name:'保存',exact:true}).click();
  await expect(nodeForm.getByText('已保存',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'装柜通知',exact:true}).click();
  const copy=page.getByRole('button',{name:'复制图片',exact:true}); await expect(copy).toBeEnabled();
  await copy.click(); await expect(page.getByRole('status').filter({hasText:'已复制并保存标记'})).toBeVisible();
  const actual=await page.evaluate(async()=>{const items=await navigator.clipboard.read();const png=await items[0].getType('image/png');return {type:png.type,size:png.size,signature:Array.from(new Uint8Array(await png.arrayBuffer()).slice(0,8))}});
  expect(actual.type).toBe('image/png'); expect(actual.size).toBeGreaterThan(100);expect(actual.signature).toEqual([137,80,78,71,13,10,26,10]);
  const marksBefore=await (await page.request.get('/api/tickets/'+ticket.id+'/notification-marks')).json();
  await page.evaluate(()=>Object.defineProperty(navigator.clipboard,'write',{value:async()=>{throw new DOMException('Synthetic denied','NotAllowedError')},configurable:true}));
  await copy.click(); await expect(page.getByRole('status').filter({hasText:'复制失败'})).toBeVisible();
  const marksAfter=await (await page.request.get('/api/tickets/'+ticket.id+'/notification-marks')).json();expect(marksAfter.length).toBe(marksBefore.length);
});

test("all public translations and unknown tracking proof", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "FR", exact: true }).click();
  await expect(page.getByText("Fret aérien", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await page.getByRole("link", { name: /客户自助查询/ }).click();
  await page.getByLabel("收据号 / 快递单号").fill("SYNTHETIC-no-record");
  await page.getByLabel("客户电话后四位").fill("1234");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("未找到");
});

test('stale edits retain dirty values; offline save never reports persistence',async({page})=>{
  await signIn(page);
  const session=await(await page.request.get('/api/auth/session')).json();
  const headers={'X-CSRF-Token':session.csrf_token,'Idempotency-Key':crypto.randomUUID()};
  const name='SYNTHETIC conflict '+Date.now();
  const created=await page.request.post('/api/warehouses',{headers,data:{name}});expect(created.status()).toBe(200);const warehouse=await created.json();
  await page.getByRole('button',{name:'仓库资料',exact:true}).click();await page.getByRole('button',{name,exact:true}).click();await page.getByRole('button',{name:'编辑',exact:true}).click();
  const form=page.locator('.detail-pane form');await form.getByLabel('仓库名称',{exact:true}).fill(name+' unsaved');await form.getByLabel('依据 / 来源',{exact:true}).fill('Synthetic reviewed correction');
  const concurrent=await page.request.patch('/api/warehouses/'+warehouse.id,{headers:{...headers,'Idempotency-Key':crypto.randomUUID()},data:{expected_version:warehouse.version,name:name+' committed',source:'Synthetic concurrent correction'}});expect(concurrent.status()).toBe(200);
  const stale=page.waitForResponse(r=>r.request().method()==='PATCH'&&new URL(r.url()).pathname==='/api/warehouses/'+warehouse.id);
  await form.getByRole('button',{name:'保存',exact:true}).click();expect((await stale).status()).toBe(409);
  await expect(form.getByLabel('仓库名称',{exact:true})).toHaveValue(name+' unsaved');await expect(page.getByRole('alert')).toContainText('版本');
  await page.context().setOffline(true);await form.getByRole('button',{name:'保存',exact:true}).click();await expect(page.getByRole('alert')).toContainText('网络');
  await expect(form.getByLabel('仓库名称',{exact:true})).toHaveValue(name+' unsaved');await expect(form.getByText('已保存',{exact:true})).toHaveCount(0);
  await page.context().setOffline(false);
  expect((await(await page.request.get('/api/warehouses/'+warehouse.id)).json()).name).toBe(name+' committed');
});

test('revoking the active actor clears mounted drafts and rejects new direct reads',async({page,browser})=>{
  await signIn(page);
  const auth=await(await page.request.get('/api/auth/session')).json();
  const headers=()=>({'X-CSRF-Token':auth.csrf_token,'Idempotency-Key':crypto.randomUUID()});
  const warehouse=await(await page.request.post('/api/warehouses',{headers:headers(),data:{name:'SYNTHETIC permission scope '+Date.now()}})).json();
  const username='scope-'+Date.now();const created=await page.request.post('/api/admin/users',{headers:headers(),data:{username,password:'synthetic-password-123'}});expect(created.status()).toBe(201);const user=await created.json();
  const granted=await page.request.put('/api/admin/users/'+user.id+'/grants',{headers:headers(),data:{expected_version:user.version,capabilities:['warehouses.read','warehouses.manage'],warehouse_ids:[warehouse.id]}});expect(granted.status()).toBe(200);
  const context=await browser.newContext({baseURL:new URL(page.url()).origin});const operator=await context.newPage();await signIn(operator,username);
  await operator.getByRole('button',{name:'仓库资料',exact:true}).click();await operator.getByRole('button',{name:'新增仓库',exact:true}).click();await operator.getByLabel('仓库名称',{exact:true}).fill('SYNTHETIC revoked actor private draft');
  const revoked=await page.request.put('/api/admin/users/'+user.id+'/grants',{headers:headers(),data:{expected_version:(await granted.json()).version,capabilities:[],warehouse_ids:[]}});expect(revoked.status()).toBe(200);
  await operator.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(operator.getByRole('button',{name:'仓库资料',exact:true})).toHaveCount(0);await expect(operator.locator('input[value="SYNTHETIC revoked actor private draft"]')).toHaveCount(0);
  expect((await operator.request.get('/api/warehouses')).status()).toBe(403);await context.close();
});
