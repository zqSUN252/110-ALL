from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from app.api.finance import (ReviewInput,BankReviewInput,PostInput,ConfirmImportInput,ImagesInput,ArchiveInput,command_payload)
from app.api.documents_inputs import Render


class Input(BaseModel):model_config=ConfigDict(extra='forbid')


class ThreadInput(Input):
    warehouse_id:str
    scope:Literal['live','sample']='live'
    title:str=Field(default='',max_length=200)
    source_message_id:str|None=None


class TurnInput(Input):
    expected_version:int=Field(ge=1)
    text:str=Field(default='',max_length=16000)
    source_message_id:str|None=None
    attachment_ids:list[str]=Field(default_factory=list,max_length=20)


class FocusItem(Input):
    case_id:str
    expected_version:int=Field(ge=1)
    image_id:str|None=None


class FocusInput(Input):
    expected_version:int=Field(ge=1)
    items:list[FocusItem]=Field(max_length=40)


class Query(Input):
    kind:Literal['cases','bank','bank_files','archives','templates','files','cargo','invoices']
    query:str=Field(default='',max_length=200)
    amount:str|None=None
    currency:Literal['NGN','USD','CNY']|None=None
    transaction_date:str|None=None
    account_id:str|None=None
    offset:int=Field(default=0,ge=0,le=1000000)
    limit:int=Field(default=20,ge=1,le=100)


class CaseInput(Input):case_id:str


class Compare(Input):case_ids:list[str]=Field(min_length=1,max_length=10)


class Propose(Input):
    action:Literal['evidence.review','bank.review','bank.import.confirm','evidence.attach','payment.post']
    target_id:str|None=None
    focus_position:int|None=Field(default=None,ge=1,le=40)
    payload:dict


class PreviewBank(Input):
    attachment_id:str
    account_id:str


class FileInput(Input):
    kind:Literal['evidence','bank_original','archive','document','upload','pending','notification']
    id:str|None=None


class ExportInput(Input):
    kind:Literal['pending','bank_archive','document']
    payload:dict=Field(default_factory=dict)


class ProposalInput(Input):
    expected_version:int=Field(ge=1)
    nonce:str=Field(min_length=32,max_length=100)
    source_message_id:str|None=None


class SourceAssessment(Input):
    case_id:str
    attachment_id:str
    message_id:str|None=None


ACTION_MODELS={'evidence.review':ReviewInput,'bank.review':BankReviewInput,'bank.import.confirm':ConfirmImportInput,'evidence.attach':ImagesInput,'payment.post':PostInput}
TOOLS={'query':Query,'case':CaseInput,'compare':Compare,'propose':Propose,'bank_preview':PreviewBank,'file':FileInput,'export':ExportInput,'source_assessment':SourceAssessment,'notification':Render}


def definitions():return [{'type':'function','function':{'name':name,'description':{'query':'查询当前授权范围，分页返回事实及未知银行覆盖。','case':'读取精确凭证、原图引用、来源、人工意见。','compare':'确定性银行候选及差异，不执行核验。','propose':'明确人工指令生成版本绑定预览和nonce，绝不自动执行。','bank_preview':'预览本人已上传银行原文件，导入另需确认。','file':'回取授权的真实原文件或当前文档。','export':'明确授权导出完整待核清单、原模板归档或正式文档。','source_assessment':'取得补证完整字段与来源指纹预览，不提供模型自行确定的审核结论。','notification':'使用当前文档来源生成通知草稿，不发送，不登记已投递。'}[name],'parameters':model.model_json_schema()}} for name,model in TOOLS.items()]
