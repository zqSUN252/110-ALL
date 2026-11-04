"""Shared thread, focus and confirmation services. Financial effects remain domain-owned."""
from datetime import timedelta
import hashlib,hmac,re,secrets
from sqlalchemy import select,delete
from pydantic import ValidationError
from app.common import DomainError,require,check_version,json_value
from app.models import (DialogueThread,DialogueTurn,DialogueFocus,DialogueFile,DialogueProposal,Job,EvidenceCase,EvidenceImage,ChannelMessage,BankFile,BankAccount,Payment,Attachment,utcnow)
from app.finance.service import locked,record
from app.channels.service import ChannelConfig,operator_message
from app.channels.source import same_boundary,require_source
from app.channels.worker import actor_now
from .commands import command
from .inputs import ACTION_MODELS,command_payload


def thread_access(db,actor,identity,cap='assistant.read'):
    thread=locked(db,DialogueThread,identity);require(actor,cap,thread.warehouse_id)
    if actor.id!=thread.actor_id:raise DomainError('forbidden','Conversation belongs to another operator',403)
    if thread.source_message_id:
        if not db.info.get('settings') or not db.info['settings'].collection_enabled:raise DomainError('collection_paused','Channel conversations are paused',409)
        operator_message(db,actor,thread.source_message_id,ChannelConfig.from_env())
    return thread


def scoped_case(db,actor,thread,identity):
    from app.evidence.service import get_case
    case=locked(db,EvidenceCase,identity)
    if case.warehouse_id!=thread.warehouse_id or case.scope!=thread.scope:raise DomainError('source_scope','Case differs from conversation boundary',403)
    result=get_case(db,actor,identity)
    if thread.source_message_id:
        config=ChannelConfig.from_env();origin=operator_message(db,actor,thread.source_message_id,config)
        for image in db.scalars(select(EvidenceImage).where(EvidenceImage.case_id==identity)):
            if image.message_id:
                msg=db.get(ChannelMessage,image.message_id);require_source(actor,msg,config,'channels.read')
                if not same_boundary(origin,msg):raise DomainError('source_scope','Case belongs to a different source chat',403)
    return result


def thread_mutation(db,actor,p):thread_access(db,actor,p['id'],'assistant.act')


def create_access(db,actor,p):
    require(actor,'assistant.act',p.get('warehouse_id'))
    if p.get('source_message_id'):
        if not db.info['settings'].collection_enabled:raise DomainError('collection_paused','Channel work paused',409)
        msg=operator_message(db,actor,p['source_message_id'],ChannelConfig.from_env())
        if msg.warehouse_id!=p.get('warehouse_id') or msg.scope!=p.get('scope','live'):raise DomainError('source_scope','Conversation source differs',403)


@command('assistant.thread.create','warehouse_id scope title source_message_id',create_access)
def create_thread(db,actor,p):
    if p.get('scope','live') not in ('sample','live'):raise DomainError('invalid_scope','Explicit scope required')
    row=DialogueThread(actor_id=actor.id,warehouse_id=p['warehouse_id'],scope=p.get('scope','live'),title=p.get('title',''),source_message_id=p.get('source_message_id'))
    db.add(row);db.flush();return record(row)


@command('assistant.turn','id expected_version text source_message_id attachment_ids',thread_mutation)
def submit_turn(db,actor,p):
    thread=thread_access(db,actor,p['id'],'assistant.act');check_version(thread,p.get('expected_version'))
    body=p['text']
    if not isinstance(body,str) or len(body)>16000 or (not body and not p.get('attachment_ids')):raise DomainError('invalid_body','Text or at least one explicit original attachment required')
    mid=p.get('source_message_id')
    if thread.source_message_id:
        config=ChannelConfig.from_env();msg=operator_message(db,actor,mid,config);origin=operator_message(db,actor,thread.source_message_id,config)
        if not same_boundary(msg,origin) or body!=(msg.body or ''):raise DomainError('source_scope','Use original signed source message body',403)
    elif mid:raise DomainError('source_scope','Web conversation cannot rebind to a channel',403)
    for aid in p.get('attachment_ids',[]):
        a=locked(db,Attachment,aid)
        if a.owner_type!='user' or a.owner_id!=actor.id:raise DomainError('forbidden','Upload must belong to current operator',403)
        require(actor,'attachments.read')
    j=Job(kind='assistant.turn',payload={'actor_id':actor.id},idempotency_key='assistant.turn:'+thread.id+':'+p['idempotency_key']);db.add(j);db.flush()
    turn=DialogueTurn(thread_id=thread.id,job_id=j.id,source_message_id=mid,text=body);db.add(turn);db.flush()
    for aid in p.get('attachment_ids',[]):
        db.add(DialogueFile(thread_id=thread.id,turn_id=turn.id,attachment_id=aid,kind='upload',sources={}))
        image=db.scalar(select(EvidenceImage).join(EvidenceCase,EvidenceCase.id==EvidenceImage.case_id).where(EvidenceImage.attachment_id==aid,EvidenceCase.warehouse_id==thread.warehouse_id,EvidenceCase.scope==thread.scope).order_by(EvidenceImage.created_at.desc()).limit(1))
        if image:
            current=scoped_case(db,actor,thread,image.case_id)
            focus=list(db.scalars(select(DialogueFocus).where(DialogueFocus.thread_id==thread.id).order_by(DialogueFocus.position)))
            if len(focus)<40 and not any(f.image_id==image.id for f in focus):db.add(DialogueFocus(thread_id=thread.id,position=len(focus)+1,case_id=image.case_id,case_version=current['version'],image_id=image.id))
    thread.version+=1
    return {'id':turn.id,'thread_id':thread.id,'thread_version':thread.version,'job_id':j.id,'status':j.status,'executed':False}


