"""Current action AND response authorization, evaluated before idempotency replay.

This never checks expected versions or action expiry: a still-authorized replay
recovers its original committed result even after that mutation changed versions.
"""
from sqlalchemy import select
from app.common import DomainError,require
from app.models import (Party,PartyWarehouse,Invoice,Allocation,Payment,BankAccount,BankAccountWarehouse,
    BankFile,BankEntry,BankClaim,EvidenceCase,EvidenceImage,ChannelMessage,ChannelAction)
from .access import account_access,party_member,payment_access


def get(db,model,identity):
    obj=db.get(model,identity) if identity else None
    if not obj:raise DomainError('not_found','Record not found',404)
    return obj


def case_read(db,actor,case):
    require(actor,'evidence.read',case.warehouse_id)
    for image in db.scalars(select(EvidenceImage).where(EvidenceImage.case_id==case.id)):
        if image.message_id:
            message=get(db,ChannelMessage,image.message_id)
            require(actor,'channels.read',message.warehouse_id)
            if message.warehouse_id!=case.warehouse_id or message.scope!=case.scope:raise DomainError('source_scope','Stored evidence source ownership differs',403)
            from app.channels.source import context_authorized
            context_authorized(db,actor,message,image.context)


def authorize(db,actor,name,p):
    rules={
        'warehouse.create':('warehouses.manage',None),'party.create':('parties.manage',None),'party.membership':('parties.manage',Party),
        'invoice.create':('payables.manage' if p.get('kind')=='AP' else 'finance.invoice',None),
        'invoice.adjust':('finance.adjust',Invoice),'invoice.discount':('finance.discount',Invoice),
        'payment.create':('finance.payment',None),'payment.post':('finance.post',Payment),'payment.attach_bank':('finance.post',Payment),
        'payment.reverse':('finance.reverse',Payment),'payable.pay':('payables.pay',None),'opening.create':('finance.opening',None),
        'bank.account.create':('bank.manage',None),'bank.account.membership':('bank.manage',BankAccount),
        'bank.import.preview':('bank.import',BankAccount),'bank.import.confirm':('bank.import',BankFile),
        'bank.case.match':('bank.review',EvidenceCase),'bank.case.review':('bank.review',EvidenceCase),'bank.archive':('bank.export',None),
        'finance.export':('finance.export',None),'evidence.create':('evidence.write',None),'evidence.attach':('evidence.write',EvidenceCase),
        'evidence.review':('evidence.review',EvidenceCase),'evidence.bind_account':('evidence.review',EvidenceCase),'evidence.recognize':('evidence.recognize',EvidenceCase),
        'channel.action.prepare':('channels.act',ChannelMessage),'channel.action.execute':('channels.act',ChannelAction),'channel.delivery.queue':('channels.send',None)}
    cap,model=rules[name];warehouse=p.get('warehouse_id');obj=None
    if model:
        identity=p.get('account_id') if name=='bank.import.preview' else p.get('message_id') if name=='channel.action.prepare' else p.get('id')
        obj=get(db,model,identity)
        if isinstance(obj,BankFile):obj=get(db,BankAccount,obj.account_id)
        if isinstance(obj,Invoice) and obj.kind=='AP' and name=='invoice.adjust':cap='payables.manage'
        warehouse=getattr(obj,'warehouse_id',warehouse)
    if isinstance(obj,BankAccount):account_access(db,actor,obj,cap)
    elif isinstance(obj,Payment):payment_access(db,actor,obj,cap)
    else:require(actor,cap,warehouse)

    if name in ('party.membership','bank.account.membership'):
        require(actor,cap,p['warehouse_id']);require(actor,cap,obj.warehouse_id)
        cls,key=(PartyWarehouse,PartyWarehouse.party_id) if isinstance(obj,Party) else (BankAccountWarehouse,BankAccountWarehouse.account_id)
        for wh in db.scalars(select(cls.warehouse_id).where(key==obj.id)):require(actor,cap,wh)
    if p.get('party_id') and name in ('invoice.create','payment.create','payable.pay','opening.create'):
        party_member(db,p['party_id'],warehouse)
    if name in ('payment.post','payable.pay'):
        for row in p.get('allocations') or []:
            inv=get(db,Invoice,row.get('invoice_id'));require(actor,cap,inv.warehouse_id)
            party_member(db,inv.party_id,inv.warehouse_id)
            if inv.party_id!=(obj.party_id if obj else p['party_id']):
                require(actor,'finance.cross_party',inv.warehouse_id)
                if obj:payment_access(db,actor,obj,'finance.cross_party')
    if name=='invoice.adjust':
        for payment in db.scalars(select(Payment).join(Allocation,Allocation.payment_id==Payment.id).where(Allocation.invoice_id==obj.id).distinct()):payment_access(db,actor,payment,cap)
        if 'discount_total_ngn' in p:require(actor,'finance.discount',warehouse)
    if name in ('payment.attach_bank','payment.post') and (name=='payment.attach_bank' or p.get('approval')=='bank'):
        case=get(db,EvidenceCase,p.get('case_id'));require(actor,'evidence.review',case.warehouse_id);require(actor,'finance.post',case.warehouse_id)
        require(actor,'bank.review',obj.warehouse_id)
        account_access(db,actor,get(db,BankAccount,case.account_id),'bank.review',case.warehouse_id)
    if name=='payment.reverse' and p.get('claim_resolution')=='released':
        payment_access(db,actor,obj,'bank.release')
    if name.startswith('evidence.'):
        if name in ('evidence.create','evidence.review','evidence.attach','evidence.bind_account'):
            if obj:case_read(db,actor,obj)
            else:require(actor,'evidence.read',warehouse)
        if isinstance(obj,EvidenceCase) and obj.payment_id:payment_access(db,actor,get(db,Payment,obj.payment_id),cap)
        if p.get('account_id'):
            account_access(db,actor,get(db,BankAccount,p['account_id']),'bank.read',warehouse)
        if name=='evidence.bind_account' and obj.account_id:
            account_access(db,actor,get(db,BankAccount,obj.account_id),'bank.read',warehouse)
        if p.get('payment_id'):payment_access(db,actor,get(db,Payment,p['payment_id']),'finance.post')
        from app.bank.service import attachment_for_actor
        for image in p.get('images',[]):
            attachment_for_actor(db,actor,image['attachment_id'])
            if image.get('message_id'):
                message=get(db,ChannelMessage,image['message_id']);require(actor,'channels.read',message.warehouse_id)
                if message.warehouse_id!=warehouse:raise DomainError('source_scope','Image message warehouse differs',403)
    if name=='bank.import.preview':
        account_access(db,actor,obj,'bank.read')
        from app.bank.service import attachment_for_actor
        attachment_for_actor(db,actor,p.get('attachment_id'))
    if name=='bank.import.confirm' and p.get('trusted_reference'):account_access(db,actor,obj,'bank.identity')
    if name in ('bank.case.match','bank.case.review') and obj.account_id:
        account_access(db,actor,get(db,BankAccount,obj.account_id),'bank.review',obj.warehouse_id)
        if obj.payment_id:payment_access(db,actor,get(db,Payment,obj.payment_id),'bank.review')
        if name=='bank.case.review':
            action=p.get('action','confirm')
            if action in ('confirm_manual','resolve','resolve_manual'):require(actor,'bank.override',warehouse)
            if action=='release':require(actor,'bank.release',warehouse)
            if p.get('entry_id'):account_access(db,actor,get(db,BankAccount,get(db,BankEntry,p['entry_id']).account_id),'bank.review',warehouse)
    if name=='bank.case.review':
        for claim in db.scalars(select(BankClaim).where(BankClaim.case_id==obj.id,BankClaim.state!='released')):
            account_access(db,actor,get(db,BankAccount,get(db,BankEntry,claim.entry_id).account_id),'bank.review',warehouse)
            if p.get('action')=='release':require(actor,'bank.release',warehouse)
            if p.get('action') in ('resolve','resolve_manual'):require(actor,'bank.override',warehouse)
    if name=='bank.archive':
        from app.bank.service import attachment_for_actor
        attachment_for_actor(db,actor,p.get('template_attachment_id'))
        for row in p.get('claims',[]):
            claim=get(db,BankClaim,row['id']);case=get(db,EvidenceCase,claim.case_id)
            require(actor,'bank.export',case.warehouse_id)
            account_access(db,actor,get(db,BankAccount,get(db,BankEntry,claim.entry_id).account_id),'bank.export',case.warehouse_id)
            if claim.payment_id:payment_access(db,actor,get(db,Payment,claim.payment_id),'bank.export')
    if name.startswith('channel.'):
        from app.channels.service import ChannelConfig,operator_message,action_target
        config=ChannelConfig.from_env()
        if name=='channel.action.prepare':
            msg=operator_message(db,actor,p['message_id'],config);action_target(db,actor,msg,p['action'],p['payload'])
            authorize(db,actor,{'payment.post':'payment.post','evidence.review':'evidence.review','bank.review':'bank.case.review'}[p['action']],p['payload'])
        elif name=='channel.action.execute':
            if obj.actor_id!=actor.id:raise DomainError('forbidden','Action belongs to another operator',403)
            origin=operator_message(db,actor,obj.message_id,config);confirmation=operator_message(db,actor,p['message_id'],config)
            from app.channels.source import same_boundary
            if not same_boundary(origin,confirmation):raise DomainError('source_scope','Confirmation provenance differs',403)
            action_target(db,actor,origin,obj.action,obj.payload)
            authorize(db,actor,{'payment.post':'payment.post','evidence.review':'evidence.review','bank.review':'bank.case.review'}[obj.action],obj.payload)
        elif p.get('message_id'):operator_message(db,actor,p['message_id'],config)


