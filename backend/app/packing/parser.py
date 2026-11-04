"""Bounded workbook extraction. Coordinates are observations, never transaction identity."""
from io import BytesIO
from datetime import date,datetime
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
import re,zipfile
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
import xlrd
from app.common import DomainError,json_value
from app.cargo.commands import port

ALIASES={
 'receipt':['收据号','工作号','单号','RECEIPTNO','RECEIPT','JOBNO','JOB'],
 'mark':['客户','收货人','唛头','MARK','CONSIGNEE','CUSTOMER','RECEIVER'],
 'item':['品名','货品名称','货名','DESCRIPTION','GOODS','COMMODITY'],
 'pieces':['包装件数','件数','PACKAGES','PACKAGE','QTY','PKGS','PCS'],
 'cbm':['合计体积','体积','CBM','VOLUMECBM','MEASCBM'],
 'kg':['毛重','重量','GROSSWEIGHT','GROSSKG','GROSS','WEIGHT','KGS','KG'],
 'contact_raw':['CONTACTNUMBER','CONTACTNO','CONTACT','客户电话','客户号码','电话','备忘','TEL','PHONE','MOBILE','WHATSAPP'],
 'destination':['目的港','目的地MBL','目的地','DESTINATION','DEST'],
 'storage_location':['库房名称','库房','STORAGELOCATION'],
 'salesperson':['业务员','SALESPERSON','SALES','SUPPLIER'],
 'operator_name':['录单人','操作员','OPERATOR'],
 'received_on':['收货日期','入库日期','日期','RECEIVEDDATE','DATE'],
 'address':['地址','ADDRESS'],
}


def norm(v):return re.sub(r'[\s_\-.()/]','',str(v or '')).upper()


def sheets(content):
    if len(content)>25*1024*1024:raise DomainError('file_size','Workbook exceeds 25 MB')
    if content[:4]==b'PK\x03\x04':
        with zipfile.ZipFile(BytesIO(content)) as z:
            if sum(x.file_size for x in z.infolist())>100*1024*1024 or len(z.infolist())>3000:raise DomainError('file_size','Expanded workbook too large')
        b=load_workbook(BytesIO(content),data_only=True,read_only=False)
        try:
            if len(b.worksheets)>200:raise DomainError('file_size','Too many sheets')
            for s in b:
                # Real supplied originals format through XFD. Formatting-only cells
                # are not cargo columns; inspect sparse populated cells before bounds.
                populated=[cell for cell in s._cells.values() if cell.value is not None]
                max_row=max((c.row for c in populated),default=1);max_col=max((c.column for c in populated),default=1)
                if max_row>30000 or max_col>100 or len(populated)>200000:raise DomainError('file_size','Actual sheet data exceeds row/column cap')
                rows=[[json_value(v) if isinstance(v,(date,datetime,Decimal)) else v for v in row] for row in s.iter_rows(max_row=max_row,max_col=max_col,values_only=True)]
                merged=[(m.min_row,m.max_row,m.min_col,m.max_col,str(m)) for m in s.merged_cells.ranges]
                yield s.title,rows,merged
        finally:b.close()
    elif content[:8]==bytes.fromhex('d0cf11e0a1b11ae1'):
        b=xlrd.open_workbook(file_contents=content,formatting_info=True)
        try:
            for s in b.sheets():
                if s.nrows>30000 or s.ncols>100:raise DomainError('file_size','Sheet exceeds cap')
                rows=[]
                for i in range(s.nrows):
                    rows.append([xlrd.xldate_as_datetime(c.value,b.datemode).isoformat()[:10] if c.ctype==xlrd.XL_CELL_DATE else c.value for c in s.row(i)])
                merged=[(a+1,b,c+1,d,f'{get_column_letter(c+1)}{a+1}:{get_column_letter(d)}{b}') for a,b,c,d in s.merged_cells]
                yield s.name,rows,merged
        finally:b.release_resources()
    else:raise DomainError('workbook_format','Only original XLS/XLSX workbooks are supported')


def header(row):
    keys=[norm(c) for c in row];mapping={}
    for field,aliases in ALIASES.items():
        for a in aliases:
            matches=[i for i,k in enumerate(keys) if k==norm(a) or (field=='kg' and k.startswith(norm(a)) and 'NET' not in k and '净' not in k)]
            if matches:mapping[field]={'column':matches[0]};break
    for i,k in enumerate(keys):
        if any(s in k for s in ('FREIGHT','FREIGHY','运费','PERCBMCHARGES','PERKGCHARGES')) and 'CLEAR' not in k:
            mapping['freight_usd']={'column':i,'basis':'CBM' if 'PERCBM' in k else 'KG' if 'PERKG' in k else 'total' if ('USD' in k or 'FREIGHT' in k or '运费' in k) else 'unknown'}
        if any(s in k for s in ('CLEARING','清关费')):
            mapping['clearing_ngn']={'column':i,'basis':'unknown' if 'PERCBM' in k and 'TOTAL' in k else 'CBM' if 'PERCBM' in k else 'total'}
    return mapping if 'receipt' in mapping and ('pieces' in mapping or 'item' in mapping) else None


