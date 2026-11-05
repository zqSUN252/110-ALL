import {useEffect,useRef,useState} from 'react';
import {api,download,fileBlob,ApiError,type Row} from './api';
import {RecordView,ErrorBox} from './ui';
import {useT} from './i18n';
import {copyCurrentPNG} from './clipboard';
export function ExportJob({identity,customer=false}:{identity:string;customer?:boolean}) {
  const t=useT(),[state,setState]=useState<Row>(),[error,setError]=useState<any>(),[prepared,setPrepared]=useState<string>();
  const path=(customer?'/api/customer/exports/':'/api/exports/')+identity;
  const active=useRef({path,epoch:0});
  if(active.current.path!==path)active.current={path,epoch:active.current.epoch+1};
  useEffect(()=>{
    const expected={...active.current};let live=true,inflight=false;
    const current=()=>live&&active.current.path===expected.path&&active.current.epoch===expected.epoch;
    setState(undefined);setError(null);setPrepared(undefined);
    const check=async()=>{if(inflight)return;inflight=true;try{
      const result=await api(path);if(!current())return;setState(result);
      if(result.ready){clearInterval(timer);if(result.format==='png'){
        const blob=await fileBlob(path+'/clipboard');if(!current())return;
        if(blob.type!=='image/png'||blob.size<8)throw new ApiError(422,'clipboard_failed',null);
        setPrepared(path);
      }}else if(['failed','blocked','paused','needs_review'].includes(result.status)){clearInterval(timer);setPrepared(undefined)}
    }catch(e){if(current()){setError(e);setPrepared(undefined)}}finally{inflight=false}};
    const timer=setInterval(check,1500);void check();return()=>{live=false;clearInterval(timer)};
  },[path]);
  const shown=state?.id===identity?state:undefined;
  return <section><ErrorBox error={error}/>{shown&&<RecordView data={shown}/>}<div className="toolbar"><button disabled={!shown?.ready} onClick={()=>download(path+'/file','MCSTAR.'+(shown?.format||'xlsx')).catch(setError)}>{t('下载文件','Download file')}</button><button disabled={!shown?.ready||shown.format!=='png'||prepared!==path} onClick={async()=>{const expected={...active.current};const current=()=>active.current.path===expected.path&&active.current.epoch===expected.epoch;try{setError(null);await copyCurrentPNG(path+'/clipboard',current)}catch(e){if(current()){setPrepared(undefined);setError(e)}}}}>{t('复制图片','Copy image')}</button></div></section>;
}
