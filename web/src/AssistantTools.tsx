import {useState} from 'react';
import {api,rows,type Row} from './api';
import {Form,RecordView,ErrorBox,f,type Field} from './ui';
import {lookup,required,select,clean} from './catalog';
import {useT} from './i18n';

// Ordinary user tasks share the same strict dispatcher as dialogue. No model or
// arbitrary request editor is needed to search, compare or retrieve originals.
export function AssistantTools({thread,onRefresh}:{thread:Row;onRefresh:()=>void}) {
  const t=useT(),[kind,setKind]=useState('query'),[result,setResult]=useState<Row>(),[error,setError]=useState<any>();
  const definitions: Record<string,{title:[string,string];fields:Field[]}>={
    query:{title:['查询业务记录','Find business records'],fields:[select('kind',['cases','bank','bank_files','archives','templates','files','cargo','invoices'],true),f('query'),f('amount'),select('currency',['NGN','USD','CNY']),f('transaction_date','date'),lookup('account_id','accounts'),f('offset','number',{default:0})]},
    case:{title:['查看凭证和原图','View evidence and originals'],fields:[lookup('case_id','evidence',true)]},
    compare:{title:['比较多张凭证候选','Compare evidence candidates'],fields:[{...lookup('case_ids','evidence',true),multi:true}]},
    bank_preview:{title:['预览银行原文件','Preview original bank file'],fields:[lookup('account_id','accounts',true),f('attachment_id','file',{required:true})]},
    file:{title:['回取原文件','Retrieve original file'],fields:[select('kind',['evidence','bank_original','archive','document','upload','pending','notification'],true),required('id')]},
    pending:{title:['导出完整待核清单','Export complete pending list'],fields:[]},
  };
  const call=async(body:Row,key:string)=>{
    let current=await api('/api/assistant/threads/'+thread.id);
    const turn=await api('/api/assistant/threads/'+thread.id+'/turns',{expected_version:current.version,text:kind==='pending'?'请导出完整待核清单 / export pending list':t(...definitions[kind].title)});
    current=await api('/api/assistant/threads/'+thread.id);
    const r=await api('/api/assistant/threads/'+thread.id+'/tools',{expected_version:current.version,turn_id:turn.id,name:kind==='pending'?'export':kind,arguments:kind==='pending'?{kind:'pending',payload:{}}:clean(body)},'POST',key);
    setResult(r);onRefresh();return r;
  };
  const proposals=[...(thread.turns||[]).flatMap((turn:Row)=>(turn.result?.tools||[]).filter((tool:Row)=>tool.result?.status==='confirmation_required').map((tool:Row)=>tool.result)),...(result?.status==='confirmation_required'?[result]:[])];
  return <section className="assistant-tools"><h3>{t('查询、对比与文件','Queries, comparison & files')}</h3><div className="toolbar">{Object.entries(definitions).map(([key,d])=><button key={key} onClick={()=>{setKind(key);setResult(undefined)}}>{t(...d.title)}</button>)}</div>
    <Form key={kind+thread.id} title={t(...definitions[kind].title)} fields={definitions[kind].fields} submit={call}/>
    <ErrorBox error={error}/>{result&&<RecordView data={result}/>}
    {proposals.map((proposal:Row)=><section className="confirmation" key={proposal.id}><h3>{t('待确认操作','Action awaiting confirmation')}</h3><RecordView data={proposal.preview}/><button onClick={async()=>{try{await api('/api/assistant/proposals/'+proposal.id+'/confirm',{expected_version:proposal.version,nonce:proposal.nonce});onRefresh()}catch(e){setError(e)}}}>{t('核对后确认此操作','Confirm this reviewed action')}</button></section>)}
  </section>;
}
