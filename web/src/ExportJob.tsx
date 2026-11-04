import {useEffect,useState} from 'react';
import {api,download,fileBlob,ApiError,type Row} from './api';
import {RecordView,ErrorBox} from './ui';
import {useT} from './i18n';
export function ExportJob({identity,customer=false}:{identity:string;customer?:boolean}) {
  const t=useT(),[state,setState]=useState<Row>(),[error,setError]=useState<any>(),[png,setPNG]=useState<Blob>();
  const path=(customer?'/api/customer/exports/':'/api/exports/')+identity;
  useEffect(()=>{let live=true;const check=async()=>{try{const current=await api(path);if(!live)return;setState(current);if(current.ready){clearInterval(timer);try{const blob=await fileBlob(path+'/clipboard');if(live&&blob.type==='image/png')setPNG(blob)}catch{}}else if(['failed','blocked','paused','needs_review'].includes(current.status))clearInterval(timer)}catch(e){if(live)setError(e)}};const timer=setInterval(check,1500);void check();return()=>{live=false;clearInterval(timer)}},[identity,customer]);
  return <section><ErrorBox error={error}/>{state&&<RecordView data={state}/>}<div className="toolbar"><button disabled={!state?.ready} onClick={()=>download(path+'/file','MCSTAR.'+(state?.format||'xlsx')).catch(setError)}>{t('下载文件','Download file')}</button><button disabled={!png} onClick={async()=>{try{await navigator.clipboard.write([new ClipboardItem({'image/png':png!})])}catch{setError(new ApiError(0,'clipboard_failed',null))}}}>{t('复制图片','Copy image')}</button></div></section>;
}
