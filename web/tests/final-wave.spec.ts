import {test,expect,type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
const profilePath=readFileSync('../private/ui-validation/latest-profile.txt','utf8').trim();
const profile=JSON.parse(readFileSync(profilePath,'utf8'));
async function login(page:Page){await page.goto('/');await page.getByRole('button',{name:'进入系统',exact:true}).click();await page.getByLabel('用户名',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('synthetic-password-123');await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.getByText('admin',{exact:true}).first()).toBeVisible();return (await(await page.request.get('/api/auth/session')).json()).csrf_token}
async function cmd(page:Page,csrf:string,path:string,body:any,method='POST'){const r=await page.request.fetch('/api'+path,{method,data:body,headers:{'X-CSRF-Token':csrf,'Idempotency-Key':crypto.randomUUID()}});expect(r.ok(),path+' '+r.status()).toBeTruthy();return r.json()}
function worker(){execFileSync('../.venv/Scripts/python.exe',['-m','app.domain_worker','document-export'],{env:{...process.env,MCSTAR_CONFIG:profilePath,PYTHONPATH:'../backend'},stdio:'pipe',timeout:60000})}
async function customer(page:Page){
  const csrf=await login(page),wh=await cmd(page,csrf,'/warehouses',{name:'Synthetic final browser '+Date.now()}),party=await cmd(page,csrf,'/parties',{warehouse_id:wh.id,name:'Synthetic customer',phone:'+2347000000000',phone_source:'Synthetic explicit number'});
  const receipt=await cmd(page,csrf,'/receipts',{warehouse_id:wh.id,party_id:party.id,series:'FINAL'+Date.now(),number:1,received_on:'2026-11-04',transport:'AIR',origin:'GZ',customer_phone:'+2347000000000',specs:[{length:'1',width:'1',height:'1',unit:'M',pieces:2,kg:'2'}]});
  await page.getByRole('button',{name:'退出登录',exact:true}).click();await page.getByRole('link',{name:/客户自助查询/}).click();await page.getByRole('button',{name:'验证本人手机号 / 本人箱单',exact:true}).click();
  await page.getByLabel('电话',{exact:true}).fill('+2347000000000');await page.getByRole('button',{name:'发送验证码',exact:true}).click();await expect(page.getByLabel('验证码',{exact:true})).toBeVisible();const code=JSON.parse(readFileSync(profile.private_root+'/phone-transport.json','utf8')).code;await page.getByLabel('验证码',{exact:true}).fill(code);await page.getByRole('button',{name:'验证号码归属',exact:true}).click();await expect(page.getByRole('button',{name:'退出客户账号',exact:true})).toBeVisible();
  await page.getByRole('checkbox',{name:'选择 '+receipt.reference,exact:true}).check();return {csrf,receipt};
}
async function exportCustomer(page:Page,format:string){await page.getByRole('combobox').selectOption(format);const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/customer/exports'&&r.request().method()==='POST');await page.getByRole('button',{name:'导出本人箱单',exact:true}).click();const r=await response;expect(r.ok()).toBeTruthy();const job=await r.json();worker();return job}

test('FR-I08 customer PNG is discarded when switching to XLSX or failed job',async({page})=>{
  test.setTimeout(90000);await customer(page);await exportCustomer(page,'png');const copy=page.getByRole('button',{name:'复制图片',exact:true});await expect(copy).toBeEnabled({timeout:20000});
  await exportCustomer(page,'xlsx');await expect(page.getByRole('button',{name:'下载文件',exact:true})).toBeEnabled({timeout:20000});await expect(copy).toBeDisabled();
});

test('FR-I08 customer copy always fetches the current PNG job',async({page})=>{
  test.setTimeout(120000);await customer(page);const copy=page.getByRole('button',{name:'复制图片',exact:true});
  const first=await exportCustomer(page,'png');await expect(copy).toBeEnabled({timeout:20000});
  const second=await exportCustomer(page,'png');expect(second.id).not.toBe(first.id);await expect(copy).toBeEnabled({timeout:20000});
  const fetched:string[]=[];page.on('request',r=>{const path=new URL(r.url()).pathname;if(path.endsWith('/clipboard'))fetched.push(path)});
  await copy.click();
  await expect.poll(()=>fetched.length).toBeGreaterThan(0);
  expect(fetched.every(path=>path.includes('/customer/exports/'+second.id+'/clipboard'))).toBeTruthy();
});

test('FR-I09 clearing cost normal editor previews and submits stable AP with existing payment',async({page})=>{
  const csrf=await login(page),wh=await cmd(page,csrf,'/warehouses',{name:'Synthetic clearing '+Date.now()}),party=await cmd(page,csrf,'/parties',{warehouse_id:wh.id,name:'Synthetic clearing creditor'});
  const shipment=await cmd(page,csrf,'/shipments',{warehouse_id:wh.id,reference:'Final AP '+Date.now(),transport:'SEA',clearing_on:'2026-11-04',clearing_party_id:party.id,clearing_amount_ngn:'1000'});
  await cmd(page,csrf,'/payable-payments',{warehouse_id:wh.id,party_id:party.id,amount:'300',currency:'NGN',period:'2026-11',allocations:[{invoice_id:shipment.clearing_invoice_id,expected_version:1,amount_ngn:'300'}]});
  await page.locator('.sidebar').getByRole('button',{name:'出运管理',exact:true}).click();await page.getByRole('button',{name:shipment.reference,exact:true}).click();await page.getByRole('button',{name:'编辑',exact:true}).click();const form=page.locator('form').filter({has:page.getByLabel('清关费用',{exact:true})});await form.getByLabel('清关费用',{exact:true}).fill('1200');
  await expect(form.getByRole('button',{name:'核对变更预览',exact:true})).toBeVisible();await form.getByRole('button',{name:'核对变更预览',exact:true}).click();await page.getByRole('button',{name:'确认执行上述变更',exact:true}).click();
  await expect.poll(async()=>{const r=await(await page.request.get('/api/invoices/'+shipment.clearing_invoice_id)).json();return [r.amount_ngn,r.allocated_ngn,r.outstanding_ngn]}).toEqual(['1200.00','300.00','900.00']);
});
