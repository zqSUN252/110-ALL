from sqlalchemy import select
from app.common import DomainError,check_version
from app.models import AppSetting
from app.finance.service import record
from .access import command

PROFILE_FIELDS='china_contacts packing_list_contact customer_care_contact tracking_domain bank_name account_holder account_number public_company_name public_hotline public_openshipment public_address public_website'


@command('documents.configure','expected_version '+PROFILE_FIELDS,'documents.configure')
def configure_profile(db,actor,p):
    row=db.scalar(select(AppSetting).where(AppSetting.key=='documents.profile').with_for_update())
    if row:check_version(row,p.get('expected_version'));row.version+=1
    else:
        if p.get('expected_version')!=0:raise DomainError('stale_version','New document configuration requires version 0',409)
        row=AppSetting(key='documents.profile',value={});db.add(row)
    values=dict(row.value)
    for key in PROFILE_FIELDS.split():
        if key not in p:continue
        if p[key] is not None and (not isinstance(p[key],str) or len(p[key])>2000):raise DomainError('profile_value','Configuration values must be bounded text')
        values[key]=p[key]
    row.value=values;db.flush();return record(row)
