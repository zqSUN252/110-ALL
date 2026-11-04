"""Real XLSX/PDF/PNG exports sharing the formal document content model."""
from io import BytesIO
from pathlib import Path
from decimal import Decimal
import re
import math
from functools import lru_cache
from PIL import Image,ImageDraw,ImageFont
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,Alignment,PatternFill
from openpyxl.drawing.image import Image as SheetImage
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib.units import mm
from pypdf import PdfReader
from app.common import DomainError
from .projection import shown,total_text

ASSETS=Path(__file__).parent/'assets'
MIME={'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','pdf':'application/pdf','png':'image/png'}


@lru_cache(maxsize=32)
def raster_font(path,size):return ImageFont.truetype(path,size)


def resources(settings):
    candidates={'regular':settings.document_font_regular or 'C:/Windows/Fonts/ebrima.ttf','bold':settings.document_font_bold or 'C:/Windows/Fonts/ebrimabd.ttf','chinese':settings.document_font_chinese or 'C:/Windows/Fonts/msyh.ttc','emoji':'C:/Windows/Fonts/seguiemj.ttf'}
    return {'fonts':{k:v if Path(v).is_file() else None for k,v in candidates.items()},'logo':str(ASSETS/'logo.png') if (ASSETS/'logo.png').is_file() else None,'logo_side':str(ASSETS/'logo_side.png') if (ASSETS/'logo_side.png').is_file() else None,'hero1':None,'hero2':None,'exact_source_layout_verified':False,'pending':['Original font-size unit and source screenshots','Original hero photographs','Physical print and real messenger paste']}


def _fonts(settings):
    resource=resources(settings)
    if not resource['logo'] or not all(resource['fonts'][k] for k in ('regular','bold','chinese')):raise DomainError('render_resources','Configure original logo and actual normal/bold/Chinese font paths',503)
    names={}
    import hashlib
    for kind,path in resource['fonts'].items():
        if not path:continue
        name='MC'+hashlib.sha256(path.encode()).hexdigest()[:12]
        if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,path,subfontIndex=0))
        names[kind]=(name,path)
    return names


def _font_for(char,fonts,bold=False):
    if char in ('\u200d','\ufe0f'):return fonts['regular']
    for kind in (('bold','chinese','emoji') if bold else ('regular','chinese','emoji')):
        if kind in fonts and ord(char) in pdfmetrics.getFont(fonts[kind][0]).face.charToGlyph:return fonts[kind]
    raise DomainError('missing_glyph','Configured fonts do not contain all document characters',503)


def unit_text(value):
    return f"{Decimal(value):,.6f}".rstrip('0').rstrip('.') if value is not None else 'TO BE CONFIRMED'