@command('assistant.focus','id expected_version items',thread_mutation)
def set_focus(db,actor,p):
    thread=thread_access(db,actor,p['id'],'assistant.act');check_version(thread,p.get('expected_version'))
    if not isinstance(p['items'],list) or len(p['items'])>40:raise DomainError('focus_limit','At most 40 exact focus records')
    rows=[]
    for pos,item in enumerate(p['items'],1):
        case=scoped_case(db,actor,thread,item['case_id'])
        if case['version']!=item.get('expected_version'):raise DomainError('stale_version','Focus source changed',409)
        if item.get('image_id') not in [x['id'] for x in case['images']]+[None]:raise DomainError('image_scope','Image is not a member of this case')
        rows.append(DialogueFocus(thread_id=thread.id,position=pos,case_id=case['id'],case_version=case['version'],image_id=item.get('image_id')))
    db.execute(delete(DialogueFocus).where(DialogueFocus.thread_id==thread.id));db.add_all(rows);thread.version+=1;db.flush()
    return {'id':thread.id,'version':thread.version,'focus':[record(r) for r in rows]}


def get_thread(db,actor,identity):
    thread=thread_access(db,actor,identity)
    turns=[]
    for t in db.scalars(select(DialogueTurn).where(DialogueTurn.thread_id==identity).order_by(DialogueTurn.created_at,DialogueTurn.id)):
        for tool in (t.result or {}).get('tools',[]):authorize_result(db,actor,thread,tool.get('tool'),tool.get('result',{}))
        j=db.get(Job,t.job_id);turns.append(record(t)|{'status':j.status,'error_code':j.error_code})
    for focus in db.scalars(select(DialogueFocus).where(DialogueFocus.thread_id==identity)):scoped_case(db,actor,thread,focus.case_id)
    from .files import authorize_file
    for file in db.scalars(select(DialogueFile).where(DialogueFile.thread_id==identity)):authorize_file(db,actor,thread,file)
    return record(thread)|{'turns':turns,'focus':[record(f) for f in db.scalars(select(DialogueFocus).where(DialogueFocus.thread_id==identity).order_by(DialogueFocus.position))], 'files':[{'id':f.id,'kind':f.kind,'url':'/api/assistant/files/'+f.id} for f in db.scalars(select(DialogueFile).where(DialogueFile.thread_id==identity))]}


