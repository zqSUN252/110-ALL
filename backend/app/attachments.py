import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4
from app.common import DomainError, require
from app.models import Attachment


def put_file(db, actor, content, filename, media_type, owner_type, owner_id):
    require(actor, 'attachments.write')
    return store_authorized_file(db,actor,content,filename,media_type,owner_type,owner_id)


def store_authorized_file(db, actor, content, filename, media_type, owner_type, owner_id):
    """Private byte storage. Callers must first authorize their specific owner/purpose."""
    # Domain services add their own owner validation; HTTP currently accepts user ownership only.
    settings = db.info.get('settings')
    if settings is None:
        from app.settings import load_settings
        settings = load_settings()
    if len(content) > settings.max_attachment_bytes:
        raise DomainError('file_too_large', 'File exceeds configured limit', 413)
    digest = hashlib.sha256(content).hexdigest()
    relative = f'objects/{digest[:2]}/{digest}'
    root = settings.private_root.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise DomainError('invalid_object', 'Invalid private object path', 500)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / ('.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        if os.name != 'nt':
            temporary.chmod(0o600)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise DomainError('integrity_error', 'Temporary object checksum mismatch', 500)
        try:
            # Same-filesystem hard link creates the final name atomically and never
            # overwrites a winner. The temporary handle is closed before publication.
            os.link(temporary, target)
        except FileExistsError:
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise DomainError('integrity_error', 'Stored object checksum mismatch', 500)
    finally:
        # Only our uniquely named temporary object; never a published original.
        temporary.unlink(missing_ok=True)
    safe_name = Path(filename.replace('\\', '/')).name[:255] or 'attachment'
    row = Attachment(sha256=digest, object_path=relative, filename=safe_name,
        media_type=media_type[:120], size_bytes=len(content), created_by=actor.id,
        owner_type=owner_type, owner_id=owner_id)
    db.add(row)
    db.flush()
    return row


def read_file(settings, attachment):
    expected = f'objects/{attachment.sha256[:2]}/{attachment.sha256}'
    if not re.fullmatch('[0-9a-f]{64}', attachment.sha256) or attachment.object_path != expected:
        raise DomainError('invalid_object', 'Invalid private object reference', 500)
    root = settings.private_root.resolve()
    path = (root / expected).resolve()
    if not path.is_relative_to(root):
        raise DomainError('invalid_object', 'Invalid private object path', 500)
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        raise DomainError('missing_object', 'Original attachment unavailable', 404)
    if hashlib.sha256(content).hexdigest() != attachment.sha256:
        raise DomainError('integrity_error', 'Stored object checksum mismatch', 500)
    return content