def formal_lines(data,kind='packing'):
    lines=[];seen_invoices=set()
    for r in data['rows']:
        label=kind.startswith('label_');fee=kind=='label_fee'
        if label:lines.extend(['CARGO RECEIPT','MC STAR LOGISTICS / 广州铭鑫思达'])
        else:lines.extend(['MC STAR LOGISTICS',{'SEA':'SEA CARGO','AIR':'AIR CARGO','EXPRESS':'EXPRESS','WAREHOUSE':'CARGO RECEIPT'}[r['transport']]])
        lines.extend([f"{'WAYBILL NO.' if r['transport'] in ('AIR','EXPRESS') else 'RECEIPT NO.'}: {shown(r['receipt'])}",f"Client Mark: {shown(r['mark'])}",f"Client Contact: {shown(r['phone'])}",f"Goods: {' / '.join(v for v in (r['item_zh'],r['item_en']) if v) or 'TO BE CONFIRMED'}",f"Packages: {shown(r['pieces'])} PCS",f"Weight: {shown(r['kg'])} KG",f"CBM: {shown(r['cbm'])}",f"Destination: {shown(r['destination'])}"])
        if not label:lines.extend([f"Container: {shown(r['container'])}",f"Warehouse Address: {shown(r['address'])}",f"Warehouse Contact: {shown(r['destination_contact'])}"])
        if fee or not label:
            for c in r['charges']:
                name='Freight' if c['component']=='freight' else 'Clearing'
                unit=unit_text(c['unit_price'])
                amount=f"{Decimal(c['amount']):,.2f}" if c['amount'] is not None else 'TO BE CONFIRMED'
                if label:lines.extend([f"{name}: {c['currency']} {amount}",f"Rate: {c['currency']} {unit} PER {c['unit']}"])
                else:lines.append(f"{name}: {c['currency']} {unit} PER {c['unit']} / Total {c['currency']} {amount}")
            usd=f"{Decimal(r['freight_usd']):,.2f}" if r['freight_usd'] is not None else 'TO BE CONFIRMED';ngn=f"{Decimal(r['clearing_ngn']):,.2f}" if r['clearing_ngn'] is not None else 'TO BE CONFIRMED'
            lines.append(f"Total cost: USD {usd} + NGN {ngn}")
            if not label:
                if r.get('invoice_id') in seen_invoices:
                    lines.append(f"Shared invoice: {shown(r.get('invoice_reference'))}; amounts shown once")
                else:
                    lines.extend([f"Exchange Rate: {shown(r['ngn_per_usd'])}",f"Invoice Total (NGN): {shown(r['invoice_total_ngn'])}",f"Received (NGN): {shown(r.get('received_total_ngn',r['allocated_ngn']))}",f"Outstanding (NGN): {shown(r['outstanding_ngn'])}"])
                    if r['rounding_adjustment_ngn'] is not None and Decimal(r['rounding_adjustment_ngn'])!=0:lines.append(f"Rounding adjustment (NGN): {r['rounding_adjustment_ngn']}")
                    if r.get('invoice_id'):seen_invoices.add(r['invoice_id'])
        lines.append(f"Received Date: {shown(r['received_on'])}")
        lines.append('')
    if not kind.startswith('label_'):
        lines.extend([f"TOTAL {key}: {total_text(value)}" for key,value in data['totals'].items()])
    return lines


def _runs(line,fonts,size,bold=False):
    colon=line.find(': ')
    for index,char in enumerate(line):
        name,path=_font_for(char,fonts,bold or (colon>=0 and index>colon))
        width=pdfmetrics.stringWidth(char,name,size)
        yield char,name,path,width


def _wrap(lines,fonts,size,width):
    result=[]
    for raw in lines:
        bold=bool(re.search(r'\*[^*]+\*',raw)) or raw in ('CARGO RECEIPT','MC STAR LOGISTICS','SEA CARGO','AIR CARGO','EXPRESS')
        line=re.sub(r'\*{1,2}([^*]+)\*{1,2}',r'\1',raw);part='';used=0
        for char,name,path,w in _runs(line,fonts,size,bold):
            if used+w>width and part:result.append((part,bold));part='';used=0
            part+=char;used+=w
        result.append((part,bold))
    return result


def _label_barcode(draw,receipt,x,y,width,height):
    if not receipt or not receipt.isascii():raise DomainError('barcode_source','Barcode needs an actual ASCII receipt identifier')
    barcode=Code128(receipt);barcode._calculate();pattern=barcode.decomposed
    units=sum(ord(c.lower())-96 for c in pattern);scale=width/(units+20);pos=x+10*scale
    for c in pattern:
        w=(ord(c.lower())-96)*scale
        if c.isupper():draw.rectangle((pos,y,pos+w,y+height),fill='black')
        pos+=w


def png(settings,lines,label=False,receipt=None):
    fonts=_fonts(settings);dpi=300 if label else 144;scale=dpi/72
    width=76*mm if label else 595.28;height=130*mm if label else None
    size=8.4 if label else 11;pad=8 if label else 28
    wrapped=_wrap(lines,fonts,size,width-2*pad)
    top=48 if label else 80;spacing=size*1.5
    needed=top+len(wrapped)*spacing+pad+(32 if label else 0)
    if label and needed>height:raise DomainError('label_overflow','Actual source text exceeds physical label; review content/layout',409)
    height=height or max(needed,300)
    if height*scale>30000:raise DomainError('image_too_tall','Use PDF/XLSX for this full export',409)
    im=Image.new('RGB',(round(width*scale),round(height*scale)),'white');draw=ImageDraw.Draw(im)
    with Image.open(ASSETS/'logo.png') as original:
        logo=original.convert('RGBA');logo.thumbnail((round((width-2*pad)*scale),round((top-10)*scale)))
        im.paste(logo,(round(pad*scale),round(5*scale)),logo)
    y=top
    for line,bold in wrapped:
        x=pad
        # Physical label barcode is above the original received date.
        if label and line.startswith('Received Date:'):
            _label_barcode(draw,receipt,pad*scale,y*scale,(width-2*pad)*scale,20*scale);y+=28
        for char,name,path,w in _runs(line,fonts,size,bold):
            font=raster_font(path,round(size*scale));draw.text((x*scale,(y+size)*scale),char,font=font,fill='black',anchor='ls');x+=w
        y+=spacing
    output=BytesIO();im.save(output,'PNG',dpi=(dpi,dpi));content=output.getvalue()
    with Image.open(BytesIO(content)) as check:check.verify()
    return content


