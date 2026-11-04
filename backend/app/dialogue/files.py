"""Authorized original/file projections and complete pending exports."""
from html import escape
import hashlib,json
from datetime import timedelta
from sqlalchemy import select
from app.common import DomainError,require,json_value,check_version
from app.models import (DialogueThread,DialogueTurn,DialogueFile,Job,Attachment,EvidenceCase,EvidenceImage,BankFile,BankAccount,DocumentExport,utcnow)
from app.attachments import read_file,put_file
from app.finance.service import locked,record
from app.finance.access import account_access
from app.channels.worker import actor_now
from .service import thread_access,scoped_case,explicit_action
from .commands import command


def send_access(db,actor,p):
    f=locked(db,DialogueFile,p['id']);thread=thread_access(db,actor,f.thread_id,'assistant.act');require(actor,'channels.send',thread.warehouse_id);authorize_file(db,actor,thread,f)


@command('assistant.file.send','id expected_version recipient',send_access)
def send_file(db,actor,p):
    from app.channels.service import queue_delivery,ChannelConfig
    from app.models import Delivery
    f=locked(db,DialogueFile,p['id']);check_version(f,p.get('expected_version'))
    thread=thread_access(db,actor,f.thread_id,'assistant.act')
    if f.kind=='bank_preview' or (f.kind not in ('document','notification') and not f.attachment_id):raise DomainError('export_pending','Wait for actual file')
    if f.kind=='document':
        from app.documents.exports import status
        if not status(db,f.export_id,actor=actor)['ready']:raise DomainError('export_pending','Wait for formal export')
    cfg=ChannelConfig.from_env()
    result=queue_delivery(db,actor,{'session':cfg.assistant_session,'recipient':p['recipient'],'text':'受授权文件','message_id':thread.source_message_id,'idempotency_key':'assistant-file:'+f.id+':'+p['idempotency_key']})
    d=locked(db,Delivery,result['id']);d.artifact_id=f.id
    return result|{'provider_accepted':False,'delivered':False}


