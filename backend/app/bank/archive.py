"""Original NGN template archive; private files and verified readback, no delivery side effect."""
from pathlib import Path
from tempfile import TemporaryDirectory
from app.common import DomainError, require, check_version
from app.models import BankClaim, BankEntry, BankAccount, EvidenceCase, Payment
from app.attachments import put_file, read_file
from app.finance.service import command, locked, record
from .service import attachment_for_actor
from .archive_writer import write_archive, inspect_template, MIME


@command('bank.archive','warehouse_id template_attachment_id claims label')
def export_archive(db,actor,p):
    require(actor,'bank.export',p['warehouse_id'])
    template=attachment_for_actor(db,actor,p['template_attachment_id'])
    original=read_file(db.info['settings'],template)
    try:inspect_template(original)
    except (ValueError,KeyError):raise DomainError('invalid_template','Original NGN archive template is invalid') from None
    if not isinstance(p['claims'],list) or not 1<=len(p['claims'])<=5000: raise DomainError('invalid_selection','Select 1 to 5000 claims')
    records=[]; modes=set(); ids=set()
    for selection in p['claims']:
        claim=locked(db,BankClaim,selection['id']); check_version(claim,selection.get('expected_version'))
        entry=locked(db,BankEntry,claim.entry_id); account=locked(db,BankAccount,entry.account_id); case=locked(db,EvidenceCase,claim.case_id)
        if account.warehouse_id!=p['warehouse_id']: raise DomainError('forbidden','Bank archive warehouse differs',403)
        if claim.id in ids:raise DomainError('duplicate_claim','Claim appears twice')
        ids.add(claim.id); modes.add(account.scope)
        if claim.state not in ('verified_unallocated','posted') or entry.currency!='NGN' or entry.transaction_date is None:
            raise DomainError('archive_ineligible','Only confirmed dated NGN bank credits can use this template')
        # Stable entry identity avoids content-based dedup swallowing same-amount transactions.
        payment=db.get(Payment,claim.payment_id) if claim.payment_id else None
        records.append({'code':'BANK-'+entry.id,'currency':entry.currency,'date':entry.transaction_date.isoformat(),'amount':str(entry.credit),
            'business_reference':case.business_reference,'description':entry.description,
            'fx_ngn_usd':str(payment.ngn_per_usd) if payment and payment.ngn_per_usd else None,
            'fx_cny_usd':str(payment.cny_per_usd) if payment and payment.cny_per_usd else None})
    if len(modes)>1:raise DomainError('scope_mismatch','Sample and live archives must be separate')
    with TemporaryDirectory(dir=db.info['settings'].private_root) as folder:
        source=Path(folder)/'template.xlsx'; output=Path(folder)/'archive.xlsx'; source.write_bytes(original)
        try: report=write_archive(source,records,output,p.get('label',''))
        except ValueError:raise DomainError('archive_validation_failed','Archive template/typed readback validation failed') from None
        attachment=put_file(db,actor,output.read_bytes(),'bank-archive.xlsx',MIME,'bank_archive',p['warehouse_id'])
    return {'id':attachment.id,'file':record(attachment),'template_sha256':template.sha256,'scope':next(iter(modes)),**report,'sent':False}
