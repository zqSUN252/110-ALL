"""Separate private archive inputs; never normal attachments or backup payloads."""
import hashlib
from pathlib import Path,PurePosixPath
from uuid import uuid4
from zipfile import ZipFile,BadZipFile
from sqlalchemy import select
from app.common import DomainError
from app.models import AppSetting
from app.private_storage import protect_private_root

ARCHIVE_LIMIT=64*1024*1024
MEMBER_LIMIT=64*1024*1024


def inspect_archive(path):
    try:
        with ZipFile(path) as z:
            members=z.infolist()
            if len(members)>10000 or sum(m.file_size for m in members)>256*1024*1024:raise ValueError()
            if len({m.filename for m in members})!=len(members):raise ValueError()
            for m in members:
                p=PurePosixPath(m.filename.replace('\\','/'))
                if p.is_absolute() or '..' in p.parts or ':' in m.filename or m.flag_bits&1 or m.file_size>MEMBER_LIMIT or m.file_size>max(1024*1024,m.compress_size*500):raise ValueError()
            member=z.getinfo('mcstar_pkg/data/data.json')
            # Read only known members, never extract original code or credentials.
            body=z.read(member)
            prompts=z.read('mcstar_pkg/prompts/用户提示词全记录.md')
            if len(prompts)>16*1024*1024:raise ValueError()
    except (BadZipFile,KeyError,ValueError,RuntimeError):
        raise DomainError('archive_invalid','Expected bounded MCSTAR source archive',400) from None
    return body,prompts


async def receive_archive(request,db,actor):
    root=db.info['settings'].private_root/'source-inputs';protect_private_root(root)
    identity=uuid4().hex;path=root/(identity+'.zip');size=0;hasher=hashlib.sha256()
    try:
        with path.open('xb') as output:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>ARCHIVE_LIMIT:raise DomainError('source_too_large','Archive limit is 64 MiB',413)
                hasher.update(chunk);output.write(chunk)
        body,_=inspect_archive(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    metadata={'id':identity,'sha256':hasher.hexdigest(),'member_sha256':hashlib.sha256(body).hexdigest(),'size_bytes':size,'uploaded_by':actor.id,'state':'uploaded_unapplied'}
    db.add(AppSetting(key='migration.source.'+identity,value=metadata));db.flush()
    return metadata


def read_source(db,identity):
    import re
    if not re.fullmatch('[0-9a-f]{32}',identity):raise DomainError('not_found','Source input unavailable',404)
    row=db.scalar(select(AppSetting).where(AppSetting.key=='migration.source.'+identity))
    if not row:raise DomainError('not_found','Source input unavailable',404)
    path=db.info['settings'].private_root/'source-inputs'/(identity+'.zip')
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=row.value['sha256']:raise DomainError('source_changed','Original private input unavailable or changed',409)
    body,prompts=inspect_archive(path)
    from app.migration.source import parse_source
    from app.migration.configuration import extract_profiles
    try:
        parsed=parse_source(body);parsed['configuration']=extract_profiles(prompts)
    except (ValueError,TypeError,KeyError,UnicodeError):raise DomainError('source_invalid','Source requires structural review',400) from None
    parsed['controls']['provided_warehouse_profiles']=len(parsed['configuration']['warehouse_profiles'])
    parsed['controls']['public_contacts_present']=set(parsed['configuration']['public'])=={'public_hotline','public_openshipment'}
    return parsed,row.value
