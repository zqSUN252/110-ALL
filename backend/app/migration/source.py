"""Exact-decimal MCSTAR source parser. Files and credentials never enter public output."""
import base64
import hashlib
import io
import json
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from zipfile import ZipFile
from PIL import Image
from app.common import json_value

KEYS = set('users partners bills shipments seq whBank fxRate oplog lang ri_wh ri_co userDeleted f6recs f6TelFix2 cr_wh f6cfg shipTrash payLedger opPend billTomb shipTomb arrReg partnerTomb'.split())
SECRETS = {'password','pass','pwd','passwordhash','password_hash','token','tokens','secret','apikey','api_key','session','sessions'}
KNOWN={
 'parties':set('id name phone tel address addr type contact note tels'.split()),
 'shipments':set('id no bl mode dest status carrierId clearDate clearingCoId clearingFee customerId grossWeight nodes note origin originalFile originalName pieces plDesc plMarks plTerm shipDate tickets upAt vessel volume'.split()),
 'tickets':set('receipt consignee contact desc pieces cbm weightKg freightUSD clearingNGN addr arrived arrivedDate dest mgr nodes notified onHoldRelease pkg remark supplier transport unitPrice'.split()),
 'bills':set('id no type partnerId shipmentNo ticketKeys ticketKey cur amount received discount status date dueDate slips apAuto clearingNGN creditInfo creditRelease discountNote groupMarks groupTels ngnReceived paysAP releaseIssued settleFx usdAmount usdRate item'.split()),
 'slips':set('amountNGN approved approvedAt approvedBy bank cur date file merged note payer uid amountOrig'.split()),
 'ledger':set('amountNGN amountOrig approved approvedAt approvedBy at bank batch billId billNo by cur date partner payer rcp shipNo sig uid'.split()),
 'f6':set('cbm created customer dest gross item mode no pieces sent status statusDate tel telManual upAt wh'.split())}


def digest(value):
    return hashlib.sha256(json.dumps(json_value(value),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def loads(value):
    return json.loads(value, parse_float=Decimal, parse_int=int, parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))


def scrub(value):
    if isinstance(value,dict):
        return {k:('[excluded credential]' if k.lower() in SECRETS else scrub(v)) for k,v in value.items()}
    if isinstance(value,list):return [scrub(v) for v in value]
    if isinstance(value,str) and value.startswith('data:'):return {'embedded_sha256':hashlib.sha256(value.encode()).hexdigest()}
    if isinstance(value,str) and value.lstrip().startswith(('{','[')):
        try:return scrub(loads(value))
        except (ValueError,TypeError):pass
    return json_value(value)


def epoch_ms(value):
    if isinstance(value,(int,Decimal)):
        return datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=int(Decimal(value)*1000))
    return None


def decimal(value):
    if value is None or value=='':return None
    result=Decimal(str(value))
    if not result.is_finite():raise ValueError('Nonfinite source money')
    return result


