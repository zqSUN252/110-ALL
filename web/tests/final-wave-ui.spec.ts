import {test,expect,type Page} from '@playwright/test';
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
test.beforeEach(async({page})=>{await page.setViewportSize({width:1440,height:1000})});
const profilePath=readFileSync('../private/ui-validation/latest-profile.txt','utf8').trim();
async function capture(page:Page,name:string,target?:any){
  if(!process.env.MCSTAR_FIX_SHOTS)return;
  const directory=resolve(process.env.MCSTAR_FIX_SHOTS);mkdirSync(directory,{recursive:true});
  const original=page.viewportSize()!;const scroll=await page.evaluate(()=>({x:scrollX,y:scrollY}));
  for(const width of [1440,390]){const height=width===1440?1000:844;await page.setViewportSize({width,height});if(target)await target.evaluate((el:HTMLElement)=>scrollTo(0,el.getBoundingClientRect().top+scrollY-90));await page.screenshot({path:resolve(directory,name+'-'+width+'.png'),animations:'disabled'});writeFileSync(resolve(directory,name+'-'+width+'.json'),JSON.stringify({name,width,height,url:page.url(),scope:'Final wave synthetic UI; no real customer data'},null,2))}
  await page.setViewportSize(original);await page.evaluate(({x,y})=>scrollTo(x,y),scroll);
}
function clearVoidMembers(documentId:string){
  execFileSync('../.venv/Scripts/python.exe',['-c',`import sys
from sqlalchemy import create_engine,text
from app.settings import load_settings
settings=load_settings();engine=create_engine(settings.database_url)
with engine.begin() as connection:connection.execute(text('update packing_documents set void_members=NULL where id=:i'),{'i':sys.argv[1]})`,documentId],{env:{...process.env,MCSTAR_CONFIG:profilePath,PYTHONPATH:'../backend'},stdio:'pipe'});
}
async function login(page:Page,name='admin'){
  await page.goto('/');await page.getByRole('button',{name:'进入系统',exact:true}).click();
  await page.getByLabel('用户名',{exact:true}).fill(name);await page.getByLabel('密码',{exact:true}).fill('synthetic-password-123');
  await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.getByText(name,{exact:true}).first()).toBeVisible();
  return (await(await page.request.get('/api/auth/session')).json()).csrf_token;
}
async function cmd(page:Page,csrf:string,path:string,body:any,method='POST'){
  const r=await page.request.fetch('/api'+path,{method,data:body,headers:{'X-CSRF-Token':csrf,'Idempotency-Key':crypto.randomUUID()}});
  expect(r.ok(),method+' '+path+' '+r.status()+' '+(await r.text())).toBeTruthy();return r.json();
}
const area=(page:Page)=>page.locator('.workspace-content > div:visible');
async function nav(page:Page,name:string){await page.locator('.sidebar').getByRole('button',{name,exact:true}).click()}
async function openNamed(page:Page,name:string){
  const a=area(page);await a.locator('tbody tr').first().waitFor();
  for(let n=0;n<20&&!(await a.getByRole('button',{name,exact:true}).count());n++){
    const next=a.getByRole('button',{name:'下一页',exact:true});if(!(await next.isEnabled()))break;await next.click();await page.waitForTimeout(180);
  }
  await a.getByRole('button',{name,exact:true}).click();
}
function baseTicket(ref:string,wh:any,party:any,doc:any,mark:string,clearing:string){
  return {document_id:doc.id,party_id:party.id,receipt:ref,transport:'AIR',origin:'GZ',pieces:1,kg:'1',cbm:'1',
    contact_raw:'+2347000000000',phone_normalized:'+2347000000000',phone_confirmed:true,mark,item_en:'Synthetic cargo',
    destination:'LAGOS',destination_warehouse_id:wh.id,address:'Synthetic destination',freight_usd:'0',clearing_ngn:clearing,ngn_per_usd:'1500',
    charge_components:[{component:'freight',currency:'USD',unit:'KG',amount:'0',source:'Synthetic'},{component:'clearing',currency:'NGN',unit:'CBM',amount:clearing,source:'Synthetic'}]};
}
async function sea(page:Page,csrf:string){
  const ref='FINAL-UI-'+Date.now();
  const wh=await cmd(page,csrf,'/warehouses',{name:ref});
  const party=await cmd(page,csrf,'/parties',{warehouse_id:wh.id,name:ref,phone:'+2347000000000',phone_source:'Synthetic explicit number'});
  const shipment=await cmd(page,csrf,'/shipments',{warehouse_id:wh.id,reference:ref,transport:'AIR',origin:'GZ'});
  const doc=await cmd(page,csrf,'/packing/documents',{shipment_id:shipment.id,reference:ref,uploaded_on:'2026-11-01'});
  const ticket=await cmd(page,csrf,'/tickets',baseTicket(ref+'-A',wh,party,doc,ref,'1000'));
  return {ref,wh,party,shipment,doc,ticket};
}

