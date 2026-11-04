import {chromium} from '@playwright/test';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
const round=process.argv[2]||'round1';
if(!['round1','confirmation'].includes(round))throw new Error('Only the agreed visual rounds are supported');
const root='../private/ui-validation/visual-'+round;mkdirSync(root,{recursive:true});
const ids=JSON.parse(readFileSync('../private/ui-validation/http-acceptance.json','utf8')).synthetic_ids;
const browser=await chromium.launch();const measurements=[];
for(const width of [1440,390]){
  const page=await browser.newPage({viewport:{width,height:1000},baseURL:process.env.MCSTAR_TEST_URL||'http://127.0.0.1:8014'});
  const capture=async(name)=>{
    await page.waitForTimeout(350);await page.getByText('正在读取…',{exact:true}).waitFor({state:'hidden'});
    await page.waitForLoadState('networkidle');await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>Promise.all([...document.images].map(image=>image.decode().catch(()=>{}))));await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:`${root}/${width}-${name}.png`,fullPage:true,animations:'disabled'});
    measurements.push({width,surface:name,...await page.evaluate(()=>({documentWidth:document.documentElement.scrollWidth,viewport:innerWidth,tableWidths:[...document.querySelectorAll('.workspace-content > div:not([hidden]) .table-wrap')].map(e=>({client:e.clientWidth,scroll:e.scrollWidth})),focused:document.activeElement?.tagName}))});
  };
  await page.goto('/');await page.getByRole('heading',{name:'MC STAR LOGISTICS',exact:true}).waitFor();await capture('welcome');
  await page.getByRole('button',{name:'进入系统',exact:true}).click();await page.getByLabel('用户名',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('synthetic-password-123');await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByText('admin',{exact:true}).first().waitFor();
  const visible=()=>page.locator('.workspace-content > div:visible');
  const nav=async(name)=>{if(width<700)await page.getByRole('button',{name:'打开导航',exact:true}).click();await page.locator('.sidebar').getByRole('button',{name,exact:true}).click();await visible().getByRole('heading',{level:1}).waitFor();await page.waitForTimeout(500)};
  await nav('入库管理');await visible().getByRole('button',{name:'新增入库',exact:true}).click();await capture('receipts');
  await nav('PACKING LIST');const ticket=await(await page.request.get('/api/tickets/'+ids.ticket)).json();await visible().getByLabel('收据号',{exact:true}).fill(ticket.receipt);await visible().getByRole('button',{name:ticket.receipt,exact:true}).click();await visible().getByRole('button',{name:'主管复核',exact:true}).click();await capture('tickets');
  if(width===390){await visible().locator('.table-wrap').evaluate(e=>e.scrollLeft=e.scrollWidth);await capture('tickets-horizontal');}
  await nav('应收账款');await capture('receivables');
  await nav('水单审核');await visible().getByRole('button',{name:'添加水单',exact:true}).click();await capture('evidence');
  await nav('110对话助手');await capture('assistant');
  await page.close();
}
await browser.close();writeFileSync(root+'/measurements.json',JSON.stringify(measurements,null,2));console.log(JSON.stringify({round,captures:measurements.length,directory:root,document_overflows:measurements.filter(m=>m.documentWidth>m.viewport).map(m=>({width:m.width,surface:m.surface,overflow:m.documentWidth-m.viewport}))}));
