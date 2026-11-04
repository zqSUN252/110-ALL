import {useEffect,useState} from 'react';
import {api,query,type Row} from './api';
import {DataTable,ErrorBox,RecordView,FileViewer} from './ui';
import {useT} from './i18n';

export function BankEntries({accountId}:{accountId:string}) {
  const t=useT(),[search,setSearch]=useState(''),[after,setAfter]=useState<string>(),[history,setHistory]=useState<(string|undefined)[]>([]),[data,setData]=useState<Row>(),[detail,setDetail]=useState<Row>(),[error,setError]=useState<any>();
  useEffect(()=>{let active=true;const timer=setTimeout(()=>api('/api/workspace/bank-entries?'+query({account_id:accountId,query:search,after,limit:50})).then(r=>{if(active)setData(r)}).catch(e=>{if(active)setError(e)}),180);return()=>{active=false;clearTimeout(timer)}},[accountId,search,after]);
  return <section><h3>{t('银行原始交易','Original bank transactions')}</h3><ErrorBox error={error}/>
    <label>{t('交易参考号 / 摘要','Reference / description')}<input value={search} onChange={e=>{setSearch(e.target.value);setAfter(undefined);setHistory([])}}/></label>
    <DataTable items={data?.items||[]} columns={[{key:'reference',render:r=><button onClick={()=>api('/api/workspace/bank-entry/'+r.id).then(setDetail).catch(setError)}>{r.reference||t('查看原始行','View original row')}</button>},{key:'transaction_date'},{key:'description'},{key:'credit'},{key:'debit'},{key:'currency'}]}/>
    <div className="toolbar"><button disabled={!history.length} onClick={()=>{setAfter(history.at(-1));setHistory(h=>h.slice(0,-1))}}>{t('上一页','Previous')}</button><button disabled={!data?.next} onClick={()=>{setHistory(h=>[...h,after]);setAfter(data?.next)}}>{t('下一页','Next')}</button></div>
    {detail&&<><RecordView data={detail}/>{detail.sources?.map((source:Row)=><details key={source.id}><summary>{t('查看原始文件','View original file')} · {source.sheet} · {source.row_number}</summary><FileViewer path={'/api/finance/files/'+source.attachment_id}/></details>)}</>}
  </section>
}