test('FR-M01 copy history shows the authorized operator and time',async({page})=>{
  test.setTimeout(90000);await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  const csrf=await login(page),data=await sea(page,csrf);
  await cmd(page,csrf,'/tracking/nodes',{target_type:'shipment',target_id:data.shipment.id,expected_version:data.shipment.version,node:'loading',mode:'manual',occurred_at:'2026-11-01T08:00:00+08:00',source:'Synthetic actual loading'});
  await nav(page,'PACKING LIST');await area(page).getByLabel('收据号',{exact:true}).fill(data.ticket.receipt);
  await area(page).getByRole('button',{name:'装柜通知',exact:true}).click();
  const copy=area(page).getByRole('button',{name:'复制图片',exact:true});await expect(copy).toBeEnabled();await copy.click();
  await expect(area(page).getByText('已复制并保存标记；尚未投递。',{exact:true})).toBeVisible();
  await area(page).locator('.notice-panel').getByRole('button',{name:'关闭',exact:true}).click();
  await openNamed(page,data.ticket.receipt);
  await area(page).getByRole('button',{name:'复制记录',exact:true}).click();
  const mark=area(page).locator('p').filter({hasText:'已通知装柜'}).first();
  await expect(mark).toContainText('admin');await expect(mark).toContainText('20');
  await capture(page,'final-m01-copy-history',mark);
});

test('FR-I05 billing group UI previews and confirms two members into one invoice',async({page})=>{
  test.setTimeout(120000);const csrf=await login(page);
  const ref='FINAL-GROUP-'+Date.now();
  const wh=await cmd(page,csrf,'/warehouses',{name:ref});
  const party=await cmd(page,csrf,'/parties',{warehouse_id:wh.id,name:ref,phone:'+2347000000000',phone_source:'Synthetic explicit number'});
  const shipment=await cmd(page,csrf,'/shipments',{warehouse_id:wh.id,reference:ref,transport:'AIR',origin:'GZ'});
  const doc=await cmd(page,csrf,'/packing/documents',{shipment_id:shipment.id,reference:ref,uploaded_on:'2026-11-01'});
  const a=await cmd(page,csrf,'/tickets',baseTicket(ref+'-A',wh,party,doc,'GROUP-A','1000'));
  const b=await cmd(page,csrf,'/tickets',baseTicket(ref+'-B',wh,party,doc,'GROUP-B','500'));
  for(const t of [a,b])await cmd(page,csrf,'/tickets/'+t.id+'/arrive',{expected_version:t.version,pieces:1,kg:'1',cbm:'1',occurred_on:'2026-11-02',source:'Synthetic actual arrival',mode:'air'});
  await nav(page,'箱单文档');await openNamed(page,ref);
  await area(page).getByRole('button',{name:'客户应收分组',exact:true}).click();
  const second=area(page).locator('select[aria-label="分组 '+ref+'-B"]');await expect(second).toBeVisible();
  const targetKey=await area(page).locator('tbody tr').filter({hasText:ref+'-A'}).locator('select').inputValue();
  await second.selectOption(targetKey);
  await area(page).getByLabel('变更原因',{exact:true}).fill('Synthetic explicit same customer billing group');
  await area(page).getByRole('button',{name:'核对分组与资金预览',exact:true}).click();
  await expect(area(page).getByRole('button',{name:'确认分组与来源分配',exact:true})).toBeVisible();
  await capture(page,'final-i05-billing-group-preview');
  await area(page).getByRole('button',{name:'确认分组与来源分配',exact:true}).click();
  await expect.poll(async()=>{const x=await(await page.request.get('/api/tickets/'+a.id)).json();const y=await(await page.request.get('/api/tickets/'+b.id)).json();return [x.invoice_id,y.invoice_id]}).toEqual([expect.any(String),expect.any(String)]);
  const first=await(await page.request.get('/api/tickets/'+a.id)).json();const secondTicket=await(await page.request.get('/api/tickets/'+b.id)).json();
  expect(first.invoice_id).toBe(secondTicket.invoice_id);
  const invoice=await(await page.request.get('/api/invoices/'+first.invoice_id)).json();
  expect(invoice.amount_ngn).toBe('1500.00');
});