def number(value,places):
    if value is None or str(value).strip()=='':return None
    try:
        n=Decimal(str(value).replace(',','').strip())
        if not n.is_finite() or n<0 or n>=Decimal('1e14'):raise InvalidOperation()
        rounded=n.quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP)
        if places==0 and n!=rounded:raise InvalidOperation()
        return int(n) if places==0 else rounded
    except (InvalidOperation,ValueError):raise DomainError('source_number','Source quantity/amount needs review') from None


def parse(content,overrides=None):
    output=[];mappings={};issues=[];overrides=overrides or {};position=0
    for name,rows,merged in sheets(content):
        mapping=None;ended=False;destination=port(name) if norm(name) in ('PH','P.H','PORTHARCOURT','ABA','ABUJA','ONITSHA','LAGOS') else None
        context_address=None;context_row=None
        for ri,raw in enumerate(rows,1):
            raw=[v if not isinstance(v,(date,datetime)) else v.isoformat() for v in raw]
            found=header(raw)
            if found:
                mapping=overrides.get(name,{}).get('fields',found);ended=False;mappings.setdefault(name,[]).append({'header_row':ri,'fields':mapping})
                output.append({'sheet':name,'row_number':ri,'position':position,'raw':raw,'provenance':{'header':True},'issues':[],'eligible':False});position+=1;continue
            if any(norm(v).rstrip(':：') in ('TOTAL','GRANDTOTAL','合计','小计') for v in raw):ended=True
            first=next((str(v).strip().upper() for v in raw if v is not None and str(v).strip()),'')
            if re.match(r'^(NOTES?\b|REMARKS?\s*:|AMOUNT\s+SHOWN|EXCHANGE\s+RATE)',first):ended=True
            addresses=[str(v).strip() for v in raw if v and 'WAREHOUSE' in str(v).upper() and 'ADDRESS' in str(v).upper()]
            if addresses:
                context_address=' '.join(str(v).strip() for v in raw if v is not None);context_row=ri
                match=re.search(r'\b(PORT\s+HARCOURT|P\.H|ONITSHA|ABUJA|ABA|LAGOS)\b',context_address,re.I)
                destination=port(match.group(1)) if match else None
            result={'sheet':name,'row_number':ri,'position':position,'raw':raw,'provenance':{},'issues':[],'eligible':False};position+=1
            if not mapping or ended or addresses:
                output.append(result);continue
            for field,definition in mapping.items():
                if field not in ALIASES and field not in ('freight_usd','clearing_ngn'):raise DomainError('mapping_field','Unsupported mapping field')
                ci=definition['column']
                if not isinstance(ci,int) or not 0<=ci<100:raise DomainError('mapping_column','Mapping column invalid')
                value=raw[ci] if ci<len(raw) else None;provenance={'cell':f'{get_column_letter(ci+1)}{ri}'}
                if value in (None,''):
                    for a,b,c,d,label in merged:
                        if a<=ri<=b and c<=ci+1<=d:
                            value=rows[a-1][c-1];provenance={'cell':f'{get_column_letter(c)}{a}','merged_range':label};break
                result[field]=str(value).strip() if value is not None and str(value).strip() else None
                result['provenance'][field]=provenance
            has_cargo=any(result.get(k) for k in ('pieces','cbm','kg'))
            if not has_cargo:output.append(result);continue
            if not result.get('receipt'):result['issues'].append('receipt_missing_no_identity_invented')
            if '\ufffd' in ''.join(str(v or '') for v in raw):result['issues'].append('encoding_replacement_character')
            for k,places in [('pieces',0),('kg',6),('cbm',6),('freight_usd',2),('clearing_ngn',2)]:
                try:result[k]=number(result.get(k),places)
                except DomainError:result[k]=None;result['issues'].append(k+'_invalid')
            for k in ('freight_usd','clearing_ngn'):
                if result.get(k) is not None:
                    basis=mapping[k].get('basis')
                    if basis not in ('total','KG','PCS','CBM'):result[k]=None;result['issues'].append(k+'_basis_unconfirmed')
                    elif basis!='total':
                        quantity=result.get({'KG':'kg','PCS':'pieces','CBM':'cbm'}[basis])
                        if quantity is None:result[k]=None;result['issues'].append(k+'_quantity_missing')
                        else:result[k]=(result[k]*quantity).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            result['destination']=port(result.get('destination')) or destination
            if context_address and not result.get('address'):result['address']=context_address
            if context_row:result['provenance']['section']={'row':context_row,'address':context_address}
            if not result['destination']:result['issues'].append('destination_unknown')
            if result.get('received_on'):
                try:result['received_on']=date.fromisoformat(result['received_on'][:10])
                except ValueError:result['received_on']=None;result['issues'].append('received_on_invalid')
            result['eligible']=True  # Missing identity remains a reviewable source row.
            output.append(result)
        if not mapping:issues.append({'sheet':name,'code':'header_not_found'})
    if not mappings:raise DomainError('headers_missing','No cargo header found')
    return output,mappings,issues
