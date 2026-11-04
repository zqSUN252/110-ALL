"""Task 2 independent review regressions: real PG/HTTP, only remote transport faked."""
import asyncio
from dataclasses import replace
from io import BytesIO
from uuid import uuid4
import hashlib,hmac,json
import httpx
import pytest
from openpyxl import Workbook
from PIL import Image
from sqlalchemy import select
from fastapi.testclient import TestClient
from app.common import Actor,DomainError
from app.models import BankClaim,BankEntry,EvidenceCase,EvidenceReview,Payment,FinanceEvent,ChannelMessage,Delivery,utcnow
from app.attachments import put_file
from app.finance import service as finance
from app.bank import service as bank
from app.evidence import service as evidence
from app.channels import service as channels
from app.channels.worker import delivery_once,media_once
from test_finance import actor,call,seed
from test_evidence import import_bank,case,image
from test_channels import configure,event


def another_file(db,who,account,reference='SYNTHETIC001',trusted=False):
    book=Workbook();sheet=book.active;sheet.title='Other observation'
    sheet.append(['Date','Credit','Debit','Description','Reference'])
    sheet.append(['2026-11-01',100000,0,'ALPHA SYNTHETIC',reference])
    out=BytesIO();book.save(out)
    attachment=put_file(db,who,out.getvalue(),'observation.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
    preview=call(db,who,bank.import_preview,account_id=account['id'],attachment_id=attachment.id)
    return call(db,who,bank.confirm_import,id=preview['id'],expected_version=1,account_version=2,sheets=['Other observation'],trusted_reference=trusted)


@pytest.mark.parametrize('posted',[False,True])
def test_f1_new_bank_ambiguity_freezes_existing_basis_without_erasing_cash(app,posted):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        c=case(db,who,wh,account);claim=call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)['claim']
        payment=None
        if posted:
            inv=call(db,who,finance.create_invoice,warehouse_id=wh,party_id=party,reference='SYN',amount_ngn='100000',uploaded_on='2026-11-01')
            payment=call(db,who,finance.create_payment,warehouse_id=wh,party_id=party,amount='100000',currency='NGN')
            call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='bank',case_id=c['id'],case_version=2,allocations=[{'invoice_id':inv['id'],'expected_version':1,'amount_ngn':'100000'}])
        another_file(db,who,account)
        current=db.get(BankClaim,claim['id']);db.refresh(current)
        assert current.state=='disputed'
        assert current.reviewed_entry_version==1 and current.reviewed_basis_hash
        assert db.get(EvidenceCase,c['id']).state=='disputed'
        from app.bank.archive import export_archive
        book=Workbook();sheet=book.active;sheet.append(['编号','日期','Reference','付款人','说明','金额','汇率','人民币汇率','换算','人民币']);sheet['F24']=None
        buf=BytesIO();book.save(buf);template=put_file(db,who,buf.getvalue(),'template.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','user',who.id)
        with pytest.raises(DomainError) as archived:
            call(db,who,export_archive,warehouse_id=wh,template_attachment_id=template.id,claims=[{'id':claim['id'],'expected_version':current.version}])
        assert archived.value.code=='bank_basis_changed'
        if posted:
            result=finance.get_payment(db,who,payment['id'])
            assert result['amount_ngn']=='100000.00' and result['allocated_ngn']=='100000.00' and result['bank_state']=='disputed'
            assert db.scalar(select(FinanceEvent.id).where(FinanceEvent.payment_id==payment['id'],FinanceEvent.kind=='evidence_disputed'))
        else:
            inv=call(db,who,finance.create_invoice,warehouse_id=wh,party_id=party,reference='SYN',amount_ngn='100000',uploaded_on='2026-11-01')
            payment=call(db,who,finance.create_payment,warehouse_id=wh,party_id=party,amount='100000',currency='NGN')
            with pytest.raises(DomainError) as posted_error:
                call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='bank',case_id=c['id'],case_version=db.get(EvidenceCase,c['id']).version,allocations=[{'invoice_id':inv['id'],'expected_version':1,'amount_ngn':'100000'}])
            assert posted_error.value.code=='claim_unavailable'
            assert db.get(Payment,payment['id']).state=='pending'
        restored=call(db,who,bank.review_case,id=c['id'],expected_version=db.get(EvidenceCase,c['id']).version,
            action='resolve_manual',entry_id=entries[0].id,entry_version=db.get(BankEntry,entries[0].id).version,reason='Reviewed original bank identity against both retained observations')
        assert restored['claim']['state']==('posted' if posted else 'verified_unallocated')
        assert restored['claim']['id']==claim['id']
        if posted:assert finance.get_payment(db,who,payment['id'])['allocated_ngn']=='100000.00'