def authorize_result(db,actor,thread,name,result):
    """Cached tool results are not an authorization bypass after grants/source changes."""
    if 'error' in result:return
    if name=='case':scoped_case(db,actor,thread,result['id'])
    elif name=='source_assessment':
        scoped_case(db,actor,thread,result['case_id'])
        from app.evidence.service import source_assessment_preview
        source_assessment_preview(db,actor,result['case_id'],result['attachment_id'],result.get('message_id'))
    elif name=='compare':
        for item in result.get('items',[]):
            case=scoped_case(db,actor,thread,item['id'])
            if case.get('account_id'):
                from app.finance.access import account_access
                account_access(db,actor,locked(db,BankAccount,case['account_id']),'bank.read',thread.warehouse_id)
    elif name=='query':
        kind=result.get('kind')
        for item in result.get('items',[]):
            if kind=='cases':scoped_case(db,actor,thread,item['id'])
            elif kind=='bank':
                from app.finance.access import account_access
                account_access(db,actor,locked(db,BankAccount,item['account_id']),'bank.read',thread.warehouse_id)
                for claim in item.get('claims',[]):
                    case=locked(db,EvidenceCase,claim['case_id']);require(actor,'evidence.read',case.warehouse_id)
            elif kind=='invoices':
                from app.finance.service import get_invoice
                current=get_invoice(db,actor,item['id'])
                if current['warehouse_id']!=thread.warehouse_id or current['scope']!=thread.scope:raise DomainError('source_scope','Invoice moved outside conversation',403)
            elif kind=='cargo':
                from app.packing.service import get_ticket
                current=get_ticket(db,actor,item['id'])
                if current['warehouse_id']!=thread.warehouse_id or thread.scope!='live':raise DomainError('source_scope','Cargo moved outside conversation',403)
            elif kind=='files':
                from .files import authorize_file
                authorize_file(db,actor,thread,locked(db,DialogueFile,item['id']))
            elif kind=='bank_files':
                from app.finance.access import account_access
                account_access(db,actor,locked(db,BankAccount,locked(db,BankFile,item['id']).account_id),'bank.read',thread.warehouse_id)
            elif kind=='archives':
                from app.finance.authorization import export_file_access
                export_file_access(db,actor,locked(db,Attachment,item['id']))
            elif kind=='templates':require(actor,'documents.read',thread.warehouse_id)
    elif name=='propose' and result.get('status')=='confirmation_required':
        proposal=locked(db,DialogueProposal,result['id'])
        if proposal.thread_id!=thread.id:raise DomainError('source_scope','Proposal conversation differs',403)
        from app.finance.authorization import authorize
        authorize(db,actor,{'bank.review':'bank.case.review'}.get(proposal.action,proposal.action),proposal.payload)
        if proposal.action!='bank.import.confirm':scoped_case(db,actor,thread,proposal.payload['id'])
    elif name in ('file','export','bank_preview','notification') and result.get('id'):
        from .files import authorize_file
        file=db.get(DialogueFile,result['id'])
        if not file and result.get('export_id'):file=db.scalar(select(DialogueFile).where(DialogueFile.thread_id==thread.id,DialogueFile.export_id==result['export_id']))
        if not file:raise DomainError('file_scope','File provenance unavailable',403)
        authorize_file(db,actor,thread,file)


def explicit_action(text):
    if any(v in text for v in ('?','？','如果','假如','是否','能否','可否','怎么','如何','不要','别','不执行')) or re.search(r'\b(if|would|could|whether|how|why|don.t|not)\b',text,re.I):return False
    return bool(re.search(r'确认|修正|改为|清空|暂缓|撤回|排除|导入|补充|附加|导出|归档|生成|confirm|correct|clear|defer|retract|reject|import|attach|export|archive',text,re.I))


def human_allows_action(text,action,payload):
    if not explicit_action(text):return False
    words={'evidence.attach':r'补充|附加|attach','bank.import.confirm':r'导入|import','payment.post':r'核销|付款|post'}
    if action=='evidence.review':words[action]={'correct':r'修正|改为|清空|correct|clear','defer':r'暂缓|defer','retract':r'撤回|retract','reject':r'排除|否定|reject'}.get(payload.get('action','correct'),'(?!)')
    if action=='bank.review':words[action]={'defer':r'暂缓|defer','retract':r'撤回|retract','release':r'释放|release'}.get(payload.get('action','confirm'),r'确认|confirm|resolve')
    return bool(re.search(words.get(action,'(?!)'),text,re.I))


def validate_proposal(db,actor,thread,action,payload):
    from app.finance.authorization import authorize
    if action not in ACTION_MODELS:raise DomainError('unsupported_action','Unsupported assistant action')
    model=BankFile if action=='bank.import.confirm' else Payment if action=='payment.post' else EvidenceCase
    target=locked(db,model,payload['id'])
    if model is BankFile:
        account=locked(db,BankAccount,target.account_id)
        from app.finance.access import account_access
        account_access(db,actor,account,'bank.import',thread.warehouse_id)
        if account.scope!=thread.scope:raise DomainError('source_scope','Bank scope differs',403)
    else:
        if target.warehouse_id!=thread.warehouse_id or target.scope!=thread.scope:raise DomainError('source_scope','Action target differs from thread',403)
        if model is EvidenceCase:scoped_case(db,actor,thread,target.id)
    authorize(db,actor,{'bank.review':'bank.case.review'}.get(action,action),payload)
    check_version(target,payload.get('expected_version'))
    return target


