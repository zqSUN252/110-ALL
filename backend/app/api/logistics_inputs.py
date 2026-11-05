"""HTTP input types. Services independently enforce the same business boundaries."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field


class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class Version(Input):expected_version:int=Field(ge=1)


class Spec(Input):
    length:str
    width:str
    height:str
    unit:Literal['MM','CM','M']
    pieces:int=Field(ge=1)
    kg:str
    packing:str|None=None


class ReceiptFields(Input):
    party_id:str|None=None
    received_on:str|None=None
    transport:Literal['SEA','AIR','EXPRESS','WAREHOUSE']|None=None
    origin:Literal['GZ','HK']|None=None
    goods_class:str|None=None
    destination:str|None=None
    destination_warehouse_id:str|None=None
    storage_location:str|None=None
    item_zh:str|None=None
    item_en:str|None=None
    customer_phone:str|None=None
    supplier_phone:str|None=None
    salesperson:str|None=None
    operator_name:str|None=None
    courier_company:str|None=None
    courier_number:str|None=None
    declaration_required:bool|None=None


class ReceiptCreate(ReceiptFields):
    warehouse_id:str
    series:str=Field(min_length=1,max_length=80)
    number:int=Field(ge=1)
    received_on:str
    transport:Literal['SEA','AIR','EXPRESS','WAREHOUSE']
    specs:list[Spec]=Field(min_length=1,max_length=200)


class ReceiptPatch(ReceiptFields,Version):pass
class Repack(Version):
    specs:list[Spec]=Field(min_length=1,max_length=200)
    occurred_on:str
    reason:str=Field(min_length=1)


class Review(Version):
    decision:Literal['approved','rejected']='approved'
    comment:str|None=None


class State(Version):
    state:Literal['active','void']
    reason:str=Field(min_length=1)


class Attach(Version):attachment_id:str
class Quote(Version):
    manual:dict|None=None
    fx:dict|None=None


class DispatchRow(Version):
    receipt_id:str
    pieces:int=Field(ge=1)
    kg:str
    cbm:str


class Dispatch(Input):
    shipment_id:str
    occurred_on:str
    reference:str|None=None
    rows:list[DispatchRow]=Field(min_length=1,max_length=200)


class ShipmentFields(Input):
    reference:str|None=None
    transport:Literal['SEA','AIR','EXPRESS','WAREHOUSE']|None=None
    origin:Literal['GZ','HK']|None=None
    destination:str|None=None
    clearing_on:str|None=None
    clearing_party_id:str|None=None
    clearing_amount_ngn:str|None=None


class ShipmentCreate(ShipmentFields):
    warehouse_id:str
    reference:str=Field(min_length=1,max_length=200)
    transport:Literal['SEA','AIR','EXPRESS','WAREHOUSE']


class ShipmentPatch(ShipmentFields,Version):finance_preview:dict|None=None


class ChargeComponent(Input):
    component:Literal['freight','clearing']
    currency:Literal['USD','NGN']
    unit:Literal['PCS','KG','CBM']
    amount:str|None
    source:str=Field(min_length=1)


class TicketFields(Input):
    charge_components:list[ChargeComponent]|None=None
    party_id:str|None=None
    native_receipt_id:str|None=None
    receipt:str|None=None
    mark:str|None=None
    item_zh:str|None=None
    item_en:str|None=None
    contact_raw:str|None=None
    phone_normalized:str|None=None
    phone_confirmed:bool|None=None
    address:str|None=None
    destination:str|None=None
    destination_warehouse_id:str|None=None
    transport:Literal['SEA','AIR','EXPRESS','WAREHOUSE']|None=None
    origin:Literal['GZ','HK']|None=None
    goods_class:str|None=None
    received_on:str|None=None
    pieces:int|None=Field(default=None,ge=0)
    kg:str|None=None
    cbm:str|None=None
    freight_usd:str|None=None
    clearing_ngn:str|None=None
    ngn_per_usd:str|None=None


class TicketCreate(TicketFields):
    document_id:str
    receipt:str=Field(min_length=1,max_length=200)
    pieces:int=Field(ge=0)
    kg:str|None=None
    cbm:str


class TicketPatch(TicketFields,Version):
    repricing:Literal['retain_totals','quote']|None=None
    manual:dict|None=None
    fx:dict|None=None
    finance_preview:dict|None=None
    discount_total_ngn:str|None=None
    reason:str|None=None


class Arrive(Version):
    pieces:int=Field(ge=1)
    kg:str|None=None
    cbm:str
    occurred_on:str
    source:str=Field(min_length=1)
    mode:Literal['air','sea']
    dispatch_allocations:list[dict]|None=None


class DocumentCreate(Input):
    shipment_id:str
    reference:str=Field(min_length=1,max_length=200)
    uploaded_on:str


class Split(Version):
    ticket_ids:list[str]=Field(min_length=1,max_length=1000)
    reference:str=Field(min_length=1,max_length=200)


class Void(Version):
    preview:dict
    reason:str=Field(min_length=1)


class Recover(Version):
    reason:str=Field(min_length=1)
    preview:dict
    selected_members:list[str]|None=None


class ImportPreview(Input):
    warehouse_id:str
    attachment_id:str
    kind:Literal['packing','f6']
    mapping:dict|None=None


class ImportCommit(Version):
    selection:list[dict]=Field(min_length=1,max_length=30000)
    excluded_rows:list[dict]=Field(default_factory=list)
    shipment_id:str|None=None
    reference:str|None=None
    uploaded_on:str|None=None


class Replace(Version):
    excluded_rows:list[dict]=Field(default_factory=list)
    batch_id:str
    mapping:list[dict]=Field(min_length=1,max_length=30000)
    preview:dict
    reason:str=Field(min_length=1)


MODELS={'create_receipt':ReceiptCreate,'update_receipt':ReceiptPatch,'repack':Repack,'review_receipt':Review,'receipt_state':State,'dispatch':Dispatch,'quote_receipt':Quote,'attach_receipt':Attach,
        'create_shipment':ShipmentCreate,'update_shipment':ShipmentPatch,'create_document':DocumentCreate,'create_ticket':TicketCreate,'update_ticket':TicketPatch,'arrive':Arrive,'attach_ticket':Attach,
        'split_document':Split,'void_document':Void,'void_ticket':Void,'recover_document':Recover,'preview_import':ImportPreview,'commit_import':ImportCommit,'replace_document':Replace,'request_translation':Version}
