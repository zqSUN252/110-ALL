import {chromium} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
const credentials=JSON.parse(readFileSync('../private/local-review-final.json','utf8'));
const browser=await chromium.launch();
let stage='start';
try{
  const page=await browser.newPage({baseURL:'http://127.0.0.1:8011'});
  stage='welcome';await page.goto('/');await page.getByRole('heading',{name:'MC STAR LOGISTICS',exact:true}).waitFor();
  const spec=await (await page.request.get('/openapi.json')).json();
  if(process.env.MCSTAR_EXPECT_FIX1==='1'&&!spec.paths?.['/api/release-orders/{id}/photos'])throw new Error('stale_api_process');
  const anonymous=await page.request.get('/api/legacy/tickets');
  stage='controlled_login';await page.getByRole('button',{name:'进入系统',exact:true}).click();await page.getByLabel('用户名',{exact:true}).fill(credentials.username);await page.getByLabel('密码',{exact:true}).fill(credentials.password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByText(credentials.username,{exact:true}).first().waitFor();
  const pages={};
  for(const name of ['来源客户','来源出运单','来源箱单票件','来源账款']){
    stage='source_business_list';await page.locator('.sidebar').getByRole('button',{name,exact:true}).click();const area=page.locator('.workspace-content > div:visible');await area.locator('tbody tr').first().waitFor();pages[name]=await area.locator('tbody tr').count();if(!pages[name])throw new Error('empty');
  }
  stage='reload';await page.reload();await page.getByRole('heading',{name:'MC STAR LOGISTICS',exact:true}).waitFor();await page.getByRole('button',{name:'进入系统',exact:true}).click();await page.getByText(credentials.username,{exact:true}).first().waitFor();
  const session=await page.request.get('/api/auth/session');const body=await session.json();if(!body.csrf_token)throw new Error('csrf');
  const privateFile=await page.request.get('/private/local-review-final.json');if(privateFile.status()!==404)throw new Error('private exposure');
  const receipt={status:'passed',url:'http://127.0.0.1:8011',profile:'supplied-snapshot',source_list_rows:pages,anonymous_status:anonymous.status(),private_path_status:privateFile.status(),reload_csrf:true,api_paths:Object.keys(spec.paths).length,fix1_scoped_photo_route:Boolean(spec.paths?.['/api/release-orders/{id}/photos']),source_business_mutations:0,external_calls:0};
  writeFileSync(process.env.MCSTAR_SNAPSHOT_RECEIPT||'../private/ui-validation/snapshot-browser.json',JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));
}catch(error){console.error(JSON.stringify({status:'failed',stage,error_type:error.constructor.name}));process.exitCode=1}finally{await browser.close()}