def pdf(settings,lines,label=False,receipt=None):
    fonts=_fonts(settings);width,height=(76*mm,130*mm) if label else (595.28,841.89);pad=8 if label else 28;size=8.4 if label else 11
    wrapped=_wrap(lines,fonts,size,width-2*pad);top=48 if label else 80;spacing=size*1.5
    if label and top+len(wrapped)*spacing+pad+32>height:raise DomainError('label_overflow','Actual source text exceeds physical label',409)
    buffer=BytesIO();canvas=Canvas(buffer,pagesize=(width,height));canvas.setTitle('CARGO RECEIPT' if label else 'MC STAR PACKING LIST')
    def header():canvas.drawImage(str(ASSETS/'logo.png'),pad,height-top+5,width=min(150,width-2*pad),height=top-10,preserveAspectRatio=True,mask='auto')
    header();y=height-top
    for line,bold in wrapped:
        if y<pad+spacing:canvas.showPage();header();y=height-top
        if label and line.startswith('Received Date:'):
            barcode=Code128(receipt,barHeight=20);barcode._calculate();canvas.saveState();canvas.translate(pad,y-20);canvas.scale((width-2*pad)/barcode.width,1);barcode.drawOn(canvas,0,0);canvas.restoreState();y-=28
        x=pad;chunks=[]
        for char,name,path,w in _runs(line,fonts,size,bold):
            if chunks and chunks[-1][0]==name:chunks[-1][1]+=char;chunks[-1][2]+=w
            else:chunks.append([name,char,w])
        for name,chunk,w in chunks:canvas.setFont(name,size);canvas.drawString(x,y,chunk);x+=w
        y-=spacing
    canvas.save();content=buffer.getvalue()
    if not PdfReader(BytesIO(content)).pages:raise DomainError('pdf_verify','PDF readback failed',500)
    return content


HEADERS=[('receipt','Receipt No.'),('container','Container / Waybill'),('mark','Client Mark'),('phone','Client Contact'),('item_en','Goods EN'),('item_zh','Goods ZH'),('destination','Destination Port'),('warehouse','Destination Warehouse'),('address','Address'),('transport','Transport'),('pieces','PCS'),('kg','KG'),('cbm','CBM'),('freight_usd','USD charges'),('clearing_ngn','NGN charges'),('ngn_per_usd','Exchange Rate'),('invoice_total_ngn','Invoice Total NGN'),('rounding_adjustment_ngn','Rounding adjustment NGN'),('received_total_ngn','Received NGN'),('historical_received_ngn','Historical received NGN'),('historical_bank_state','Historical bank status'),('outstanding_ngn','Outstanding NGN'),('charges','Actual Unit Charges')]
NUMERIC={'pieces','kg','cbm','freight_usd','clearing_ngn','ngn_per_usd','invoice_total_ngn','rounding_adjustment_ngn','allocated_ngn','received_total_ngn','historical_received_ngn','outstanding_ngn'}
SHARED_MONEY={'invoice_total_ngn','rounding_adjustment_ngn','received_total_ngn','historical_received_ngn','outstanding_ngn'}
HEADERS.append(('invoice_reference','Shared invoice reference'))


def literal(cell,value):
    cell.value=value
    if isinstance(value,str):cell.data_type='s'  # Never interpret untrusted source values as formulas.


