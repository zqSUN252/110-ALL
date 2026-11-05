import {useState} from 'react';
import {api,type Row} from './api';
import {Form,RecordView,ErrorBox} from './ui';
import {required} from './catalog';
import {ActionForm} from './workflows';
import {useT} from './i18n';

export function HoldReview({data,onSaved}:{data:Row;onSaved:(r:Row)=>void}) {
  const t=useT(),[selected,setSelected]=useState(new Set<string>());
  const facts=[...(data.facts||data.ticket?.facts||[]).map((h:Row)=>({...h,source_version:h.current_source_version??data.source_version})),...(data.native?.facts||[]).map((h:Row)=>({...h,source_version:h.current_source_version??data.native.source_version}))];
  return <section><RecordView data={{label:data.label||data.ticket?.label}}/>{facts.map((h:Row)=><article key={h.id} className="hold-fact"><label><input type="checkbox" disabled={h.state!=='active'} checked={selected.has(h.id)} onChange={e=>setSelected(old=>{const next=new Set(old);if(e.target.checked)next.add(h.id);else next.delete(h.id);return next})}/>{t('选择控货事实','Select hold fact')} · {h.raw_value||h.source||h.id}</label><RecordView data={h}/>
    {h.state==='removed'&&<Form title={t('撤销此次解控','Cancel this removal')} fields={[required('reason')]} initial={{expected_version:h.version}} submit={(b,k)=>api('/api/holds/'+h.id+'/cancel',b,'POST',k)} onSaved={onSaved}/>}
  </article>)}
    {selected.size>0&&<ActionForm action={{key:'remove-holds',title:['有依据解除选中控货','Remove selected holds with evidence'],cap:'release.hold',fields:[required('reason'),required('removed_on','date'),required('confirmed_phone'),required('ngn_per_usd')],preview:async b=>{
      const members=await Promise.all(facts.filter(h=>selected.has(h.id)).map(async h=>({hold_id:h.id,expected_version:h.version,source_version:h.source_version,...(h.ticket_id?{finance_preview:(await api('/api/tickets/'+h.ticket_id+'/change-preview',{contact_raw:b.confirmed_phone,phone_normalized:b.confirmed_phone,phone_confirmed:true,ngn_per_usd:b.ngn_per_usd})).finance_preview}:{})})));
      return {...b,members};
    },run:(b,k)=>api('/api/holds/remove',b.preview,'POST',k)}} onSaved={onSaved}/>}
  </section>;
}