def test_f1_known_trusted_observation_does_not_invalidate_owner(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']],trusted=True)
        c=case(db,who,wh,account);claim=call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)['claim']
        another_file(db,who,account,trusted=True)
        assert db.get(BankClaim,claim['id']).state=='verified_unallocated'
        assert len(list(db.scalars(select(BankEntry))))==1
        case(db,who,wh,account)  # a second proof is not new contradictory bank evidence
        assert db.get(BankClaim,claim['id']).state=='verified_unallocated'
        repeated=call(db,who,evidence.attach_images,id=c['id'],expected_version=2,images=[{'attachment_id':image(db,who).id,'source':'Another observation of the same original bytes'}])
        assert len(repeated['images'])==2 and db.get(BankClaim,claim['id']).state=='verified_unallocated'


@pytest.mark.parametrize('change',['operators','generation','instance','scope','groups','since'])
def test_f2_queued_send_rechecks_source_before_transport(app,monkeypatch,change):
    settings=replace(app.state.settings,collection_enabled=True,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);config=configure(monkeypatch,who,wh)
        raw,sig=event(config,wh);msg=channels.intake(db,settings,raw,sig,'sha512',config)
        delivery=call(db,who,channels.queue_delivery,message_id=msg['id'],session='assistant',recipient='synthetic-target',text='synthetic')
    changes={'operators':{},'generation':'new-generation','instance':'new-instance','scope':'sample','groups':{'synthetic-group':'different-warehouse'},'since':int(utcnow().timestamp())+10}
    changed=replace(config,**{change:changes[change]});calls=[]
    def transport(request):calls.append(request);return httpx.Response(200,json={'id':'should-never-send'})
    result=asyncio.run(delivery_once(app.state.session_factory,settings,changed,httpx.MockTransport(transport)))
    assert result['status']=='blocked' and not calls
    with app.state.session_factory() as db:assert db.get(Delivery,delivery['id']).state=='blocked'


def test_f3_context_does_not_cross_warehouse_or_generation(app,monkeypatch):
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);other=call(db,who,finance.create_warehouse,name='Other')['id'];config=configure(monkeypatch,who,wh)
        raw,sig=event(config,wh,source='current',body='current caption');accepted=channels.intake(db,settings,raw,sig,'sha512',config)
        for source,warehouse,generation in [('other warehouse',other,config.generation),('old generation',wh,'old')]:
            db.add(ChannelMessage(instance=config.instance,session=config.assistant_session,source_id=source,group_id='synthetic-group',sender_id='synthetic-operator',sent_at=utcnow(),warehouse_id=warehouse,scope='live',generation=generation,body='PRIVATE '+source,body_hash='0'*64,state='received'))
        from datetime import datetime,timezone,timedelta
        db.add(ChannelMessage(instance=config.instance,session=config.assistant_session,source_id='before capture start',group_id='synthetic-group',sender_id='synthetic-operator',sent_at=datetime.fromtimestamp(config.since,tz=timezone.utc)-timedelta(seconds=1),warehouse_id=wh,scope='live',generation=config.generation,body='PRIVATE before capture start',body_hash='0'*64,state='received'))
        db.flush();message=db.get(ChannelMessage,accepted['id'])
        context=evidence.context_for_message(db,message)
        assert context['related_messages']==[]