def authorize_response(db,actor,name,result):
    """A cached snapshot can retain a former relationship after a rebind. Check
    the snapshot's protected sources as well as the current target relationships."""
    if name in ('payment.create','payment.post','payment.attach_bank','payment.reverse','payable.pay'):
        capability={'payment.create':'finance.payment','payment.reverse':'finance.reverse','payable.pay':'payables.pay'}.get(name,'finance.post')
        payment_access(db,actor,get(db,Payment,result['id']),capability)
    if name=='channel.action.execute' and result.get('executed'):
        action=get(db,ChannelAction,result['id'])
        authorize_response(db,actor,{'payment.post':'payment.post','evidence.review':'evidence.review','bank.review':'bank.case.review'}[action.action],result['result'])
    if name in ('evidence.create','evidence.review','evidence.attach','evidence.bind_account'):
        case=get(db,EvidenceCase,result['id']);case_read(db,actor,case)
        for image in result.get('images',[]):
            if image.get('message_id'):
                message=get(db,ChannelMessage,image['message_id'])
                from app.channels.source import context_authorized
                context_authorized(db,actor,message,image.get('context') or {})
        if name=='evidence.bind_account':
            for review in result.get('reviews',[]):
                if review.get('action')=='bind_account':
                    for key in ('previous_account_id','account_id'):
                        account_id=review.get('fields',{}).get(key)
                        if account_id:account_access(db,actor,get(db,BankAccount,account_id),'bank.read',case.warehouse_id)
    if name=='bank.case.review' and result.get('claim'):
        claim=result['claim'];case=get(db,EvidenceCase,claim['case_id']);entry=get(db,BankEntry,claim['entry_id'])
        account_access(db,actor,get(db,BankAccount,entry.account_id),'bank.review',case.warehouse_id)
    if name in ('finance.export','bank.archive'):export_access(db,actor,name,result)