def workbook(data):
    book=Workbook();book.remove(book.active)
    def table(name,rows):
        from openpyxl.utils import get_column_letter
        sh=book.create_sheet(name);sh.append([label for _,label in HEADERS]);sh.freeze_panes='E2';sh.auto_filter.ref=f'A1:{get_column_letter(len(HEADERS))}{len(rows)+1}'
        seen=set()
        for i,r in enumerate(rows,2):
            for j,(key,label) in enumerate(HEADERS,1):
                v=r.get(key)
                if key in SHARED_MONEY and r.get('invoice_id') in seen:v=None
                if key=='charges':v='; '.join(f"{c['component']} {c['currency']} {unit_text(c['unit_price'])}/{c['unit']} / Total {c['currency']} {shown(c['amount'])}" for c in v)
                elif key in NUMERIC:v=Decimal(str(v)) if v is not None else None
                literal(sh.cell(i,j),v)
                sh.cell(i,j).alignment=Alignment(wrap_text=True,vertical='top')
                if key in NUMERIC:sh.cell(i,j).number_format='#,##0.00######'
            if r.get('invoice_id'):seen.add(r['invoice_id'])
        from openpyxl.utils import get_column_letter
        for j in range(1,len(HEADERS)+1):sh.column_dimensions[get_column_letter(j)].width=20
        for c in sh[1]:c.font=Font(name='Ebrima',bold=True);c.fill=PatternFill('solid',fgColor='E7EBEE')
        return sh
    table('Master',data['rows'])
    ports={}
    for r in data['rows']:ports.setdefault(r['destination'] or 'TO BE CONFIRMED',[]).append(r)
    for i,(port,rows) in enumerate(ports.items(),1):table((str(i)+' '+re.sub(r'[\[\]:*?/\\]','_',port))[:31],rows)
    # Per-customer sheets use exactly the same formal fields and order as PNG/PDF.
    groups={}
    for r in data['rows']:groups.setdefault(('invoice',r['invoice_id']) if r.get('invoice_id') else (r['shipment_id'],r['party_id'],r['phone'],r['mark']),[]).append(r)
    for i,rows in enumerate(groups.values(),1):
        sh=book.create_sheet('Customer '+str(i));sh.column_dimensions['A'].width=110
        logo=SheetImage(ASSETS/'logo.png');logo.width=180;logo.height=55;sh.add_image(logo,'A1');sh.row_dimensions[1].height=45
        subset=dict(data,rows=rows,totals={})
        for index,line in enumerate(formal_lines(subset),2):literal(sh.cell(index,1),line);sh.cell(index,1).font=Font(name='Ebrima',size=11,bold=index in (2,3));sh.cell(index,1).alignment=Alignment(wrap_text=True)
    totals=book.create_sheet('Totals');totals.append(['Field','Complete total','Known subtotal','Pending source count'])
    for key,value in data['totals'].items():totals.append([{'freight_usd':'USD charges','clearing_ngn':'NGN charges'}.get(key,key),Decimal(value['value']) if value['value'] is not None else None,Decimal(value['known_subtotal']),value['pending_count']])
    output=BytesIO();book.save(output);content=output.getvalue();check=load_workbook(BytesIO(content),data_only=False)
    if check['Master'].max_row!=len(data['rows'])+1:raise DomainError('xlsx_verify','Workbook row readback mismatch',500)
    seen=set()
    for i,r in enumerate(data['rows'],2):
        for j,(key,_) in enumerate(HEADERS,1):
            c=check['Master'].cell(i,j)
            if key in SHARED_MONEY and r.get('invoice_id') in seen:
                if c.value is not None:raise DomainError('xlsx_verify','Shared bill repeated in member row',500)
                continue
            if key in NUMERIC and r.get(key) is not None and abs(Decimal(str(c.value))-Decimal(str(r[key])))>Decimal('.000001'):raise DomainError('xlsx_verify','Numeric amount readback mismatch',500)
            if key not in NUMERIC and c.data_type=='f':raise DomainError('xlsx_verify','Source text became a formula',500)
        if r.get('invoice_id'):seen.add(r['invoice_id'])
    check.close();return content


def render_artifact(settings,data,fmt,kind):
    if fmt not in MIME:raise DomainError('format','Choose xlsx, pdf or png')
    label=kind.startswith('label_')
    if label and len(data['rows'])!=1:raise DomainError('label_members','A physical label belongs to one source receipt')
    if label and fmt=='xlsx':raise DomainError('label_format','Labels support explicit PDF printing or PNG clipboard')
    if fmt=='xlsx':return workbook(data),MIME[fmt]
    lines=formal_lines(data,kind);receipt=data['rows'][0]['receipt'] if label else None
    return (png(settings,lines,label,receipt) if fmt=='png' else pdf(settings,lines,label,receipt)),MIME[fmt]