def test_f4_http_explicit_null_clears_date_and_matching(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']]);c=case(db,who,wh,account)
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'}).json()
        headers={'X-CSRF-Token':login['csrf_token'],'Idempotency-Key':'clear-date'}
        response=client.post('/api/evidence/'+c['id']+'/review',json={'expected_version':1,'fields':{'transaction_date':None}},headers=headers)
        assert response.status_code==200,response.text
        assert response.json()['transaction_date'] is None
        result=client.post('/api/bank/cases/'+c['id']+'/match',json={'expected_version':2},headers={**headers,'Idempotency-Key':'match-cleared'}).json()
        assert result['state']=='review' and result['reason']=='date_missing'
    with app.state.session_factory() as db:
        review=db.scalar(select(EvidenceReview).where(EvidenceReview.case_id==c['id']))
        assert review.fields=={'transaction_date':None}


def test_f5_media_existing_case_can_bind_account_and_claim(app,monkeypatch):
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;who=actor(db);wh,_=seed(db,who);config=configure(monkeypatch,who,wh)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']]);entry_id=entries[0].id
        raw,_=event(config,wh);body=json.loads(raw);body['payload']['hasMedia']=True;raw=json.dumps(body).encode();sig=hmac.new(config.webhook_key.encode(),raw,'sha512').hexdigest()
        channels.intake(db,settings,raw,sig,'sha512',config)
    buf=BytesIO();Image.new('RGB',(100,100)).save(buf,format='PNG')
    def transport(request):
        return httpx.Response(200,json={'id':'synthetic-message-1','media':{'url':'https://waha.invalid/api/files/test.png'}}) if '/messages/' in request.url.path else httpx.Response(200,content=buf.getvalue())
    saved=asyncio.run(media_once(app.state.session_factory,settings,who.id,config,httpx.MockTransport(transport)))
    with app.state.session_factory() as db,db.begin():
        cid=saved['case_id'];bound=call(db,who,evidence.bind_account,id=cid,expected_version=1,account_id=account['id'],reason='Explicit account selection')
        assert bound['id']==cid and len(bound['images'])==1
        call(db,who,evidence.review_case,id=cid,expected_version=2,fields={'amount':'100000','currency':'NGN','transaction_date':'2026-11-01','recipient_account':'0000001234','payer_name':'ALPHA SYNTHETIC','bank_name':'Access'})
        result=call(db,who,bank.review_case,id=cid,expected_version=3,entry_id=entry_id,entry_version=1)
        assert result['claim']['state']=='verified_unallocated'


def test_f6_one_party_account_payment_across_explicit_warehouses(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);a,party=seed(db,who);b=call(db,who,finance.create_warehouse,name='Second')['id']
        call(db,who,finance.set_party_membership,id=party,expected_version=1,warehouse_id=b,active=True,source='Same customer documented')
        account,entries=import_bank(db,who,a,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        call(db,who,bank.set_account_membership,id=account['id'],expected_version=2,warehouse_id=b,active=True,source='Shared receiving account authorization')
        c=case(db,who,b,account);call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)
        invoices=[call(db,who,finance.create_invoice,warehouse_id=wh,party_id=party,reference='SYN',amount_ngn='50000',uploaded_on='2026-11-01') for wh in (a,b)]
        payment=call(db,who,finance.create_payment,warehouse_id=a,party_id=party,amount='100000',currency='NGN')
        posted=call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='bank',case_id=c['id'],case_version=2,allocations=[{'invoice_id':inv['id'],'expected_version':1,'amount_ngn':'50000'} for inv in invoices])
        assert posted['allocated_ngn']=='100000.00' and posted['available_ngn']=='0.00'
        assert len(list(db.scalars(select(Payment))))==1 and len(list(db.scalars(select(BankClaim))))==1


