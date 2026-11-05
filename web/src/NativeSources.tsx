import {useEffect,useRef,useState} from 'react';
import {api,type Row} from './api';
import {ErrorBox,Form,RecordView,f} from './ui';
import {required,select} from './catalog';
import {useT} from './i18n';
import type {Action} from './workflows';

export function NativeArrivalForm({action,onSaved}:{action:Action;onSaved:(r:Row)=>void}){
  const t=useT(),[options,setOptions]=useState<Row[]>([]),[error,setError]=useState<any>(),[loaded,setLoaded]=useState(false);
  const ticket=action.record!;const load=async()=>{setLoaded(false);try{setOptions(await api('/api/tickets/'+ticket.id+'/native-dispatches'));setError(null);setLoaded(true)}catch(e){setError(e)}};
  useEffect(()=>{void load()},[ticket.id,ticket.version]);
  const names=Object.fromEntries(options.map(r=>[r.id,[`${r.occurred_on} · ${r.batch_id} · ${r.remaining.pieces} PCS / ${r.remaining.kg} KG / ${r.remaining.cbm} CBM`,`${r.occurred_on} · ${r.batch_id} · ${r.remaining.pieces} PCS / ${r.remaining.kg} KG / ${r.remaining.cbm} CBM`]]));
  const allocation=f('dispatch_allocations','rows',{title:['实际出库来源分配','Actual outbound source allocations'],fields:[{...select('stock_event_id',options.map(r=>r.id),true),title:['出库批次及剩余数量','Outbound batch and remaining quantities'],optionLabels:names},required('pieces','number'),required('kg'),required('cbm')]});
  return <section><ErrorBox error={error}/><button onClick={()=>void load()}>{t('刷新实际出库来源','Refresh actual outbound sources')}</button>{loaded&&<><p>{t('逐项选择本收据、本出运的实际出库批次；各来源数量合计须与本次到货一致。','Select actual outbound batches of this receipt and shipment. The selected source quantities must equal this arrival.')}</p>{!options.length&&<p role="status">{t('当前没有可用的原生出库来源。请先核对实际出库记录。','No native outbound source is available. Check actual dispatch records first.')}</p>}<Form title={t(...action.title)} fields={[...action.fields,allocation]} initial={action.initial} submit={async(b,k)=>{
    const dispatch_allocations=(b.dispatch_allocations||[]).map((r:Row)=>({...r,expected_version:options.find(x=>x.id===r.stock_event_id)?.version}));
    const payload:Row={...b,dispatch_allocations};if(action.key==='arrival-sources')payload.arrival_version=ticket.arrivals.find((r:Row)=>r.id===b.arrival_id)?.version;
    const result=await action.run(payload,k);onSaved(result);await load();return result;
  }}/></>}</section>
}

export function RecoverDocument({action,onSaved}:{action:Action;onSaved:(r:Row)=>void}){
  const t=useT(),[source,setSource]=useState<Row>(),[selected,setSelected]=useState<string[]>([]),[reason,setReason]=useState(''),[preview,setPreview]=useState<Row>(),[error,setError]=useState<any>(),[busy,setBusy]=useState(false);const key=useRef(crypto.randomUUID());
  const record=action.record!,path='/api/packing/documents/'+record.id;
  const load=async()=>{try{setSource(await api(path+'/recover-preview'));setPreview(undefined);setError(null)}catch(e){setError(e)}};
  useEffect(()=>{void load()},[record.id,record.version]);
  return <section className="workflow"><h3>{t(...action.title)}</h3><ErrorBox error={error}/><button onClick={()=>void load()}>{t('重新读取恢复依据','Reload recovery evidence')}</button>{source&&<>
    {source.original_members_unknown&&<><p>{t('历史整单作废的成员范围未知。选择现在明确恢复的成员；这次决定保留原未知状态和旧文件。','The historical cancellation membership is unknown. Explicitly select the members to restore now; the unknown historical state and original files remain preserved.')}</p>{source.candidates.map((r:Row)=><label key={r.ticket_id}><input type="checkbox" checked={selected.includes(r.ticket_id)} onChange={e=>{setPreview(undefined);setSelected(e.target.checked?[...selected,r.ticket_id]:selected.filter(i=>i!==r.ticket_id))}}/>{r.receipt||r.ticket_id}</label>)}</>}
    <label>{t('恢复原因','Recovery reason')}<input value={reason} onChange={e=>{setPreview(undefined);setReason(e.target.value)}}/></label><button disabled={busy||!reason.trim()||(source.requires_member_selection&&!selected.length)} onClick={async()=>{setBusy(true);try{const pv=source.requires_member_selection?await api(path+'/recover-preview',{selected_members:selected}):await api(path+'/recover-preview');setPreview(pv);key.current=crypto.randomUUID();setError(null)}catch(e){setError(e)}finally{setBusy(false)}}}>{t('核对恢复与财务预览','Review recovery and financial effects')}</button>
    {preview&&<section className="confirmation"><RecordView data={preview}/><button className="primary" disabled={busy} onClick={async()=>{setBusy(true);try{const result=await api(path+'/recover',{expected_version:preview.version,preview,reason,...(source.requires_member_selection?{selected_members:selected}:{})},'POST',key.current);setPreview(undefined);onSaved(result)}catch(e){setError(e)}finally{setBusy(false)}}}>{t('确认恢复所列成员','Confirm recovery of listed members')}</button></section>}
  </>}</section>
}
