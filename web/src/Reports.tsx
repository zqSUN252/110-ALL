import {useEffect,useState} from 'react';
import {api,query,download,type Row} from './api';
import {Form,RecordView,ErrorBox,DataTable,f} from './ui';
import {lookup,required,select} from './catalog';
import {useT} from './i18n';

export function Reports({kind,openRecord}:{kind:string;openRecord:(kind:string,id:string)=>void}) {
  const t=useT(),[result,setResult]=useState<Row>(),[job,setJob]=useState<Row>(),[error,setError]=useState<any>();
  useEffect(()=>{if(!job?.id||['complete','failed'].includes(job.status))return;let live=true;const timer=setInterval(()=>api('/api/cargo/export-jobs/'+job.id).then(r=>{if(live)setJob(r)}).catch(setError),1500);return()=>{live=false;clearInterval(timer)}},[job?.id,job?.status]);
  return <section><h1>{kind==='warehouse-report'?t('入库分析与库存导出','Inbound analysis & stock export'):kind==='bank-archive'?t('按原模板归档银行核验','Archive bank reviews in original template'):t('期初余额','Opening balances')}</h1><ErrorBox error={error}/>
    {kind==='warehouse-report'?<>
      <Form title={t('入库台账分析','Inbound ledger analysis')} fields={[lookup('warehouse_id','warehouses'),f('on','date'),f('salesperson')]} submitLabel={['查询入库','Read inbound ledger']} submit={async b=>{const r=await api('/api/f6/analysis?'+query(b));setResult(r);return r}}/>
      {result&&<><RecordView data={{on:result.on,totals:result.totals,destinations:result.destinations,salespeople:result.salespeople}}/><DataTable items={result.rows||[]} columns={[{key:'reference',render:r=><button onClick={()=>openRecord('receipts',r.receipt_id)}>{r.reference}</button>},{key:'destination'},{key:'salesperson'},{key:'pieces'},{key:'kg'},{key:'cbm'}]}/></>}
      <Form title={t('检查收据断号','Find receipt numbering gaps')} fields={[lookup('warehouse_id','warehouses',true),required('series')]} submitLabel={['查询断号','Find gaps']} submit={async b=>{const r=await api('/api/receipts/numbering-gaps?'+query(b));setResult(r);return r}}/>
      <Form title={t('完整入库导出','Export all receipts')} fields={[lookup('warehouse_id','warehouses',true)]} submitLabel={['排队生成Excel','Queue Excel export']} submit={async(b,k)=>{const r=await api('/api/cargo/exports',{...b,mode:'all'},'POST',k);setJob({id:r.job_id||r.id,status:'pending'});return r}}/>
      <Form title={t('按日入库分析导出','Export daily inbound analysis')} fields={[lookup('warehouse_id','warehouses',true),required('on','date'),f('salesperson')]} submit={async(b,k)=>{const r=await api('/api/f6/analysis/export',b,'POST',k);await download('/api/cargo/exports/'+r.attachment_id,'MCSTAR-inbound.xlsx');return r}}/>
      {job&&<><RecordView data={job}/>{job.payload?.attachment_id&&<button onClick={()=>download('/api/cargo/exports/'+job.payload.attachment_id,'MCSTAR-receipts.xlsx').catch(setError)}>{t('下载Excel','Download Excel')}</button>}</>}
    </>:kind==='bank-archive'?<Form fields={[lookup('warehouse_id','warehouses',true),f('template_attachment_id','file',{required:true}),f('claims','rows',{fields:[lookup('id','claims',true)]}),f('label')]} submitLabel={['生成原模板归档','Generate original-template archive']} submit={async(b,k)=>{const claims=await Promise.all((b.claims||[]).map(async(c:Row)=>({id:c.id,expected_version:(await api('/api/workspace/bank-claim/'+c.id)).version})));const r=await api('/api/bank/archives',{...b,claims},'POST',k);setResult(r);if(r.attachment_id)await download('/api/finance/files/'+r.attachment_id,'MCSTAR-bank-archive.xlsx');return r}}/>:
      <Form fields={[lookup('party_id','parties',true),lookup('warehouse_id','warehouses',true),{...select('kind',['credit','debt'],true),optionLabels:{credit:['期初可用余额','Opening credit'],debt:['期初欠款','Opening debt']}},required('amount_ngn'),required('as_of','date'),required('source')]} submitLabel={['确认期初余额','Confirm opening balance']} submit={async(b,k)=>{const r=await api('/api/opening-balances',b,'POST',k);setResult(r);return r}}/>}
    {result&&<RecordView data={result}/>}
  </section>;
}