def test_f7_evidence_cached_result_requires_current_read(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;admin=actor(db);wh,_=seed(db,admin);img=image(db,admin)
        who=Actor(admin.id,{'evidence.write','evidence.read'},{wh})
        payload={'warehouse_id':wh,'images':[{'attachment_id':img.id}],'idempotency_key':'replay-proof'}
        first=evidence.create_case(db,who,payload)
        assert evidence.create_case(db,who,payload)==first
        with pytest.raises(DomainError):evidence.create_case(db,Actor(who.id,{'evidence.write'},{wh}),payload)


@pytest.mark.parametrize('revocation',['target','operator'])
def test_f7_channel_cached_action_rechecks_secondary_target_and_operator(app,monkeypatch,revocation):
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;admin=actor(db);wh,party=seed(db,admin);config=configure(monkeypatch,admin,wh)
        raw,sig=event(config,wh);msg=channels.intake(db,settings,raw,sig,'sha512',config)
        pay=call(db,admin,finance.create_payment,warehouse_id=wh,party_id=party,amount='100',currency='NGN')
        who=Actor(admin.id,{'channels.act','finance.post'},{wh});payload={'message_id':msg['id'],'action':'payment.post','payload':{'id':pay['id'],'allocations':[]},'idempotency_key':'cached-action'}
        first=channels.prepare_action(db,who,payload);assert first['status']=='web_confirmation_required'
        if revocation=='operator':monkeypatch.setenv('MCSTAR_WAHA_OPERATORS','{}')
        else:who=Actor(admin.id,{'channels.act'},{wh})
        with pytest.raises(DomainError):channels.prepare_action(db,who,payload)


@pytest.mark.parametrize('change',['instance','generation','scope','groups'])
def test_f3_deferred_media_rejects_changed_provenance_without_fetch(app,monkeypatch,change):
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,_=seed(db,who);config=configure(monkeypatch,who,wh)
        raw,_=event(config,wh);payload=json.loads(raw);payload['payload']['hasMedia']=True;raw=json.dumps(payload).encode()
        channels.intake(db,settings,raw,hmac.new(config.webhook_key.encode(),raw,'sha512').hexdigest(),'sha512',config)
    updates={'instance':'different','generation':'different','scope':'sample','groups':{'synthetic-group':'other-warehouse'}}
    calls=[]
    def transport(request):calls.append(request);return httpx.Response(500)
    result=asyncio.run(media_once(app.state.session_factory,settings,who.id,replace(config,**{change:updates[change]}),httpx.MockTransport(transport)))
    assert result['status']=='blocked' and not calls


def test_f2_source_warehouse_grant_revoked_blocks_queued_delivery(app,monkeypatch):
    from app.models import User,Grant,WarehouseScope
    from sqlalchemy import delete
    settings=replace(app.state.settings,collection_enabled=True,sending_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;admin=actor(db);wh,_=seed(db,admin)
        user=db.scalar(select(User).where(User.username=='operator'));caps={'channels.send','channels.act'}
        db.add_all([Grant(user_id=user.id,capability=cap) for cap in caps]);db.add(WarehouseScope(user_id=user.id,warehouse_id=wh));db.flush()
        who=Actor(user.id,caps,{wh});config=configure(monkeypatch,who,wh)
        raw,sig=event(config,wh);msg=channels.intake(db,settings,raw,sig,'sha512',config)
        call(db,who,channels.queue_delivery,message_id=msg['id'],session='assistant',recipient='synthetic-target',text='synthetic')
        db.execute(delete(WarehouseScope).where(WarehouseScope.user_id==user.id))
    calls=[]
    def transport(request):calls.append(request);return httpx.Response(200,json={'id':'bad'})
    assert asyncio.run(delivery_once(app.state.session_factory,settings,config,httpx.MockTransport(transport)))['status']=='blocked'
    assert not calls


def test_f3_action_origin_generation_is_rechecked_against_new_confirmation(app,monkeypatch):
    settings=replace(app.state.settings,collection_enabled=True)
    with app.state.session_factory() as db,db.begin():
        who=actor(db);wh,party=seed(db,who);config=configure(monkeypatch,who,wh)
        raw,sig=event(config,wh);msg=channels.intake(db,settings,raw,sig,'sha512',config)
        invoice=call(db,who,finance.create_invoice,warehouse_id=wh,party_id=party,reference='SYN',amount_ngn='100',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,warehouse_id=wh,party_id=party,amount='100',currency='NGN')
        action=call(db,who,channels.prepare_action,message_id=msg['id'],action='payment.post',payload={'id':payment['id'],'expected_version':1,'approval':'manual','allocations':[{'invoice_id':invoice['id'],'expected_version':1,'amount_ngn':'100'}]})
        new=replace(config,generation='new-generation');monkeypatch.setenv('MCSTAR_WAHA_GENERATION',new.generation)
        raw,sig=event(new,wh,source='new-confirmation',body=action['confirmation_command']);confirmation=channels.intake(db,settings,raw,sig,'sha512',new)
        with pytest.raises(DomainError):call(db,who,channels.execute_action,id=action['id'],message_id=confirmation['id'],expected_version=1)
        assert db.get(Payment,payment['id']).state=='pending'


def test_f5_rebind_freezes_original_claim_and_preserves_case_history(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,_=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']]);c=case(db,who,wh,account)
        claim=call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)['claim']
        other=call(db,who,bank.create_account,warehouse_id=wh,bank_name='Access',account_number='0000009999',currency='NGN')
        result=call(db,who,evidence.bind_account,id=c['id'],expected_version=2,account_id=other['id'],reason='Correct original receiving account')
        assert result['state']=='disputed' and len(result['images'])==1 and len(result['reviews'])==2
        assert db.get(BankClaim,claim['id']).state=='disputed' and db.get(BankClaim,claim['id']).entry_id==entries[0].id
        with pytest.raises(DomainError):call(db,who,evidence.bind_account,id=c['id'],expected_version=2,account_id=account['id'],reason='stale')


def test_f6_membership_and_all_affected_scopes_required(app):
    with app.state.session_factory() as db,db.begin():
        who=actor(db);a,party=seed(db,who);b=call(db,who,finance.create_warehouse,name='Second')['id']
        with pytest.raises(DomainError):call(db,who,finance.create_invoice,warehouse_id=b,party_id=party,reference='SYN',amount_ngn='50',uploaded_on='2026-11-01')
        call(db,who,finance.set_party_membership,id=party,expected_version=1,warehouse_id=b,active=True,source='Confirmed second customer warehouse')
        invoice=call(db,who,finance.create_invoice,warehouse_id=b,party_id=party,reference='SYN',amount_ngn='50',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,warehouse_id=a,party_id=party,amount='100',currency='NGN')
        payload={'id':payment['id'],'expected_version':1,'approval':'manual','allocations':[{'invoice_id':invoice['id'],'expected_version':1,'amount_ngn':'50'}],'idempotency_key':'multiwarehouse'}
        restricted=Actor(who.id,{'finance.post','finance.read'},{a})
        with pytest.raises(DomainError):finance.post_payment(db,restricted,payload)
        posted=finance.post_payment(db,who,payload)
        assert posted['available_ngn']=='50.00' and posted['party_id']==party
        with pytest.raises(DomainError):finance.get_payment(db,restricted,payment['id'])
        with pytest.raises(DomainError):finance.post_payment(db,restricted,payload)
        assert finance.totals(db,restricted,a)['scope_limited']
        assert finance.post_payment(db,who,payload)==posted  # authorized stale-version replay remains valid


def test_f6_explicit_cross_party_authorization_preserves_payer_balance(app):
    from app.models import CrossPartyAuthorization
    with app.state.session_factory() as db,db.begin():
        who=actor(db);a,payer=seed(db,who);b=call(db,who,finance.create_warehouse,name='Beneficiary warehouse')['id']
        beneficiary=call(db,who,finance.create_party,warehouse_id=b,name='Distinct beneficiary')['id']
        inv=call(db,who,finance.create_invoice,warehouse_id=b,party_id=beneficiary,reference='BENEFICIARY',amount_ngn='80',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,warehouse_id=a,party_id=payer,amount='100',currency='NGN')
        ids={'payment':payment['id'],'invoice':inv['id'],'payer':payer,'beneficiary':beneficiary};actor_id=who.id
    base={'id':ids['payment'],'expected_version':1,'approval':'manual','allocations':[{'invoice_id':ids['invoice'],'expected_version':1,'amount_ngn':'80'}]}
    proof={'source':'Synthetic written payer instruction for named invoice and beneficiary','beneficiaries':[{'invoice_id':ids['invoice'],'party_id':ids['beneficiary']}]}
    for payload,capabilities in [({**base}, {'*'}),({**base,'cross_party':proof},{'finance.post'}),({**base,'cross_party':{**proof,'source':''}},{'*'}),({**base,'cross_party':{**proof,'beneficiaries':[{'invoice_id':ids['invoice'],'party_id':ids['payer']}]}},{'*'})]:
        with pytest.raises(DomainError):
            with app.state.session_factory() as db,db.begin():call(db,Actor(actor_id,capabilities,{a,b}),finance.post_payment,**payload)
    with app.state.session_factory() as db,db.begin():
        who=Actor(actor_id,{'*'},set());result=call(db,who,finance.post_payment,**base,cross_party=proof)
        assert result['party_id']==payer and result['amount_ngn']=='100.00' and result['available_ngn']=='20.00'
        permission=db.scalar(select(CrossPartyAuthorization).where(CrossPartyAuthorization.payment_id==ids['payment']))
        assert permission.beneficiary_party_id==beneficiary and permission.invoice_id==ids['invoice'] and permission.amount_ngn==80 and permission.source==proof['source']
        preview=finance.adjustment_preview(db,who,ids['invoice'],'50')
        call(db,who,finance.adjust_invoice,id=ids['invoice'],expected_version=2,amount_ngn='50',reason='Beneficiary invoice repriced',preview=preview)
        remaining=finance.get_payment(db,who,ids['payment'])
        assert remaining['party_id']==payer and remaining['available_ngn']=='50.00'


def test_f7_review_replay_denied_after_read_revocation_without_blocking_authorized_recovery(app):
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;admin=actor(db);wh,_=seed(db,admin)
        c=call(db,admin,evidence.create_case,warehouse_id=wh,images=[{'attachment_id':image(db,admin).id}])
        who=Actor(admin.id,{'evidence.review','evidence.read'},{wh});p={'id':c['id'],'expected_version':1,'fields':{'amount':'100'},'idempotency_key':'review-replay'}
        result=evidence.review_case(db,who,p);assert result['version']==2
        assert evidence.review_case(db,who,p)==result
        with pytest.raises(DomainError):evidence.review_case(db,Actor(who.id,{'evidence.review'},{wh}),p)


def test_f6_payable_party_period_retains_one_cash_record_across_memberships(app):
    with app.state.session_factory() as db,db.begin():
        who=actor(db);a,party=seed(db,who);b=call(db,who,finance.create_warehouse,name='Other supplier branch')['id']
        call(db,who,finance.set_party_membership,id=party,expected_version=1,warehouse_id=b,active=True,source='Confirmed supplier identity')
        for warehouse in (a,b):call(db,who,finance.create_invoice,warehouse_id=warehouse,party_id=party,kind='AP',period='2026-11',reference='PERIOD',amount_ngn='100',uploaded_on='2026-11-01')
        call(db,who,finance.pay_payable,warehouse_id=a,party_id=party,period='2026-11',amount='150',currency='NGN')
        period=finance.payable_period(db,who,a,party,'2026-11')
        assert period['billed_ngn']=='200.00' and period['paid_ngn']=='150.00' and period['balance_ngn']=='50.00'
        with pytest.raises(DomainError):finance.payable_period(db,Actor(who.id,{'payables.read'},{a}),a,party,'2026-11')


def test_review_migration_preserves_old_cash_and_freezes_unversioned_claim(app):
    from alembic import command
    from sqlalchemy import text
    from app.cli import migration_config
    from app.models import Allocation,PartyWarehouse,BankAccountWarehouse
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);wh,party=seed(db,who)
        account,entries=import_bank(db,who,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        c=case(db,who,wh,account)
        claim=call(db,who,bank.review_case,id=c['id'],expected_version=1,entry_id=entries[0].id,entry_version=1)['claim']
        invoice=call(db,who,finance.create_invoice,warehouse_id=wh,party_id=party,reference='OLD',amount_ngn='100000',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,warehouse_id=wh,party_id=party,amount='100000',currency='NGN')
        call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='bank',case_id=c['id'],case_version=2,allocations=[{'invoice_id':invoice['id'],'expected_version':1,'amount_ngn':'100000'}])
        # Represent a pre-review-schema claim, which never had this basis column.
        db.execute(text('UPDATE bank_claims SET reviewed_basis_hash=NULL'))
    config=migration_config();config.attributes['database_url']=app.state.settings.database_url
    command.downgrade(config,'b11000200001')
    command.upgrade(config,'head');command.check(config)
    with app.state.session_factory() as db:
        payment_row=db.get(Payment,payment['id']);claim_row=db.get(BankClaim,claim['id'])
        assert payment_row.state=='posted' and payment_row.amount_ngn==100000 and payment_row.bank_state=='disputed'
        assert claim_row.state=='disputed' and claim_row.entry_id==entries[0].id and claim_row.payment_id==payment['id']
        assert list(db.scalars(select(Allocation.amount_ngn).where(Allocation.payment_id==payment['id'])))==[100000]
        assert db.scalar(select(PartyWarehouse).where(PartyWarehouse.party_id==party)).warehouse_id==wh
        assert db.scalar(select(BankAccountWarehouse).where(BankAccountWarehouse.account_id==account['id'])).warehouse_id==wh
        assert db.scalar(select(FinanceEvent).where(FinanceEvent.payment_id==payment['id'],FinanceEvent.kind=='evidence_disputed'))


def test_f7_cross_warehouse_export_replay_and_adjustment_preview_recheck_related_cash(app):
    from app.finance.export import export_approved
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings;who=actor(db);a,party=seed(db,who);b=call(db,who,finance.create_warehouse,name='Other')['id']
        call(db,who,finance.set_party_membership,id=party,expected_version=1,warehouse_id=b,active=True,source='Explicit shared customer')
        inv=call(db,who,finance.create_invoice,warehouse_id=b,party_id=party,reference='SHARED',amount_ngn='100',uploaded_on='2026-11-01')
        payment=call(db,who,finance.create_payment,warehouse_id=a,party_id=party,amount='100',currency='NGN')
        call(db,who,finance.post_payment,id=payment['id'],expected_version=1,approval='manual',allocations=[{'invoice_id':inv['id'],'expected_version':1,'amount_ngn':'100'}])
        payload={'warehouse_id':a,'on':finance.totals(db,who,a)['day'],'idempotency_key':'cross-warehouse-export'}
        exported=export_approved(db,who,payload)
        assert exported['count']==1
        with pytest.raises(DomainError):export_approved(db,Actor(who.id,{'finance.export'},{a}),payload)
        with pytest.raises(DomainError):finance.adjustment_preview(db,Actor(who.id,{'finance.adjust'},{b}),inv['id'],'80')
        from app.models import Attachment
        from app.finance.authorization import export_file_access
        with pytest.raises(DomainError):export_file_access(db,Actor(who.id,{'finance.export'},{a}),db.get(Attachment,exported['id']))
        export_file_access(db,who,db.get(Attachment,exported['id']))