def fingerprint(value):return hashlib.sha256(json.dumps(json_value(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def authorize_file(db,actor,thread,file):
    if file.thread_id!=thread.id:raise DomainError('file_scope','File belongs to another conversation',403)
    if file.kind=='document':
        from app.documents.exports import authorize_export
        objects=authorize_export(db,locked(db,DocumentExport,file.export_id),actor=actor)
        if thread.scope!='live' or any(o.warehouse_id!=thread.warehouse_id for o in objects):raise DomainError('source_scope','Formal file source moved outside conversation',403)
        return
    if file.kind=='notification':
        from app.models import Notification
        from app.documents.notifications import current_generation
        from app.documents.projection import today
        from app.documents.access import authorize
        n=locked(db,Notification,file.notification_id)
        authorize(db,actor,{'notification_id':n.id},'documents.notify');objects=current_generation(db,actor,n)
        if thread.scope!='live' or any(o.warehouse_id!=thread.warehouse_id for o in objects):raise DomainError('source_scope','Notice source moved outside conversation',403)
        if n.generated_on!=today(db):raise DomainError('stale_generation','Signature day changed; generate a fresh notice',409)
        return
    a=locked(db,Attachment,file.attachment_id) if file.attachment_id else None
    if file.kind=='upload':
        require(actor,'attachments.read')
        if not a or a.owner_type!='user' or a.owner_id!=actor.id:raise DomainError('forbidden','Original upload owner changed',403)
    elif file.kind=='evidence':
        image=locked(db,EvidenceImage,file.sources['image_id']);scoped_case(db,actor,thread,image.case_id)
        if image.attachment_id!=file.attachment_id:raise DomainError('file_scope','Evidence file relation changed',409)
    elif file.kind=='bank_original':
        bf=locked(db,BankFile,file.sources['bank_file_id']);account=locked(db,BankAccount,bf.account_id)
        account_access(db,actor,account,'bank.read',thread.warehouse_id)
        if account.scope!=thread.scope or bf.attachment_id!=file.attachment_id:raise DomainError('file_scope','Bank original source changed',403)
    elif file.kind in ('pending','bank_archive'):
        require(actor,'evidence.export' if file.kind=='pending' else 'bank.export',thread.warehouse_id)
        if file.kind=='pending':
            current=[scoped_case(db,actor,thread,item['id']) for item in file.sources['cases']]
            if fingerprint(current)!=file.sources['fingerprint']:raise DomainError('stale_export','Evidence sources changed; request a new complete export',409)
        else:
            from app.bank.archive import archive_snapshot
            data=archive_snapshot(db,actor,file.sources['payload'])
            if fingerprint(data)!=file.sources['fingerprint']:raise DomainError('stale_export','Archive sources changed',409)
        if a and (a.owner_type!='dialogue_file' or a.owner_id!=file.id):raise DomainError('file_scope','Artifact owner changed',403)
    elif file.kind=='archive':
        if not a or a.owner_type!='bank_archive' or a.owner_id!=thread.warehouse_id:raise DomainError('file_scope','Archive warehouse differs',403)
        from app.finance.authorization import export_file_access
        export_file_access(db,actor,a)
    elif file.kind=='bank_preview':
        account=locked(db,BankAccount,file.sources['account_id']);account_access(db,actor,account,'bank.import',thread.warehouse_id)
        if account.scope!=thread.scope or a.owner_type!='user' or a.owner_id!=actor.id:raise DomainError('file_scope','Import source changed',403)
    else:raise DomainError('unsupported_file','Unknown assistant file type')


def download(db,actor,identity):
    f=locked(db,DialogueFile,identity);thread=thread_access(db,actor,f.thread_id);authorize_file(db,actor,thread,f)
    if f.kind=='document':
        from app.documents.exports import download as formal_download
        return formal_download(db,db.info['settings'],f.export_id,actor=actor)
    if f.kind=='notification':
        from app.models import Notification
        return locked(db,Notification,f.notification_id).body.encode(),'text/plain'
    if not f.attachment_id:raise DomainError('export_pending','File is not ready',409)
    a=locked(db,Attachment,f.attachment_id);return read_file(db.info['settings'],a),a.media_type


def delivery_material(db,actor,identity):
    f=locked(db,DialogueFile,identity);thread=thread_access(db,actor,f.thread_id);authorize_file(db,actor,thread,f)
    if f.kind=='notification':
        from app.models import Notification
        return None,locked(db,Notification,f.notification_id).body
    if f.kind=='document':
        from app.documents.exports import download_attachment
        return download_attachment(db,f.export_id,actor=actor),None
    if f.kind=='bank_preview' or not f.attachment_id:raise DomainError('export_pending','Wait for actual file')
    return locked(db,Attachment,f.attachment_id),None


def link_file(db,actor,thread,turn,kind,identity):
    if kind=='pending':raise DomainError('export_required','Request an explicit complete pending export')
    sources={};attachment=None;export=None;notice=None
    if kind=='evidence':image=locked(db,EvidenceImage,identity);attachment=image.attachment_id;sources={'image_id':image.id}
    elif kind=='bank_original':bf=locked(db,BankFile,identity);attachment=bf.attachment_id;sources={'bank_file_id':bf.id}
    elif kind=='document':export=identity
    elif kind=='notification':notice=identity
    else:attachment=identity
    f=DialogueFile(thread_id=thread.id,turn_id=turn.id,kind=kind,attachment_id=attachment,export_id=export,notification_id=notice,sources=sources);db.add(f);db.flush();authorize_file(db,actor,thread,f)
    return {'id':f.id,'url':'/api/assistant/files/'+f.id,'kind':kind,'executed':False}


def notification(db,actor,thread,turn,args):
    from app.models import Ticket,Receipt,ReleaseMember
    from app.documents.notifications import render
    from app.api.finance import command_payload
    if thread.scope!='live':raise DomainError('source_scope','Formal notices require live operational sources',403)
    selected=[locked(db,Ticket,i) for i in args.ticket_ids]+[locked(db,Receipt,i) for i in args.receipt_ids]
    if args.order_id:selected.extend(locked(db,Ticket,m.ticket_id) for m in db.scalars(select(ReleaseMember).where(ReleaseMember.order_id==args.order_id)))
    if any(o.warehouse_id!=thread.warehouse_id for o in selected):raise DomainError('source_scope','Notice warehouse differs',403)
    result=render(db,actor,{**command_payload(args),'idempotency_key':'assistant-notice:'+turn.id})
    return link_file(db,actor,thread,turn,'notification',result['id'])|{'notification_id':result['id'],'text':result['text'],'sent':False}


def queue_bank_preview(db,actor,thread,turn,args):
    f=DialogueFile(thread_id=thread.id,turn_id=turn.id,kind='bank_preview',attachment_id=args.attachment_id,sources={'account_id':args.account_id})
    db.add(f);db.flush();authorize_file(db,actor,thread,f)
    if not db.scalar(select(DialogueFile.id).where(DialogueFile.thread_id==thread.id,DialogueFile.kind=='upload',DialogueFile.attachment_id==args.attachment_id)):raise DomainError('attachment_required','Attach this original to the conversation first')
    j=Job(kind='assistant.file',payload={'file_id':f.id,'actor_id':actor.id},idempotency_key='assistant.file:'+f.id);db.add(j);db.flush()
    return {'id':f.id,'job_id':j.id,'status':'pending','executed':False,'imported':False}


def queue_export(db,actor,thread,turn,args):
    if not explicit_action(turn.text):return {'status':'explicit_export_required','executed':False}
    kind=args.kind
    if kind=='document':
        from app.api.documents_inputs import Export as ExportInput
        from app.api.finance import command_payload
        from app.documents.exports import queue_export as formal_queue,selection
        from pydantic import ValidationError
        try:payload=command_payload(ExportInput.model_validate(args.payload))
        except ValidationError:raise DomainError('invalid_export','Invalid formal export arguments',422) from None
        objects=selection(db,payload)
        if thread.scope!='live' or any(o.warehouse_id!=thread.warehouse_id for o in objects):raise DomainError('source_scope','Export sources differ from conversation',403)
        result=formal_queue(db,actor,{**payload,'idempotency_key':'assistant-document:'+turn.id})
        link=link_file(db,actor,thread,turn,'document',result['id'])
        return result|link|{'export_id':result['id']}
    if kind=='pending':
        require(actor,'evidence.export',thread.warehouse_id)
        # Complete scope: intentionally no pagination or implicit 200-row limit.
        cases=[scoped_case(db,actor,thread,c.id) for c in db.scalars(select(EvidenceCase).where(EvidenceCase.warehouse_id==thread.warehouse_id,EvidenceCase.scope==thread.scope,EvidenceCase.state!='bank_verified').order_by(EvidenceCase.created_at,EvidenceCase.id))]
        sources={'cases':[{'id':c['id'],'version':c['version']} for c in cases],'snapshot':cases,'fingerprint':fingerprint(cases)}
    elif kind=='bank_archive':
        from app.api.finance import ArchiveInput,command_payload
        from app.bank.archive import archive_snapshot
        from pydantic import ValidationError
        try:payload=command_payload(ArchiveInput.model_validate(args.payload))
        except ValidationError:raise DomainError('invalid_archive','Invalid original-template archive arguments',422) from None
        if payload['warehouse_id']!=thread.warehouse_id:raise DomainError('source_scope','Archive warehouse differs',403)
        data=archive_snapshot(db,actor,payload)
        if data['scope']!=thread.scope:raise DomainError('source_scope','Archive scope differs',403)
        sources={'payload':payload,'snapshot':data,'fingerprint':fingerprint(data)}
    else:raise DomainError('export_kind','Unsupported export kind')
    f=DialogueFile(thread_id=thread.id,turn_id=turn.id,kind=kind,sources=sources);db.add(f);db.flush()
    j=Job(kind='assistant.file',payload={'file_id':f.id,'actor_id':actor.id},idempotency_key='assistant.file:'+f.id);db.add(j);db.flush()
    return {'id':f.id,'job_id':j.id,'status':'pending','url':'/api/assistant/files/'+f.id,'executed':False}


def pending_html(cases):
    sections=[]
    for case in cases:
        images=''.join('<li>'+escape(i['id'])+' / '+escape(i.get('source',''))+'</li>' for i in case['images'])
        sections.append('<article><h2>'+escape(case['id'])+'</h2><pre>'+escape(json.dumps(case,ensure_ascii=False,indent=2))+'</pre><ul>'+images+'</ul></article>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>完整待核清单</title><h1>完整待核清单</h1><p>共 '+str(len(cases))+' 条</p>'+''.join(sections)+'</html>').encode()


def file_once(factory,settings):
    identity=None;lease=None
    try:
        with factory() as db,db.begin():
            db.info['settings']=settings
            j=db.scalar(select(Job).where(Job.kind=='assistant.file',(Job.status=='pending')|((Job.status=='running')&(Job.locked_at<utcnow()-timedelta(minutes=10)))).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
            if not j:return {'status':'idle','called':False}
            identity=j.id;actor=actor_now(db,j.payload['actor_id']);f=locked(db,DialogueFile,j.payload['file_id']);thread=thread_access(db,actor,f.thread_id,'assistant.act');authorize_file(db,actor,thread,f)
            j.status='running';j.locked_at=utcnow();j.attempts+=1;lease=j.locked_at;kind=f.kind;sources=f.sources;fid=f.id
            original=read_file(settings,locked(db,Attachment,f.attachment_id)) if kind=='bank_preview' else None
            template=None
            if kind=='bank_archive':template=read_file(settings,locked(db,Attachment,sources['payload']['template_attachment_id']))
        # Parser/render work is outside all database transactions and financial guards.
        parsed=None
        if kind=='pending':content=pending_html(sources['snapshot']);mime='text/html';filename='pending-complete.html'
        elif kind=='bank_archive':
            from app.bank.archive import render_snapshot
            content,mime,report=render_snapshot(settings,template,sources['snapshot']);filename='bank-archive.xlsx'
        elif kind=='bank_preview':
            from app.bank.service import parse_bytes
            parsed=parse_bytes(settings,original)
        with factory() as db,db.begin():
            from .commands import domain_guard
            db.info['settings']=settings;domain_guard(db);j=locked(db,Job,identity)
            if j.status!='running' or j.locked_at!=lease:return {'status':'lease_lost','called':False}
            actor=actor_now(db,j.payload['actor_id']);f=locked(db,DialogueFile,fid);thread=thread_access(db,actor,f.thread_id,'assistant.act');authorize_file(db,actor,thread,f)
            if kind=='bank_preview':
                from app.bank.service import import_preview
                a=locked(db,Attachment,f.attachment_id)
                if a.sha256!=hashlib.sha256(original).hexdigest():raise DomainError('source_changed','Original upload changed',409)
                db.info['bank_preparsed']={a.id:(a.sha256,parsed)}
                result=import_preview(db,actor,{'account_id':sources['account_id'],'attachment_id':a.id,'idempotency_key':'assistant-bank-preview:'+fid})
                f.sources={**f.sources,'preview':result}
            else:
                a=put_file(db,actor,content,filename,mime,'dialogue_file',fid);f.attachment_id=a.id
            f.version+=1;j.status='completed';j.error_code=None
            return {'id':fid,'status':'completed','called':False}
    except (DomainError,ValueError,OSError) as exc:
        if identity:
            with factory() as db,db.begin():
                j=locked(db,Job,identity)
                if j.status=='pending' or (j.status=='running' and j.locked_at==lease):j.status='failed';j.error_code=exc.code if isinstance(exc,DomainError) else 'file_failed'
            return {'status':'failed','called':False}
        raise