test('FR-I02 recovery normal UI restores recorded members through preview',async({page})=>{
  test.setTimeout(120000);const csrf=await login(page),data=await sea(page,csrf);
  await cmd(page,csrf,'/tickets/'+data.ticket.id+'/arrive',{expected_version:data.ticket.version,pieces:1,kg:'1',cbm:'1',occurred_on:'2026-11-02',source:'Synthetic actual arrival',mode:'air'});
  let doc=await(await page.request.get('/api/packing/documents/'+data.doc.id)).json();
  const preview=await(await page.request.get('/api/packing/documents/'+data.doc.id+'/void-preview')).json();
  await cmd(page,csrf,'/packing/documents/'+data.doc.id+'/void',{expected_version:doc.version,preview,reason:'Synthetic document cancellation'});
  await nav(page,'箱单文档');await openNamed(page,data.ref);
  await area(page).getByRole('button',{name:'恢复整份箱单',exact:true}).click();
  await area(page).getByLabel('恢复原因',{exact:true}).fill('Synthetic explicit recovery');
  await area(page).getByRole('button',{name:'核对恢复与财务预览',exact:true}).click();
  await capture(page,'final-i02-recovery-preview');
  await area(page).getByRole('button',{name:'确认恢复所列成员',exact:true}).click();
  await expect.poll(async()=>{const t=await(await page.request.get('/api/tickets/'+data.ticket.id)).json();return t.state}).toBe('active');
  const current=await(await page.request.get('/api/tickets/'+data.ticket.id)).json();
  const invoice=await(await page.request.get('/api/invoices/'+current.invoice_id)).json();
  expect(invoice.state).toBe('open');
});

test('FR-I02 unknown old cancellation requires explicit current member selection',async({page})=>{
  test.setTimeout(120000);const csrf=await login(page);const ref='FINAL-UNKNOWN-'+Date.now();
  const wh=await cmd(page,csrf,'/warehouses',{name:ref});
  const party=await cmd(page,csrf,'/parties',{warehouse_id:wh.id,name:ref});
  const shipment=await cmd(page,csrf,'/shipments',{warehouse_id:wh.id,reference:ref,transport:'AIR',origin:'GZ'});
  const doc=await cmd(page,csrf,'/packing/documents',{shipment_id:shipment.id,reference:ref,uploaded_on:'2026-11-01'});
  const a=await cmd(page,csrf,'/tickets',baseTicket(ref+'-A',wh,party,doc,'OLD-A','1000'));
  const b=await cmd(page,csrf,'/tickets',baseTicket(ref+'-B',wh,party,doc,'OLD-B','500'));
  const current=await(await page.request.get('/api/packing/documents/'+doc.id)).json();
  const preview=await(await page.request.get('/api/packing/documents/'+doc.id+'/void-preview')).json();
  await cmd(page,csrf,'/packing/documents/'+doc.id+'/void',{expected_version:current.version,preview,reason:'Synthetic old cancellation'});
  clearVoidMembers(doc.id);
  await nav(page,'箱单文档');await openNamed(page,ref);
  await area(page).getByRole('button',{name:'恢复整份箱单',exact:true}).click();
  await expect(area(page).getByText('历史整单作废的成员范围未知',{exact:false})).toBeVisible({timeout:20000});
  await area(page).getByRole('checkbox',{name:ref+'-A',exact:true}).check();
  await capture(page,'final-i02-unknown-members');
  await area(page).getByLabel('恢复原因',{exact:true}).fill('Synthetic explicit current member decision');
  await area(page).getByRole('button',{name:'核对恢复与财务预览',exact:true}).click();
  await capture(page,'final-i02-unknown-preview');
  await area(page).getByRole('button',{name:'确认恢复所列成员',exact:true}).click();
  await expect.poll(async()=>{const x=await(await page.request.get('/api/tickets/'+a.id)).json();const y=await(await page.request.get('/api/tickets/'+b.id)).json();return [x.state,y.state].sort().join(',')}).toBe('active,void');
});

