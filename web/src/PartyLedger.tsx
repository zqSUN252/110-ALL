import {useEffect,useState} from 'react';
import {api,rows,query,type Row} from './api';
import {DataTable,ErrorBox,Pager,RecordView} from './ui';
import {useT} from './i18n';

export function PartyLedger({partyId,user,openRecord,ticketsOnly=false}:{partyId:string;user:Row;openRecord:(kind:string,id:string)=>void;ticketsOnly?:boolean}) {
  const t=useT(),[kind,setKind]=useState('AR'),[data,setData]=useState<Row>(),[tickets,setTickets]=useState<Row[]>([]),[offset,setOffset]=useState(0),[error,setError]=useState<any>();
  const allowed=(cap:string)=>user.capabilities.includes('*')||user.capabilities.includes(cap);
  useEffect(()=>{let active=true;setError(null);
    if(!ticketsOnly&&allowed(kind==='AR'?'finance.read':'payables.read'))api('/api/workspace/finance?'+query({party_id:partyId,kind})).then(r=>{if(active)setData(r)}).catch(e=>{if(active)setError(e)});
    if(allowed('packing.read'))api('/api/tickets?'+query({party_id:partyId,offset,limit:50})).then(r=>{if(active)setTickets(rows(r))}).catch(e=>{if(active)setError(e)});
    return()=>{active=false};
  },[partyId,kind,offset,ticketsOnly]);
  return <section><ErrorBox error={error}/>
    {!ticketsOnly&&<><h3>{t('客户账款','Customer balances')}</h3><div className="toolbar">
      {allowed('finance.read')&&<button onClick={()=>setKind('AR')}>{t('应收与未付款','Receivables & unpaid')}</button>}
      {allowed('payables.read')&&<button onClick={()=>setKind('AP')}>{t('应付','Payables')}</button>}</div>
      {data&&<><RecordView data={data.totals}/><DataTable items={data.items} columns={[{key:'reference',render:r=><button onClick={()=>openRecord(kind==='AR'?'invoices':'payables',r.id)}>{r.reference}</button>},{key:'amount_ngn'},{key:'allocated_ngn'},{key:'historical_received_ngn'},{key:'outstanding_ngn'},{key:'due_on'}]}/>{data.next&&<button onClick={()=>api('/api/workspace/finance?'+query({party_id:partyId,kind,after:data.next})).then(setData).catch(setError)}>{t('后50张账单','Next 50 invoices')}</button>}</>}
    </>}
    {allowed('packing.read')&&<><h3>{t('名下运单','Customer shipments')}</h3><DataTable items={tickets} columns={[{key:'receipt',render:r=><button onClick={()=>openRecord('tickets',r.id)}>{r.receipt}</button>},{key:'mark'},{key:'transport'},{key:'destination'},{key:'pieces'},{key:'kg'},{key:'cbm'},{key:'state'}]}/><Pager offset={offset} count={tickets.length} onChange={setOffset}/></>}
  </section>
}
