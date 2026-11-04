"""One bounded local backup job. File IO never holds the financial guard."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from app.common import require
from app.models import Job,utcnow
from app.migration.backup import backup


def enqueue_backup(db,actor,key):
    require(actor,'*')
    row=db.scalar(select(Job).where(Job.idempotency_key=='migration-backup:'+key))
    if not row:
        row=Job(kind='migration_backup',payload={'actor_id':actor.id},idempotency_key='migration-backup:'+key)
        db.add(row);db.flush()
    return {'id':row.id,'state':row.status}


def run_once(factory,settings):
    from app.models import User,Grant
    with factory() as db,db.begin():
        row=db.scalar(select(Job).where(Job.kind.in_(['migration_backup','migration_apply']),Job.status=='running',Job.locked_at<utcnow()-timedelta(minutes=30)).with_for_update(skip_locked=True))
        if row:row.status='needs_review';row.error_code='interrupted_local_backup'
        job=db.scalar(select(Job).where(Job.kind.in_(['migration_backup','migration_apply']),Job.status=='pending').order_by(Job.created_at).with_for_update(skip_locked=True))
        if not job:return {'state':'idle'}
        user=db.get(User,job.payload['actor_id'])
        if not user or not user.active or not db.scalar(select(Grant.id).where(Grant.user_id==user.id,Grant.capability=='*')):
            job.status='blocked';return {'state':'blocked'}
        job.status='running';job.locked_at=utcnow();job.attempts+=1;identity=job.id;kind=job.kind;payload=dict(job.payload)
    # Separate transaction and no advisory financial guard during disk work.
    try:
        if kind=='migration_backup':result=backup(settings,settings.private_root/'backups'/identity)
        else:
            from app.migration.inputs import read_source
            from app.migration.service import plan_source,apply_source,upgrade_mapping
            from app.common import Actor,DomainError
            with factory() as db,db.begin():
                db.info['settings']=settings
                parsed,reference=read_source(db,payload['source_id'])
            plan=plan_source(parsed,payload['dataset'],'supplied_snapshot')
            if plan['plan_hash']!=payload['plan_hash']:raise DomainError('plan_changed','Review source plan again',409)
            with factory() as db,db.begin():
                db.info['settings']=settings
                user=db.get(User,payload['actor_id'])
                if not user or not user.active or not db.scalar(select(Grant.id).where(Grant.user_id==user.id,Grant.capability=='*')):raise DomainError('forbidden','Administrator authorization revoked',403)
                from app.migration.operations import writer_gate
                writer_gate(db)
                who=Actor(user.id,{'*'},set())
                if payload.get('mapping_upgrade_from'):
                    result=upgrade_mapping(db,who,parsed,plan,payload['mapping_upgrade_from'],payload.get('mapping_source'))
                else:result=apply_source(db,who,parsed,plan,{'archive_sha256':reference['sha256'],'private_source_id':reference['id']})
        state='complete';error=None
    except Exception:
        state='failed';error='migration_job_failed'
    with factory() as db,db.begin():
        job=db.get(Job,identity);job.status=state;job.error_code=error
        if state=='complete':job.payload=dict(job.payload,result={k:v for k,v in result.items() if k in ('batch_id','created','added_members','state','records_sha256','counts')})
    return {'id':identity,'state':state}