def parse_source(content):
    root=loads(content)
    if not isinstance(root,dict) or not isinstance(root.get('data'),dict):raise ValueError('Invalid snapshot root')
    data={}
    for key,value in root['data'].items():
        if isinstance(value,str):
            try:value=loads(value)
            except json.JSONDecodeError:
                # Scalars such as language/company text were stored as strings.
                if value.lstrip().startswith(('[','{')):raise ValueError('Malformed nested source JSON') from None
        data[key]=value
    entities={k:[] for k in ('parties','shipments','tickets','bills','slips','ledger','f6','archive')}
    issues=[]; maps=[]; files=[]
    def issue(pointer,code):issues.append({'pointer':pointer,'code':code})
    def fields(value,pointer,target):
        if pointer.rsplit('/',1)[-1].lower() in SECRETS:
            maps.append({'pointer':pointer,'target_type':target,'value_hash':digest('excluded'),'disposition':'excluded_credentials'});return
        maps.append({'pointer':pointer,'target_type':target,'value_hash':digest(scrub(value)),'disposition':'source_preserved'})
        if isinstance(value,dict):
            for key,child in value.items():fields(child,pointer+'/'+str(key).replace('~','~0').replace('/','~1'),target)
        elif isinstance(value,list):
            for i,child in enumerate(value):fields(child,pointer+'/'+str(i),target)
    def base(row,pointer,key):return {'source_key':str(key),'pointer':pointer,'raw':scrub(row)}
    def attachment(value,pointer):
        if not value:
            issue(pointer,'missing_attachment');return
        name=value.get('name','original.xlsx') if isinstance(value,dict) else 'original.jpg'
        uri=value.get('data') if isinstance(value,dict) else value
        try:
            header,encoded=uri.split(',',1)
            if not header.startswith('data:') or not header.endswith(';base64'):raise ValueError()
            body=base64.b64decode(encoded,validate=True)
            if header.startswith('data:image/jpeg'):
                with Image.open(io.BytesIO(body)) as image:
                    if image.format!='JPEG':raise ValueError()
                    image.verify()
                media='image/jpeg'
            else:
                with ZipFile(io.BytesIO(body)) as z:
                    if z.testzip() or '[Content_Types].xml' not in z.namelist() or 'xl/workbook.xml' not in z.namelist():raise ValueError()
                media='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            files.append({'pointer':pointer,'name':name,'media_type':media,'sha256':hashlib.sha256(body).hexdigest(),'bytes':body})
        except (ValueError,TypeError,AttributeError,KeyError,OSError):issue(pointer,'invalid_attachment')
    for key,value in data.items():
        short=key.removeprefix('logistics_'); pointer='/data/'+key
        fields(value,pointer,short)
        if short not in KEYS:issue(pointer,'unknown_root_key')
        if short=='partners':
            for i,r in enumerate(value):entities['parties'].append(base(r,f'{pointer}/{i}',r['id'])|{'name':r.get('name',''),'phone':r.get('phone') or r.get('tel'),'address':r.get('address') or r.get('addr'),'classification':r.get('type')})
        elif short=='shipments':
            for i,r in enumerate(value):
                p=f'{pointer}/{i}'
                entities['shipments'].append(base(r,p,r['id'])|{'source_no':r.get('no',''),'source_bl':r.get('bl'),'transport':str(r.get('mode','')).upper(),'destination':r.get('dest'),'source_status':r.get('status')})
                for j,t in enumerate(r.get('tickets',[])):
                    entities['tickets'].append(base(t,f'{p}/tickets/{j}',f'{r["id"]}:{j}')|{'shipment_key':str(r['id']),'position':j,'receipt':t.get('receipt'),'consignee':t.get('consignee'),'contact':t.get('contact'),'description':t.get('desc'),'destination':t.get('dest'),'pieces':t.get('pieces'),'kg':decimal(t.get('weightKg')),'cbm':decimal(t.get('cbm')),'freight_usd':decimal(t.get('freightUSD')),'clearing_ngn':decimal(t.get('clearingNGN'))})
                if r.get('originalFile'):attachment(r['originalFile'],p+'/originalFile')
        elif short=='bills':
            for i,r in enumerate(value):
                p=f'{pointer}/{i}'
                bill=base(r,p,r['id'])|{'kind':str(r.get('type','')).upper(),'reference':r.get('no',''),'currency':r.get('cur'),'amount':decimal(r['amount']),'received':decimal(r['received']),'discount':decimal(r.get('discount',0)),'source_status':r.get('status','unknown'),'source_party_ref':r.get('partnerId'),'source_shipment_ref':r.get('shipmentNo')}
                entities['bills'].append(bill)
                for f in ('amount','received','discount'):
                    if bill[f]!=bill[f].quantize(Decimal('.01')):issue(p+'/'+f,'subcent_control')
                for j,s in enumerate(r.get('slips',[])):
                    sp=f'{p}/slips/{j}'
                    entities['slips'].append(base(s,sp,f'{r["id"]}:{j}')|{'bill_key':str(r['id']),'kind':'slip','uid':s.get('uid'),'amount_ngn':decimal(s.get('amountNGN')),'original_currency':s.get('cur'),'original_amount':decimal(s.get('amountOrig')),'approved':s.get('approved'),'approved_at':epoch_ms(s.get('approvedAt')),'approved_by_text':s.get('approvedBy')})
                    attachment(s.get('file'),sp+'/file')
                    if not s.get('uid'):issue(sp,'payment_identity_unresolved')
                    if not s.get('cur'):issue(sp+'/cur','original_currency_unknown')
        elif short=='payLedger':
            for i,r in enumerate(value):entities['ledger'].append(base(r,f'{pointer}/{i}',str(i))|{'kind':'ledger','bill_key':r.get('billId'),'uid':r.get('uid'),'amount_ngn':decimal(r.get('amountNGN')),'original_currency':r.get('cur'),'original_amount':decimal(r.get('amountOrig')),'approved':None,'approved_at':None,'approved_by_text':r.get('approvedBy')})
        elif short=='f6recs':
            for i,r in enumerate(value):
                sent=r.get('sent') if isinstance(r.get('sent'),dict) else {}
                entities['f6'].append(base(r,f'{pointer}/{i}',str(i))|{'receipt':r.get('no'),'customer':r.get('customer'),'description':r.get('item'),'contact':r.get('tel'),'warehouse_text':r.get('wh'),'destination':r.get('dest'),'pieces':r.get('pieces'),'kg':decimal(r.get('gross')),'cbm':decimal(r.get('cbm')),'source_status':r.get('status'),'copied_at':epoch_ms(sent.get('at')),'copied_by_text':sent.get('by'),'state':'committed_external'})
        else:
            entities['archive'].append(base({'value':value},pointer,key)|{'kind':short,'state':'excluded_credentials' if short=='users' else 'nonexecutable_history'})
    active_files=len(files)
    for kind,allowed in KNOWN.items():
        for entity in entities[kind]:
            for field in set(entity['raw'])-allowed:issue(entity['pointer']+'/'+field,'unknown_field_preserved')
    for entity in entities['slips']:
        entity['approval_time_basis']='source_epoch_ms' if entity['approved_at'] is not None else None
    for entity in entities['ledger']:
        entity['approved']=entity['raw'].get('approved')
        entity['approved_at']=epoch_ms(entity['raw'].get('approvedAt'))
        entity['approval_time_basis']='source_recorded_unverified' if entity['approved_at'] is not None else None
    def historical_files(value,pointer):
        if isinstance(value,str) and value.lstrip().startswith(('{','[')):
            try:value=loads(value)
            except ValueError:return
        if isinstance(value,dict):
            for key,child in value.items():
                p=pointer+'/'+key
                if key in ('file','originalFile') and child:attachment(child,p)
                else:historical_files(child,p)
        elif isinstance(value,list):
            for i,child in enumerate(value):historical_files(child,pointer+'/'+str(i))
    for key,versions in root.get('hist',{}).items():
        for i,history in enumerate(versions):
            pointer=f'/hist/{key}/{i}'
            if key=='logistics_users':
                safe={'at':history.get('at'),'by':history.get('by'),'excluded_credentials':True}
            else:
                safe=scrub(history);historical_files(history,pointer)
            entities['archive'].append(base(safe,pointer,pointer)|{'kind':'snapshot_history','state':'nonexecutable_history'})
            fields(safe,pointer,'historical_archive')
    historical_files(data.get('logistics_shipTrash',[]),'/data/logistics_shipTrash')
    for key in ('version','tokens'):
        maps.append({'pointer':'/'+key,'target_type':key,'value_hash':digest(root.get(key)),'disposition':'excluded_credentials' if key=='tokens' else 'source_preserved'})
    controls={k:len(v) for k,v in entities.items()}
    controls|={'root_keys':len(data),'cash_created':0,'tokens_excluded':len(root.get('tokens',{})),'files':len(files),'active_files':active_files,'historical_files':len(files)-active_files,'unique_file_objects':len({f['sha256'] for f in files}),'missing_modules':[k for k in ('logistics_whRecs','logistics_mbls','logistics_custs') if k not in data]}
    return {'sha256':hashlib.sha256(content).hexdigest(),'source_version':str(root.get('version','unknown')),'entities':entities,'issues':issues,'maps':maps,'files':files,'controls':controls}