test('FR-I03 manufacturer hold UI removes and cancels with source evidence',async({page})=>{
  test.setTimeout(120000);const csrf=await login(page);const ref='FINAL-HOLD-'+Date.now();
  const wh=await cmd(page,csrf,'/warehouses',{name:ref});
  const party=await cmd(page,csrf,'/parties',{warehouse_id:wh.id,name:ref});
  const shipment=await cmd(page,csrf,'/shipments',{warehouse_id:wh.id,reference:ref,transport:'AIR',origin:'GZ'});
  const doc=await cmd(page,csrf,'/packing/documents',{shipment_id:shipment.id,reference:ref,uploaded_on:'2026-11-01'});
  const payload:any=baseTicket(ref+'-H',wh,party,doc,'HOLD','1000');payload.contact_raw='ON HOLD BY SUPPLIER';payload.phone_normalized=null;payload.phone_confirmed=false;
  const ticket=await cmd(page,csrf,'/tickets',payload);
  await nav(page,'PACKING LIST');await area(page).getByLabel('收据号',{exact:true}).fill(ticket.receipt);await openNamed(page,ticket.receipt);
  await area(page).getByRole('button',{name:'厂家控货',exact:true}).click();
  const box=area(page).getByRole('checkbox',{name:/选择控货事实/}).first();await expect(box).toBeVisible();await capture(page,'final-i03-manufacturer-hold',box);await box.check();
  await area(page).getByLabel('原因',{exact:true}).fill('Synthetic supplier release evidence');
  await area(page).getByLabel('解控日期',{exact:true}).fill('2026-11-04');
  await area(page).getByLabel('确认客户号码',{exact:true}).fill('+2347000000000');
  await area(page).getByLabel('NGN / USD结算汇率',{exact:true}).fill('1500');
  await area(page).getByRole('button',{name:'核对变更预览',exact:true}).click();
  await area(page).getByRole('button',{name:'确认执行上述变更',exact:true}).click();
  const cancelForm=area(page).locator('article form').first();
  await expect(cancelForm).toBeVisible({timeout:20000});
  await cancelForm.getByLabel('原因',{exact:true}).fill('Synthetic supervisor withdraws release');
  await cancelForm.getByRole('button',{name:'保存',exact:true}).click();
  await expect(area(page).getByText('REMOVED ON CREDIT',{exact:true}).first()).toBeVisible({timeout:20000});
  const holds=await(await page.request.get('/api/tickets/'+ticket.id+'/holds')).json();
  const facts=[...(holds.ticket?.facts||[]),...(holds.native?.facts||[])];
  expect(facts.some((h:any)=>h.state==='active')).toBeTruthy();
});

test('FR-I04 native arrival UI selects an actual outbound batch',async({page})=>{
  test.setTimeout(120000);const csrf=await login(page);const ref='FINAL-NATIVE-'+Date.now();
  const wh=await cmd(page,csrf,'/warehouses',{name:ref});
  const party=await cmd(page,csrf,'/parties',{warehouse_id:wh.id,name:ref});
  const receipt=await cmd(page,csrf,'/receipts',{warehouse_id:wh.id,series:ref,number:1,received_on:'2026-11-01',transport:'AIR',origin:'GZ',specs:[{length:'1',width:'1',height:'1',unit:'M',pieces:10,kg:'10'}]});
  const shipment=await cmd(page,csrf,'/shipments',{warehouse_id:wh.id,reference:ref,transport:'AIR',origin:'GZ'});
  const doc=await cmd(page,csrf,'/packing/documents',{shipment_id:shipment.id,reference:ref,uploaded_on:'2026-11-01'});
  const payload:any=baseTicket(receipt.reference,wh,party,doc,receipt.reference,'0');payload.native_receipt_id=receipt.id;payload.pieces=4;payload.kg='4';payload.cbm='4';
  const ticket=await cmd(page,csrf,'/tickets',payload);
  await cmd(page,csrf,'/cargo/dispatch',{shipment_id:shipment.id,occurred_on:'2026-11-02',rows:[{receipt_id:receipt.id,expected_version:receipt.version,pieces:10,kg:'10',cbm:'10'}]});
  await nav(page,'PACKING LIST');await area(page).getByLabel('收据号',{exact:true}).fill(ticket.receipt);await openNamed(page,ticket.receipt);
  await area(page).getByRole('button',{name:'空运 / EXPRESS分批到货',exact:true}).click();
  const form=area(page).locator('.detail-pane form');
  await expect(form.getByRole('button',{name:'增加明细',exact:true})).toBeVisible({timeout:20000});
  await form.getByLabel('件数',{exact:true}).first().fill('4');
  await form.getByLabel('KG',{exact:true}).first().fill('4');
  await form.getByLabel('CBM',{exact:true}).first().fill('4');
  await form.getByLabel('实际日期',{exact:true}).fill('2026-11-03');
  await form.getByLabel('依据 / 来源',{exact:true}).fill('Synthetic actual destination arrival');
  await form.getByRole('button',{name:'增加明细',exact:true}).click();
  const row=form.locator('.repeatable fieldset').last();
  await row.getByLabel('出库批次及剩余数量',{exact:true}).selectOption({index:1});
  await row.getByLabel('件数',{exact:true}).fill('4');
  await row.getByLabel('KG',{exact:true}).fill('4');
  await row.getByLabel('CBM',{exact:true}).fill('4');
  await capture(page,'final-i04-native-arrival',form);
  await form.getByRole('button',{name:'保存',exact:true}).click();
  await expect.poll(async()=>{const t=await(await page.request.get('/api/tickets/'+ticket.id)).json();return t.arrivals.length}).toBe(1);
  const options=await(await page.request.get('/api/tickets/'+ticket.id+'/native-dispatches')).json();
  expect(options[0].remaining).toEqual({pieces:'6',kg:'6',cbm:'6'});
});
