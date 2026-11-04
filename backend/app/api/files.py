import hashlib
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, Response
from app.api.dependencies import Database, get_actor, mutate
from app.common import DomainError, require, audit, run_command
from app.models import Attachment
from app.attachments import put_file, read_file

router = APIRouter(prefix='/api/attachments')


@router.post('', status_code=201)
async def upload(request: Request, db: Database, file: UploadFile = File(...), owner_type: str = Form(...),
    owner_id: str = Form(...), actor=Depends(mutate)):
    require(actor, 'attachments.write')
    if owner_type != 'user' or owner_id != actor.id:
        raise DomainError('forbidden_owner', 'Upload must belong to the current user', 403)
    content = await file.read(request.app.state.settings.max_attachment_bytes + 1)
    if len(content) > request.app.state.settings.max_attachment_bytes:
        raise DomainError('file_too_large', 'File exceeds configured limit', 413)
    payload = {'sha256': hashlib.sha256(content).hexdigest(), 'filename': file.filename,
        'media_type': file.content_type, 'owner_type': owner_type, 'owner_id': owner_id}
    def apply():
        row = put_file(db, actor, content, file.filename or 'attachment', file.content_type or 'application/octet-stream', owner_type, owner_id)
        result = {'id': row.id, 'sha256': row.sha256, 'filename': row.filename, 'size_bytes': row.size_bytes, 'version': row.version}
        audit(db, actor, 'attachments.create', 'attachment', row.id, None, result)
        return result
    return run_command(db, actor, 'attachments.create', request.headers.get('Idempotency-Key'), payload, apply)


@router.get('/{attachment_id}')
def download(attachment_id: str, request: Request, db: Database, actor=Depends(get_actor)):
    require(actor, 'attachments.read')
    row = db.get(Attachment, attachment_id)
    if not row:
        raise DomainError('not_found', 'Attachment not found', 404)
    if '*' not in actor.capabilities and not (row.owner_type == 'user' and row.owner_id == actor.id):
        raise DomainError('forbidden', 'Attachment is outside your scope', 403)
    from urllib.parse import quote
    return Response(read_file(request.app.state.settings, row), media_type=row.media_type,
        headers={'Content-Disposition': "attachment; filename*=UTF-8''"+quote(row.filename,safe=''), 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'})
