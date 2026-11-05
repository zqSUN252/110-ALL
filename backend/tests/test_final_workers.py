"""Frozen/old runtime may neither claim nor publish local or model work."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select
from app.common import DomainError
from app.models import AppSetting, Job, TranslationCandidate, DialogueThread, DialogueTurn
from app.finance.service import record
from app.cargo import service as cargo, translation
from app.documents import exports
from app.warehouses import exports as warehouse_exports
from app.dialogue import files
from app.evidence.recognition import ModelConfig
from test_logistics import who, call, seed, receipt_payload


def queue(db, actor, wh, receipt, kind):
    if kind=='translation':
        c=TranslationCandidate(receipt_id=receipt['id'],source_text='合成货物',source_version=receipt['version'],manual_version=0,requested_by=actor.id)
        db.add(c);db.flush();return c
    if kind=='document':
        result=call(db,actor,exports.queue_export,receipt_ids=[receipt['id']],kind='packing',format='png')
        return db.get(Job,result['job_id'])
    if kind=='warehouse':
        result=call(db,actor,warehouse_exports.export_receipts,warehouse_id=wh,mode='all')
        return db.get(Job,result['id'])
    thread=DialogueThread(actor_id=actor.id,warehouse_id=wh,scope='live',title='Synthetic file')
    db.add(thread);db.flush()
    job=Job(kind='assistant.turn',payload={},idempotency_key='synthetic-turn')
    db.add(job);db.flush()
    turn=DialogueTurn(thread_id=thread.id,job_id=job.id,text='导出完整待核清单',result={},model_profile=None)
    db.add(turn);db.flush()
    result=files.queue_export(db,actor,thread,turn,SimpleNamespace(kind='pending'))
    return db.get(Job,result['job_id'])


def execute(kind,app,settings,calls):
    if kind=='translation':
        def transport(request):
            calls.append('model')
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"english":"Synthetic cargo"}'}}]})
        return asyncio.run(translation.translate_once(app.state.session_factory,settings,
            config=ModelConfig('multimodal',True,'https://synthetic.invalid/v1','synthetic-key-0123456789','synthetic-model'),transport=httpx.MockTransport(transport)))
    if kind=='document':return exports.export_once(settings,app.state.session_factory)
    if kind=='warehouse':return warehouse_exports.export_once(app.state.session_factory,settings)
    return files.file_once(app.state.session_factory,settings)


@pytest.mark.parametrize('kind',['translation','document','warehouse','dialogue'])
@pytest.mark.parametrize('gate',[{'generation':'test','state':'frozen'},{'generation':'new','state':'active'}])
def test_final_i07_worker_gate_prevents_claim_and_io(app,kind,gate):
    settings=replace(app.state.settings,writer_generation='test',model_calls_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        work=queue(db,actor,wh,r,kind);before=record(work);identity=work.id;model=type(work)
        db.add(AppSetting(key='migration.writer',value=gate))
    calls=[]
    with pytest.raises(DomainError) as rejected:execute(kind,app,settings,calls)
    assert rejected.value.code=='writer_frozen'
    assert calls==[]
    with app.state.session_factory() as db:assert record(db.get(model,identity))==before


def test_final_i07_restore_inerts_executable_translation_only():
    from app.migration.backup import inert
    for state in ('pending','running'):
        assert inert('translation_candidates',{'status':state})['status']=='paused'
    assert inert('translation_candidates',{'status':'accepted'})['status']=='accepted'


@pytest.mark.parametrize('kind',['translation','document','warehouse','dialogue'])
@pytest.mark.parametrize('failure',[False,True])
def test_final_i07_freeze_during_io_prevents_publication_and_error_write(app,kind,failure,monkeypatch):
    from app.models import Attachment
    settings=replace(app.state.settings,writer_generation='test',model_calls_enabled=True)
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        work=queue(db,actor,wh,r,kind);identity=work.id;model=type(work)
        if kind=='translation':work.source_text=r['item_zh']
    calls=[]
    def freeze():
        calls.append('io')
        with app.state.session_factory() as db,db.begin():
            db.add(AppSetting(key='migration.writer',value={'generation':'test','state':'frozen'}))
    def renderer(*args,**kwargs):
        freeze()
        if failure:raise ValueError('Synthetic rendering failure')
        return b'synthetic','image/png'
    if kind=='document':monkeypatch.setattr(exports,'render_artifact',renderer)
    elif kind=='warehouse':monkeypatch.setattr(warehouse_exports,'workbook',lambda *a,**kw: renderer(*a,**kw)[0])
    elif kind=='dialogue':
        monkeypatch.setattr(files,'pending_html',lambda *a,**kw: renderer(*a,**kw)[0])
    if kind=='translation':
        def transport(request):
            freeze()
            return httpx.Response(503 if failure else 200,json={'choices':[{'message':{'content':'{"english":"Synthetic"}'}}]})
        run=lambda:asyncio.run(translation.translate_once(app.state.session_factory,settings,config=ModelConfig('multimodal',True,'https://synthetic.invalid/v1','synthetic-key-0123456789','synthetic-model'),transport=httpx.MockTransport(transport)))
    else:run=lambda:execute(kind,app,settings,[])
    with pytest.raises((DomainError,ValueError)) as rejected:run()
    assert (isinstance(rejected.value,DomainError) and rejected.value.code=='writer_frozen') or (kind=='warehouse' and failure)
    assert calls==['io']
    with app.state.session_factory() as db:
        obj=db.get(model,identity);assert obj.status=='running' and obj.error_code is None
        assert not list(db.scalars(select(Attachment)))


def test_final_i07_warehouse_render_failure_is_recorded_not_left_running(app,monkeypatch):
    from app.models import Receipt
    settings=replace(app.state.settings,writer_generation='test')
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        work=queue(db,actor,wh,r,'warehouse');identity=work.id
    def renderer(*args,**kwargs):
        raise ValueError('Synthetic rendering failure')
    monkeypatch.setattr(warehouse_exports,'workbook',renderer)
    result=warehouse_exports.export_once(app.state.session_factory,settings)
    assert result=={'id':identity,'status':'failed','error_code':'render_failed'}
    with app.state.session_factory() as db:
        job=db.get(Job,identity);assert job.status=='failed' and job.error_code=='render_failed'


def test_final_i07_warehouse_source_change_blocks_publication(app,monkeypatch):
    from app.models import Attachment,Receipt
    settings=replace(app.state.settings,writer_generation='test')
    with app.state.session_factory() as db,db.begin():
        db.info['settings']=settings;actor=who(db);wh,party=seed(db,actor)
        r=call(db,actor,cargo.create_receipt,**receipt_payload(wh,party))
        work=queue(db,actor,wh,r,'warehouse');identity=work.id
    def renderer(*args,**kwargs):
        with app.state.session_factory() as db,db.begin():
            receipt=db.scalar(select(Receipt));receipt.reference=receipt.reference+'-changed';receipt.version+=1
        return b'synthetic-workbook'
    monkeypatch.setattr(warehouse_exports,'workbook',renderer)
    result=warehouse_exports.export_once(app.state.session_factory,settings)
    assert result['status']=='failed' and result['error_code']=='stale_export'
    with app.state.session_factory() as db:
        job=db.get(Job,identity);assert job.status=='failed'
        assert not list(db.scalars(select(Attachment)))
