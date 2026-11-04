"""Actual PostgreSQL/filesystem restore, with authorization state rendered inert."""
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text,select,func
from sqlalchemy.engine import make_url
from app.db import make_engine,session_factory
from app.models import User,Grant,Attachment,Session,Job,Outbox,MigrationBatch
from app.common import Actor
from app.attachments import put_file,read_file


def test_empty_restore_preserves_rows_bytes_but_not_executable_sessions(app,tmp_path):
    from app.migration.backup import backup,restore
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        user=db.scalar(select(User).where(User.username=='admin'))
        attachment=put_file(db,Actor(user.id,{'*'},set()),b'original fixture','fixture.txt','text/plain','user',user.id)
        aid=attachment.id
        db.add(Job(kind='recognition',payload={},idempotency_key='restore-test'))
        db.add(Outbox(topic='send',payload={'safe':'history'},idempotency_key='restore-test'))
        from test_documents import prepared
        from test_finance import call
        from test_evidence import import_bank,case
        from app.bank.service import review_case
        from app.dialogue import service as dialogue
        from app.connections.service import configure
        from app.models import Delivery,DialogueTurn,Invoice,Ticket
        actor,wh,document,ticket=prepared(db,app)
        account,_=import_bank(db,actor,wh,[['2026-11-01',100000,0,'ALPHA SYNTHETIC','SYNTHETIC001']])
        evidence=case(db,actor,wh,account)
        from app.bank.service import match_case
        matched=call(db,actor,match_case,id=evidence['id'],expected_version=1)
        # The existing deterministic bank service owns claim eligibility.
        entry=__import__('app.models',fromlist=['BankEntry']).BankEntry
        bank=db.scalar(select(entry))
        call(db,actor,review_case,id=evidence['id'],expected_version=1,action='confirm_manual',entry_id=bank.id,entry_version=bank.version,reason='explicit synthetic source')
        thread=call(db,actor,dialogue.create_thread,warehouse_id=wh)
        turn=call(db,actor,dialogue.submit_turn,id=thread['id'],expected_version=1,text='synthetic retained history')
        pending=Delivery(actor_id=actor.id,session='fixture',recipient='fixture',text='not a live message',state='pending');uncertain=Delivery(actor_id=actor.id,session='fixture',recipient='fixture',text='uncertain history',state='uncertain')
        db.add_all([pending,uncertain]);db.flush();pending_id,uncertain_id=pending.id,uncertain.id
        call(db,actor,configure,role='assistant',session='fixture',instance='fixture',generation='fixture',expected_version=0)
        invoice_id=ticket['invoice_id']
        # Cross the streaming restore batch boundary with real PostgreSQL rows.
        from app.models import Warehouse
        db.add_all(Warehouse(name='SYNTHETIC chunk '+str(i),source='Original 中文 source') for i in range(1201))
    bundle=tmp_path/'backup'
    manifest=backup(app.state.settings,bundle)
    schema='restore_'+uuid4().hex
    master=app.state.engine
    with master.begin() as db:db.execute(text(f'CREATE SCHEMA {schema}'))
    url=make_url(app.state.settings.database_url).update_query_dict({'options':f'-csearch_path={schema}'})
    target=replace(app.state.settings,database_url=url.render_as_string(hide_password=False),private_root=tmp_path/'restored')
    try:
        result=restore(target,bundle)
        assert result['verified'] is True
        engine=make_engine(target.database_url)
        with session_factory(engine)() as db:
            assert db.scalar(select(func.count()).select_from(Warehouse).where(Warehouse.name.like('SYNTHETIC chunk %')))==1201
            assert db.scalar(select(func.count()).select_from(User))==3
            assert db.scalar(select(func.count()).select_from(Grant))==5
            assert read_file(target,db.get(Attachment,aid))==b'original fixture'
            assert db.scalar(select(Job.status).where(Job.kind=='recognition'))=='paused'
            assert db.scalar(select(Outbox.status))=='paused'
            assert db.scalar(select(func.count()).select_from(Session))==0
            from app.models import BankClaim,DialogueThread,Connection,Delivery,Invoice
            assert db.scalar(select(func.count()).select_from(BankClaim))==1
            assert db.scalar(select(func.count()).select_from(DialogueThread))==1
            assert db.get(Delivery,pending_id).state=='paused'
            assert db.get(Delivery,uncertain_id).state=='uncertain'
            assert db.get(Invoice,invoice_id).amount_ngn==1500
            assert db.scalar(select(Connection.recovery_enabled)) is False
        engine.dispose()
        import pytest
        with pytest.raises(ValueError,match='nonempty'):restore(target,bundle)
    finally:
        with master.begin() as db:db.execute(text(f'DROP SCHEMA {schema} CASCADE'))


def test_incremental_rebuild_and_team_readback_are_real_files(app,tmp_path):
    from app.migration.backup import backup,read_bundle,archive_team
    from app.migration.operations import incremental,materialize,DirectoryArchive
    first=tmp_path/'full';second=tmp_path/'next';delta=tmp_path/'delta';rebuilt=tmp_path/'rebuilt'
    backup(app.state.settings,first)
    with app.state.session_factory() as db,db.begin():
        db.add(Job(kind='export',payload={'fixture':True},idempotency_key='new'))
    expected=backup(app.state.settings,second)
    assert incremental(first,second,delta)['changed']==1
    assert materialize(first,delta,rebuilt)['verified']
    actual,_=read_bundle(rebuilt)
    assert actual['records_sha256']==expected['records_sha256']
    assert archive_team(rebuilt)['state']=='not_archived'
    adapter=DirectoryArchive(tmp_path/'explicit-team-fixture')
    assert archive_team(rebuilt,adapter)['state']=='archived'
    class Corrupt(DirectoryArchive):
        def read(self,key):return b'corrupt readback'
    import pytest
    with pytest.raises(ValueError,match='readback'):archive_team(rebuilt,Corrupt(tmp_path/'corrupt-fixture'))


def test_writer_generation_blocks_old_runtime_and_backout_keeps_new_events(app):
    from app.migration.operations import cutover,backout_plan
    from app.common import run_command,DomainError
    from app.models import utcnow,AuditEvent
    import pytest
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=app.state.settings
        uid=db.scalar(select(User.id).where(User.username=='admin'));actor=Actor(uid,{'*'},set())
        start=utcnow()
        cutover(db,actor,0,'new-generation','frozen synthetic source')
        with pytest.raises(DomainError,match='writer'):
            run_command(db,actor,'fixture','key',{},lambda:{'executed':True})
        db.info['settings']=replace(app.state.settings,writer_generation='new-generation')
        assert run_command(db,actor,'fixture','key',{},lambda:{'executed':True})['executed']
        db.add(AuditEvent(actor_id=uid,action='payment.posted',entity_type='payment',entity_id='external-fact'))
        db.flush()
        assert backout_plan(db,start)['state']=='reconciliation_required'
