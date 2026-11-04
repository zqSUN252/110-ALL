"""Strict allowlisted domain dispatcher; no SQL, paths, recipients or arbitrary URLs."""
from sqlalchemy import select,or_,func,delete
from app.common import DomainError,require
from app.models import (EvidenceCase,EvidenceImage,BankEntry,BankAccount,BankFile,BankSourceRow,BankClaim,Attachment,DialogueFile,Job,Ticket,Invoice,DialogueFocus)
from app.finance.service import record,decimal,day,locked
from app.finance.access import account_access
from .service import scoped_case,propose


def query(db,actor,thread,args):
    kind=args.kind
    if kind=='cases':
        require(actor,'evidence.read',thread.warehouse_id)
        q=select(EvidenceCase).where(EvidenceCase.warehouse_id==thread.warehouse_id,EvidenceCase.scope==thread.scope)
        if args.query:q=q.where(or_(EvidenceCase.payer_name.ilike('%'+args.query+'%'),EvidenceCase.business_reference.ilike('%'+args.query+'%'),EvidenceCase.transaction_reference.ilike('%'+args.query+'%')))
        if args.amount:q=q.where(EvidenceCase.amount==decimal(args.amount))
        if args.currency:q=q.where(EvidenceCase.currency==args.currency)
        if args.transaction_date:q=q.where(EvidenceCase.transaction_date==day(args.transaction_date))
        rows=list(db.scalars(q.order_by(EvidenceCase.created_at,EvidenceCase.id).offset(args.offset).limit(args.limit+1)))
        items=[scoped_case(db,actor,thread,row.id) for row in rows[:args.limit]]
        # Persist the exact shown order and each case's own version for later ordinal references.
        db.execute(delete(DialogueFocus).where(DialogueFocus.thread_id==thread.id))
        focus=[]
        for item in items:
            for image in item['images'] or [None]:
                if len(focus)>=40:break
                focus.append(DialogueFocus(thread_id=thread.id,position=len(focus)+1,case_id=item['id'],case_version=item['version'],image_id=image['id'] if image else None))
        db.add_all(focus);thread.version+=1;db.flush()
    elif kind=='bank':
        if not args.account_id:raise DomainError('account_required','Select an explicit bank account')
        account=locked(db,BankAccount,args.account_id);account_access(db,actor,account,'bank.read',thread.warehouse_id)
        if account.scope!=thread.scope:raise DomainError('source_scope','Account scope differs',403)
        q=select(BankEntry).where(BankEntry.account_id==account.id)
        if args.query:q=q.where(or_(BankEntry.description.ilike('%'+args.query+'%'),BankEntry.reference.ilike('%'+args.query+'%')))
        if args.amount:q=q.where(BankEntry.credit==decimal(args.amount))
        if args.currency:q=q.where(BankEntry.currency==args.currency)
        if args.transaction_date:q=q.where(BankEntry.transaction_date==day(args.transaction_date))
        rows=list(db.scalars(q.order_by(BankEntry.created_at,BankEntry.id).offset(args.offset).limit(args.limit+1)))
        items=[record(row)|{'original_rows':[record(r) for r in db.scalars(select(BankSourceRow).where(BankSourceRow.entry_id==row.id))],'claims':[record(c) for c in db.scalars(select(BankClaim).where(BankClaim.entry_id==row.id))]} for row in rows[:args.limit]]
    elif kind=='bank_files':
        if not args.account_id:raise DomainError('account_required','Select an explicit bank account')
        account=locked(db,BankAccount,args.account_id);account_access(db,actor,account,'bank.read',thread.warehouse_id)
        if account.scope!=thread.scope:raise DomainError('source_scope','Account scope differs',403)
        rows=list(db.scalars(select(BankFile).where(BankFile.account_id==account.id).order_by(BankFile.created_at,BankFile.id).offset(args.offset).limit(args.limit+1)))
        items=[record(r)|{'filename':db.get(Attachment,r.attachment_id).filename,'coverage':'unknown'} for r in rows[:args.limit]]
    elif kind=='archives':
        require(actor,'bank.export',thread.warehouse_id)
        rows=list(db.scalars(select(Attachment).where(Attachment.owner_type=='bank_archive',Attachment.owner_id==thread.warehouse_id).order_by(Attachment.created_at,Attachment.id).offset(args.offset).limit(args.limit+1)))
        from app.finance.authorization import export_file_access
        items=[]
        for r in rows[:args.limit]:export_file_access(db,actor,r);items.append({'id':r.id,'filename':r.filename,'media_type':r.media_type,'sha256':r.sha256})
        # Assistant original-template archives have their own typed file/provenance relation.
    elif kind=='templates':
        from app.models import DocumentTemplate
        require(actor,'documents.read',thread.warehouse_id)
        rows=list(db.scalars(select(DocumentTemplate).order_by(DocumentTemplate.created_at,DocumentTemplate.id).offset(args.offset).limit(args.limit+1)))
        items=[record(r) for r in rows[:args.limit]]
    elif kind=='files':
        rows=list(db.scalars(select(DialogueFile).where(DialogueFile.thread_id==thread.id).order_by(DialogueFile.created_at,DialogueFile.id).offset(args.offset).limit(args.limit+1)))
        items=[{'id':r.id,'kind':r.kind,'url':'/api/assistant/files/'+r.id} for r in rows[:args.limit]]
    else:
        cls,cap=(Ticket,'packing.read') if kind=='cargo' else (Invoice,'finance.read');require(actor,cap,thread.warehouse_id)
        if kind=='cargo' and thread.scope!='live':raise DomainError('source_scope','Operational cargo is live-only',403)
        q=select(cls).where(cls.warehouse_id==thread.warehouse_id)
        if kind!='cargo':q=q.where(cls.scope==thread.scope)
        if args.query:
            column=cls.receipt if kind=='cargo' else cls.reference
            q=q.where(column.ilike('%'+args.query+'%'))
        rows=list(db.scalars(q.order_by(cls.id).offset(args.offset).limit(args.limit+1)))
        if kind=='cargo':
            from app.packing.service import get_ticket
            items=[get_ticket(db,actor,r.id) for r in rows[:args.limit]]
        else:
            from app.finance.service import get_invoice
            items=[get_invoice(db,actor,r.id) for r in rows[:args.limit]]
    result={'kind':kind,'items':items,'offset':args.offset,'next_offset':args.offset+args.limit if len(rows)>args.limit else None,'coverage':'unknown' if kind=='bank' else 'selected_scope','executed':False}
    if kind=='cases':result['focus']=[record(f) for f in focus]
    return result


