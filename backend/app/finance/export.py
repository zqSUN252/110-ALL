from io import BytesIO
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from openpyxl import Workbook,load_workbook
from sqlalchemy import select
from app.models import Payment,Party,Invoice,Allocation
from app.common import DomainError,require
from app.attachments import put_file
from .service import command,day,scope,record
from .access import visible_payments


@command('finance.export','warehouse_id scope on')
def export_approved(db,actor,p):
    require(actor,'finance.export',p['warehouse_id']);mode=scope(p.get('scope','live'));selected=day(p['on'])
    zone=ZoneInfo(db.info['settings'].business_timezone);start=datetime.combine(selected,datetime.min.time(),zone);end=start+timedelta(days=1)
    query=select(Payment).where(Payment.warehouse_id==p['warehouse_id'],Payment.scope==mode,Payment.direction=='in',Payment.state=='posted',Payment.approved_at>=start,Payment.approved_at<end)
    from sqlalchemy import func
    expected_count=db.scalar(select(func.count()).select_from(query.subquery()))
    payments=list(db.scalars(visible_payments(query,actor).order_by(Payment.approved_at,Payment.id)))
    if len(payments)!=expected_count:raise DomainError('export_scope_incomplete','Some merged payments involve warehouses outside current authorization; use an authorized full-scope exporter',403)
    if len(payments)>5000:raise DomainError('export_size','Split the export into smaller daily scopes')
    book=Workbook();sheet=book.active;sheet.title='Approved payments'
    sheet.append(['Payment ID','Customer','Receipt references','Approval date','Payment date','Original currency','Original amount','NGN amount','Approval method','Bank evidence'])
    amount=Decimal('0.00');invoice_rows={}
    for payment in payments:
        invoices=list(db.scalars(select(Invoice).join(Allocation,Allocation.invoice_id==Invoice.id).where(Allocation.payment_id==payment.id).distinct().order_by(Invoice.id)))
        for invoice in invoices:
            require(actor,'finance.read',invoice.warehouse_id);invoice_rows[invoice.id]=invoice
        sheet.append([payment.id,db.get(Party,payment.party_id).name,' / '.join(i.reference for i in invoices),payment.approved_at.astimezone(zone).replace(tzinfo=None),payment.paid_on,payment.currency,payment.amount,payment.amount_ngn,payment.approval,payment.bank_state])
        amount+=payment.amount_ngn
    # Explicit text cells avoid spreadsheet formula injection from original names/references.
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
        row[3].number_format='yyyy-mm-dd hh:mm:ss';row[4].number_format='yyyy-mm-dd'
        row[6].number_format=row[7].number_format='#,##0.00'
    sheet.freeze_panes='D2';sheet.auto_filter.ref=sheet.dimensions
    components=book.create_sheet('Invoice components')
    components.append(['Invoice ID','Reference','USD charge','NGN charge','NGN per USD','Rounding adjustment NGN','Invoice NGN','Version'])
    for invoice in invoice_rows.values():
        components.append([invoice.id,invoice.reference,invoice.charge_usd,invoice.charge_ngn,invoice.ngn_per_usd,invoice.rounding_adjustment_ngn,invoice.amount_ngn,invoice.version])
    for row in components.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value,str):cell.data_type='s'
        for index in (2,3,5,6):row[index].number_format='#,##0.00'
        row[4].number_format='0.000000'
    buf=BytesIO();book.save(buf);content=buf.getvalue()
    checked=load_workbook(BytesIO(content),read_only=True,data_only=True)
    try:
        rows=list(checked.active.iter_rows(min_row=2,values_only=True))
        if len(rows)!=len(payments) or sum((Decimal(str(r[7])) for r in rows),Decimal('0'))!=amount:
            raise DomainError('export_validation_failed','Payment export typed readback differs')
        components_read=list(checked['Invoice components'].iter_rows(min_row=2,values_only=True))
        if len(components_read)!=len(invoice_rows) or any(Decimal(str(row[5]))!=invoice_rows[row[0]].rounding_adjustment_ngn for row in components_read):raise DomainError('export_validation_failed','Invoice rounding readback differs')
    finally:checked.close()
    attachment=put_file(db,actor,content,'approved-payments.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','finance_export',p['warehouse_id'])
    return {'id':attachment.id,'file':record(attachment),'source_payment_ids':[p.id for p in payments],'source_invoice_ids':list(invoice_rows),'count':len(payments),'amount_ngn':str(amount),'day':selected.isoformat(),'scope':mode,'round_trip_verified':True}