def propose(db,actor,thread,turn,args):
    if not human_allows_action(turn.text,args.action,args.payload):return {'status':'question_only','executed':False}
    action=args.action;payload=args.payload.copy();target=args.target_id
    if args.focus_position:
        focus=db.scalar(select(DialogueFocus).where(DialogueFocus.thread_id==thread.id,DialogueFocus.position==args.focus_position))
        if not focus:raise DomainError('focus_missing','No exact focus at that position',409)
        if target and target!=focus.case_id:raise DomainError('focus_mismatch','Explicit target and focus differ')
        target=focus.case_id
        if payload.get('expected_version',focus.case_version)!=focus.case_version:raise DomainError('focus_version','Use the selected image version',409)
        payload['expected_version']=focus.case_version
    if not target:raise DomainError('target_required','Select an exact current record')
    try:payload=command_payload(ACTION_MODELS[action].model_validate(payload))|{'id':target}
    except (ValidationError,KeyError):raise DomainError('invalid_action','Action arguments do not match strict schema',422) from None
    validate_proposal(db,actor,thread,action,payload)
    if action=='payment.post':return {'status':'web_confirmation_required','url':'/finance/payments/'+target,'executed':False,'reason':'Complete current invoice/party allocation preview and explicit financial confirmation required'}
    nonce=secrets.token_urlsafe(32)
    row=DialogueProposal(thread_id=thread.id,turn_id=turn.id,action=action,payload=payload,nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),expires_at=utcnow()+timedelta(minutes=10))
    db.add(row);db.flush()
    return {'id':row.id,'version':row.version,'status':'confirmation_required','preview':payload,'nonce':nonce,'confirmation_command':'/confirm '+row.id+' '+nonce,'expires_at':row.expires_at.isoformat(),'executed':False}


def proposal_access(db,actor,p):
    proposal=locked(db,DialogueProposal,p['id']);thread=thread_access(db,actor,proposal.thread_id,'assistant.act')
    # Replays must reauthorize the action without checking a now-consumed record version.
    from app.finance.authorization import authorize
    authorize(db,actor,{'bank.review':'bank.case.review'}.get(proposal.action,proposal.action),proposal.payload)
    if proposal.action not in ('bank.import.confirm','payment.post'):scoped_case(db,actor,thread,proposal.payload['id'])
    elif proposal.action=='bank.import.confirm':
        from app.finance.access import account_access
        bank_file=locked(db,BankFile,proposal.payload['id']);account=locked(db,BankAccount,bank_file.account_id);account_access(db,actor,account,'bank.import',thread.warehouse_id)
        if account.scope!=thread.scope:raise DomainError('source_scope','Bank source moved outside conversation',403)


@command('assistant.confirm','id expected_version nonce source_message_id',proposal_access)
def confirm(db,actor,p):
    proposal=locked(db,DialogueProposal,p['id']);thread=thread_access(db,actor,proposal.thread_id,'assistant.act')
    check_version(proposal,p.get('expected_version'))
    if proposal.used_at or proposal.expires_at<=utcnow():raise DomainError('action_expired','Confirmation expired or already used',409)
    if not isinstance(p.get('nonce'),str) or not hmac.compare_digest(hashlib.sha256(p['nonce'].encode()).hexdigest(),proposal.nonce_hash):raise DomainError('confirmation_required','Valid explicit confirmation nonce required',403)
    if thread.source_message_id:
        config=ChannelConfig.from_env();msg=operator_message(db,actor,p.get('source_message_id'),config);origin=operator_message(db,actor,thread.source_message_id,config)
        if not same_boundary(msg,origin) or msg.body!='/confirm '+proposal.id+' '+p['nonce']:raise DomainError('confirmation_required','Exact signed confirmation from same source required',403)
    validate_proposal(db,actor,thread,proposal.action,proposal.payload)
    from app.evidence.service import review_case,attach_images
    from app.bank.service import review_case as bank_review,confirm_import
    handler={'evidence.review':review_case,'evidence.attach':attach_images,'bank.review':bank_review,'bank.import.confirm':confirm_import}[proposal.action]
    result=handler(db,actor,{**proposal.payload,'idempotency_key':'assistant-confirm:'+proposal.id})
    proposal.used_at=utcnow();proposal.result=result;proposal.version+=1
    return {'id':proposal.id,'version':proposal.version,'executed':True,'result':result,'text':'已提交数据库操作；结果以当前记录状态为准。'}
