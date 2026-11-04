import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
test('real customer ownership flow, reload CSRF, own export, logout and another phone',async({page})=>{
  test.setTimeout(90000);
  page.setDefaultTimeout(10000);
  const base='http://127.0.0.1:8018';
  const profile=JSON.parse(readFileSync(readFileSync('../private/ui-validation/latest-profile.txt','utf8').trim(),'utf8'));
  const enter=async()=>{await page.getByRole('link',{name:/客户自助查询/}).click();await page.getByRole('button',{name:'验证本人手机号 / 本人箱单',exact:true}).click()};
  await page.goto(base);await enter();
  async function verify(number:string){await page.getByLabel('电话',{exact:true}).fill(number);const sent=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/customer/verification');await page.getByRole('button',{name:'发送验证码',exact:true}).click();expect((await sent).status()).toBe(200);await expect(page.getByLabel('验证码',{exact:true})).toBeVisible();const delivery=JSON.parse(readFileSync(profile.private_root+'/phone-transport.json','utf8'));await page.getByLabel('验证码',{exact:true}).fill(delivery.code);await page.getByRole('button',{name:'验证号码归属',exact:true}).click();await expect(page.getByRole('button',{name:'退出客户账号',exact:true})).toBeVisible()}
  await verify('+2347000000000');await expect(page.locator('tbody tr').first()).toBeVisible();
  expect((await page.request.get(base+'/api/receipts')).status()).toBe(401);
  await page.reload();await enter();await expect(page.locator('tbody tr').first()).toBeVisible();
  await page.locator('tbody input[type=checkbox]').first().check();await page.getByRole('combobox').selectOption('xlsx');const exported=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/customer/exports'&&r.request().method()==='POST');await page.getByRole('button',{name:'导出本人箱单',exact:true}).click();expect((await exported).status()).toBe(200);
  await expect(page.getByRole('button',{name:'下载文件',exact:true})).toBeEnabled({timeout:60000});
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'下载文件',exact:true}).click();expect((await download).suggestedFilename()).toMatch(/xlsx$/);
  await page.getByRole('button',{name:'退出客户账号',exact:true}).click();await expect(page.locator('tbody tr')).toHaveCount(0);expect((await page.request.get(base+'/api/customer/documents')).status()).toBe(401);
  await verify('+2347000099999');await expect(page.locator('tbody tr')).toHaveCount(0);
});
