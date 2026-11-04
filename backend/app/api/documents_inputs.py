from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class Version(Input):expected_version:int=Field(ge=0)
class Member(Version):ticket_id:str
class Release(Input):
    members:list[Member]=Field(min_length=1,max_length=200)
    mode:Literal['paid','credit','waiver']
    promise_on:str|None=None
    guarantor:str|None=None
    reason:str|None=None
class Physical(Version):
    order_id:str
    ticket_id:str
    pieces:int=Field(gt=0)
    kg:str
    cbm:str
    occurred_at:str
    photo_ids:list[str]=Field(min_length=1,max_length=20)
class HoldMember(Version):
    hold_id:str
    source_version:int=Field(ge=1)
    finance_preview:dict|None=None
class Remove(Input):
    members:list[HoldMember]=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1)
    removed_on:str
    confirmed_phone:str
    ngn_per_usd:str
class Reason(Version):reason:str=Field(min_length=1)
class Render(Input):
    kind:Literal['arrival','receipt','release','hold','remove_hold','loading','rates']
    entry:str|None=None
    ticket_ids:list[str]=Field(default_factory=list,max_length=200)
    receipt_ids:list[str]=Field(default_factory=list,max_length=200)
    order_id:str|None=None
class Copy(Version):clipboard_success:bool
class Export(Input):
    ticket_ids:list[str]=Field(default_factory=list,max_length=200)
    receipt_ids:list[str]=Field(default_factory=list,max_length=200)
    document_id:str|None=None
    format:Literal['xlsx','pdf','png']
    kind:Literal['packing','label_fee','label_plain','label_confirmation']
class Node(Version):
    target_type:Literal['ticket','shipment']
    target_id:str
    node:Literal['packing_created','packing_sent','loading','departed','arrived_port','customs_cleared','warehouse_arrived']
    mode:Literal['manual','follow']
    occurred_at:str|None=None
    source:str=Field(min_length=1)
    destination_warehouse_id:str|None=None
class Registration(Version):
    invoice_id:str
    payment_id:str|None=None
    case_id:str|None=None
    source_version:int=Field(ge=1)
    source:str=Field(min_length=1)
class MBL(Version):
    shipment_id:str
    number:str|None=None
    shipper:str|None=None
    consignee:str|None=None
    notify_party:str|None=None
    vessel:str|None=None
    voyage:str|None=None
    port_loading:str|None=None
    port_discharge:str|None=None
    final_destination:str|None=None
    description:str|None=None
    marks:str|None=None
    packages:int|None=Field(default=None,ge=0)
    gross_kg:str|None=None
    cbm:str|None=None
    freight_terms:str|None=None
    booking_no:str|None=None
    sailing_on:str|None=None
class Template(Version):
    kind:Literal['BO','SI','SO','DN']
    attachment_id:str
    field_mapping:dict[str,str]
class MBLExport(Version):kind:Literal['BO','SI','SO','DN']
class MBLAttachment(MBLExport):attachment_id:str
MODELS={'issue_order':Release,'physical_release':Physical,'remove_holds':Remove,'cancel_removal':Reason,'register_payment':Registration,'render':Render,'copy_success':Copy,'cancel_copy':Reason,'set_node':Node,'queue_export':Export,'save_mbl':MBL,'configure_template':Template,'queue':MBLExport,'attach':MBLAttachment}
