import {ApiError,fileBlob} from './api';

// Called directly inside the gesture: the ClipboardItem promise obtains a new
// authorized artifact and rejects if the displayed job changed while awaiting it.
export async function copyCurrentPNG(path:string,current:()=>boolean){
  let sourceError:any;
  const fresh=fileBlob(path).then(blob=>{
    if(!current())throw new ApiError(409,'stale_export',null);
    if(blob.type!=='image/png'||blob.size<8)throw new ApiError(422,'clipboard_failed',null);
    return blob;
  }).catch(e=>{sourceError=e;throw e});
  void fresh.catch(()=>{});
  try{await navigator.clipboard.write([new ClipboardItem({'image/png':fresh})])}
  catch(e){throw sourceError||(e instanceof ApiError?e:new ApiError(0,'clipboard_failed',null))}
}