def export_access(db,actor,name,result):
    """Immutable export provenance authorizes both replay and later file reads.
    Historic files remain evidence even if their bank basis is later disputed."""
    key='source_payment_ids' if name=='finance.export' else 'source_claim_ids'
    if key not in result:
        if '*' not in actor.capabilities:raise DomainError('export_provenance_required','Legacy export needs a complete authorized provenance review',403)
        return
    require(actor,'finance.export' if name=='finance.export' else 'bank.export',result['file']['owner_id'])
    for identity in result[key]:
        if name=='finance.export':payment_access(db,actor,get(db,Payment,identity),'finance.export')
        else:
            claim=get(db,BankClaim,identity);case=get(db,EvidenceCase,claim.case_id);entry=get(db,BankEntry,claim.entry_id)
            account_access(db,actor,get(db,BankAccount,entry.account_id),'bank.export',case.warehouse_id)
            if claim.payment_id:payment_access(db,actor,get(db,Payment,claim.payment_id),'bank.export')


def export_file_access(db,actor,attachment):
    from app.models import AuditEvent
    name='finance.export' if attachment.owner_type=='finance_export' else 'bank.archive'
    event=db.scalar(select(AuditEvent).where(AuditEvent.action==name,AuditEvent.entity_id==attachment.id).order_by(AuditEvent.created_at).limit(1))
    if not event:raise DomainError('export_provenance_required','Export provenance missing',403)
    export_access(db,actor,name,event.after)
