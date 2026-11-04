"""Real localhost HTTP end-to-end acceptance with an isolated synthetic runtime only."""
import argparse,json,sys,time
from pathlib import Path
from uuid import uuid4
from io import BytesIO
import httpx
from openpyxl import Workbook
from PIL import Image


def run(base,client=None,output=None):
    client=client or httpx.Client(base_url=base,timeout=40)
    login=client.post('/api/auth/login',json={'username':'admin','password':'synthetic-password-123'})
    assert login.status_code==200,'synthetic login failed'
    user=login.json()['user']['id'];csrf=login.json()['csrf_token'];prefix='SYN-'+uuid4().hex[:10]
    counts={'commands':0,'negative_checks':0}
    def call(path,body=None,method='POST',status=200,key=None):
        r=client.request(method,path,json=body,headers={'X-CSRF-Token':csrf,'Idempotency-Key':key or uuid4().hex})
        assert r.status_code==status,f'{method} {path.split("?")[0]}: {r.status_code}, {r.json().get("error",{}).get("code")}'
        counts['negative_checks' if status>=400 else 'commands']+=1
        return r.json()
    def get(path):return call(path,method='GET')
    def image(n):
        data=BytesIO();Image.new('RGB',(120,80),(n,100,160)).save(data,'PNG');return data.getvalue()
    def upload(data,name,media):
        r=client.post('/api/attachments',files={'file':(name,data,media)},data={'owner_type':'user','owner_id':user},headers={'X-CSRF-Token':csrf,'Idempotency-Key':uuid4().hex})
        assert r.status_code==201,'synthetic upload failed';return r.json()['id']
    wh=call('/api/warehouses',{'name':prefix+' warehouse','address':'Synthetic address','contact':'Synthetic contact'})
    party=call('/api/parties',{'warehouse_id':wh['id'],'name':prefix+' party','phone':'+2347000000000','phone_source':'Synthetic ownership evidence'})
    receipt=call('/api/receipts',{'warehouse_id':wh['id'],'series':prefix,'number':1,'received_on':'2026-11-01','transport':'AIR','party_id':party['id'],'customer_phone':'+2347000000000','item_zh':'合成货物','item_en':'Synthetic cargo','destination':'LAGOS','destination_warehouse_id':wh['id'],'origin':'GZ','specs':[{'length':'100','width':'100','height':'100','unit':'CM','pieces':3,'kg':'3'}]})
    receipt=call('/api/receipts/'+receipt['id']+'/review',{'expected_version':receipt['version'],'decision':'approved','comment':'Synthetic supervisor review'})
    shipment=call('/api/shipments',{'warehouse_id':wh['id'],'reference':prefix+' flight','transport':'AIR','origin':'GZ','destination':'LAGOS'})
    call('/api/cargo/dispatch',{'shipment_id':shipment['id'],'occurred_on':'2026-11-02','rows':[{'receipt_id':receipt['id'],'expected_version':receipt['version'],'pieces':1,'kg':'1','cbm':'1'}]})
    doc=call('/api/packing/documents',{'shipment_id':shipment['id'],'reference':prefix+' document','uploaded_on':'2026-11-01'})
    ticket=call('/api/tickets',{'document_id':doc['id'],'receipt':receipt['reference'],'native_receipt_id':receipt['id'],'party_id':party['id'],'transport':'AIR','origin':'GZ','pieces':3,'kg':'3','cbm':'3','contact_raw':'+2347000000000','phone_normalized':'+2347000000000','phone_confirmed':True,'mark':prefix,'item_en':'Synthetic cargo','destination':'LAGOS','destination_warehouse_id':wh['id'],'address':'Synthetic address','freight_usd':'2','clearing_ngn':'0','ngn_per_usd':'1500','charge_components':[{'component':'freight','currency':'USD','unit':'KG','amount':'2','source':'Synthetic reviewed bill'},{'component':'clearing','currency':'NGN','unit':'KG','amount':'0','source':'Explicit all in'}]})
    assert ticket['invoice_id'] is None
    ticket=call('/api/tickets/'+ticket['id']+'/arrive',{'expected_version':ticket['version'],'pieces':1,'kg':'1','cbm':'1','occurred_on':'2026-11-03','source':'Synthetic port arrival','mode':'air'})
    invoice=get('/api/invoices/'+ticket['invoice_id']);assert invoice['amount_ngn']=='1000.00'
    call('/api/release-orders',{'members':[{'ticket_id':ticket['id'],'expected_version':ticket['version']}],'mode':'credit','promise_on':'2026-12-01','guarantor':'Synthetic guarantor'},status=409)
    call('/api/tracking/nodes',{'target_type':'ticket','target_id':ticket['id'],'expected_version':ticket['version'],'node':'warehouse_arrived','destination_warehouse_id':wh['id'],'mode':'manual','occurred_at':'2026-11-04T08:00:00+08:00','source':'Synthetic actual destination receipt'})
    ticket=get('/api/tickets/'+ticket['id'])
    account=call('/api/bank/accounts',{'warehouse_id':wh['id'],'bank_name':'Access','account_number':prefix,'currency':'NGN'})
    book=Workbook();sheet=book.active;sheet.title='Access';sheet.append(['Date','Credit','Debit','Description','Reference']);sheet.append(['2026-11-01',1000,0,'SYNTHETIC PAYER',prefix]);buf=BytesIO();book.save(buf)
    original=upload(buf.getvalue(),'synthetic-bank.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    bankfile=call('/api/bank/imports/preview',{'account_id':account['id'],'attachment_id':original})
    call('/api/bank/imports/'+bankfile['id']+'/confirm',{'expected_version':bankfile['version'],'account_version':account['version'],'sheets':['Access'],'date_order':'dmy'})
    entry=get('/api/workspace/bank-entries?account_id='+account['id'])['items'][0]
    images=[{'attachment_id':upload(image(n),'synthetic-'+str(n)+'.png','image/png'),'source':'Synthetic original evidence'} for n in range(3)]
    case=call('/api/evidence',{'warehouse_id':wh['id'],'account_id':account['id'],'fields':{'amount':'1000','currency':'NGN','transaction_date':'2026-11-01','payer_name':'SYNTHETIC PAYER','recipient_account':prefix,'bank_name':'Access','transaction_reference':prefix},'images':images})
    assert len(case['images'])==3
    match=call('/api/bank/cases/'+case['id']+'/match',{'expected_version':case['version']});assert match['state']=='matched'
    review=call('/api/bank/cases/'+case['id']+'/review',{'expected_version':case['version'],'entry_id':entry['id'],'entry_version':entry['version']});assert review['claim']['state']=='verified_unallocated' and not review['payment_posted']
    case=get('/api/evidence/'+case['id'])
    payment=call('/api/payments',{'party_id':party['id'],'warehouse_id':wh['id'],'amount':'1000','currency':'NGN','paid_on':'2026-11-01'})
    post={'expected_version':payment['version'],'approval':'bank','case_id':case['id'],'case_version':case['version'],'allocations':[{'invoice_id':invoice['id'],'expected_version':invoice['version'],'amount_ngn':'1000'}]}
    key=uuid4().hex;posted=call('/api/payments/'+payment['id']+'/post',post,key=key);assert call('/api/payments/'+payment['id']+'/post',post,key=key)==posted
    assert get('/api/invoices/'+invoice['id'])['outstanding_ngn']=='0.00'
    ticket=get('/api/tickets/'+ticket['id']);order=call('/api/release-orders',{'members':[{'ticket_id':ticket['id'],'expected_version':ticket['version']}],'mode':'paid'})
    notification=call('/api/notifications/render',{'kind':'release','order_id':order['id'],'ticket_ids':[ticket['id']]})
    png=client.get('/api/notifications/'+notification['id']+'/image');assert png.status_code==200 and png.content.startswith(b'\x89PNG')
    photo=upload(image(20),'synthetic-pickup.png','image/png')
    picked=call('/api/physical-releases',{'order_id':order['id'],'expected_version':order['version'],'ticket_id':ticket['id'],'pieces':1,'kg':'1','cbm':'1','occurred_at':'2026-11-05T09:00:00+08:00','photo_ids':[photo]});assert picked['pieces']==1
    wrong=call('/api/tracking/query',{'reference':receipt['reference'],'last4':'9999'},status=404)
    missing=call('/api/tracking/query',{'reference':prefix+'missing','last4':'9999'},status=404);assert wrong==missing
    public=call('/api/tracking/query',{'reference':receipt['reference'],'last4':'0000'});assert 'Synthetic cargo' not in json.dumps(public) and '+2347000000000' not in json.dumps(public)
    preview=call('/api/tickets/'+ticket['id']+'/change-preview',{'freight_usd':'1.8'})
    changed=call('/api/tickets/'+ticket['id'],{'expected_version':ticket['version'],'freight_usd':'1.8','finance_preview':preview.get('finance_preview')},method='PATCH')
    call('/api/tickets/'+ticket['id'],{'expected_version':ticket['version'],'mark':'stale attempt'},method='PATCH',status=409)
    assert get('/api/payments/'+payment['id'])['amount_ngn']=='1000.00'
    totals=get('/api/workspace/finance?warehouse_id='+wh['id']);assert totals['total']==1
    call('/api/customer/verification',{'phone':'+2347000000000'},status=503)
    export=call('/api/exports',{'ticket_ids':[ticket['id']],'kind':'packing','format':'png'})
    result={'status':'passed','base_url':base,**counts,'synthetic_ids':{'warehouse':wh['id'],'party':party['id'],'receipt':receipt['id'],'shipment':shipment['id'],'document':doc['id'],'ticket':ticket['id'],'invoice':invoice['id'],'payment':payment['id'],'case':case['id'],'account':account['id'],'order':order['id'],'export':export['id']},'external_calls':0}
    root=Path(__file__).resolve().parents[1]/'private'/'ui-validation';Path(output or root/'http-acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='synthetic_ids'}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:8014');args=parser.parse_args()
    if not args.base.startswith('http://127.0.0.1:'):raise SystemExit('Synthetic localhost only')
    run(args.base)