def dispatch(db,actor,thread,turn,name,args):
    if name=='source_assessment':
        scoped_case(db,actor,thread,args.case_id)
        from app.evidence.service import source_assessment_preview
        if args.message_id:
            from app.channels.service import operator_message,ChannelConfig
            from app.channels.source import same_boundary,require_source
            from app.models import ChannelMessage
            msg=locked(db,ChannelMessage,args.message_id);require_source(actor,msg,ChannelConfig.from_env(),'channels.read')
            if msg.warehouse_id!=thread.warehouse_id or msg.scope!=thread.scope:raise DomainError('source_scope','Supplement source differs',403)
            if thread.source_message_id and not same_boundary(msg,locked(db,ChannelMessage,thread.source_message_id)):raise DomainError('source_scope','Supplement source chat differs',403)
        return source_assessment_preview(db,actor,args.case_id,args.attachment_id,args.message_id)
    if name=='notification':
        from .files import notification
        return notification(db,actor,thread,turn,args)
    if name=='query':return query(db,actor,thread,args)
    if name=='case':return scoped_case(db,actor,thread,args.case_id)
    if name=='compare':
        from app.bank.service import decision
        results=[]
        for identity in args.case_ids:
            scoped_case(db,actor,thread,identity);case=locked(db,EvidenceCase,identity)
            if case.account_id:account_access(db,actor,locked(db,BankAccount,case.account_id),'bank.read',thread.warehouse_id)
            results.append({'id':case.id,'version':case.version,'comparison':decision(db,case)})
        return {'items':results,'executed':False}
    if name=='propose':return propose(db,actor,thread,turn,args)
    if name=='bank_preview':
        from .files import queue_bank_preview
        return queue_bank_preview(db,actor,thread,turn,args)
    if name=='file':
        from .files import link_file
        return link_file(db,actor,thread,turn,args.kind,args.id)
    if name=='export':
        from .files import queue_export
        return queue_export(db,actor,thread,turn,args)
    raise DomainError('unsupported_tool','Tool is not allowlisted')
