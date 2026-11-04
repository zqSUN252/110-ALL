"""Extract only authorized effective configuration sections; never execute prompts."""
import re
from app.migration.source import digest


def extract_profiles(prompt_bytes):
    source=prompt_bytes.decode('utf-8-sig')
    chunks=re.split(r'^## 提示词 (\d+)\s*$',source,flags=re.M)
    sections={int(chunks[i]):chunks[i+1].strip() for i in range(1,len(chunks),2)}
    profiles=[]
    # Individual source sections establish these warehouse configurations, not
    # actual cargo ownership. Preserve numbers exactly; no name/phone guessing.
    for n in (119,120,121,123,124,304):
        body=sections.get(n,'')
        if not body:continue
        body=body.replace('**','')
        start=re.search(r'(?:AIR CARGO WAREHOUSE|ABUJA Warehouse|ONITSHA|PORT HARCOURT|LAGOS Warehouse|ABA Warehouse)',body,re.I)
        if not start:continue
        config=body[start.start():].strip()
        profiles.append({'kind':'destination_warehouse','source_pointer':f'/prompts/{n}','provided_text':config,'ownership':'configuration_only'})
    body=sections.get(558,'')
    match=re.search(r'⭐ New Address[：:]\s*(.*?)\s*YIWU\s*义乌\s*(.*?)(?:\n\s*尼日利亚|$)',body,re.S)
    if match:
        profiles.extend([{'kind':'china_warehouse','source_pointer':'/prompts/558/china/0','provided_text':match[1].strip(),'ownership':'configuration_only'},
            {'kind':'china_warehouse','source_pointer':'/prompts/558/china/1','provided_text':match[2].strip(),'ownership':'configuration_only'}])
    contact=sections.get(575,'')
    found=re.search(r'Hotline\s*[:：]\s*(.*?)\s*Openshipment\s*[:：]\s*(.*)',contact,re.S|re.I)
    public={}
    if found:
        public={'public_hotline':found[1].strip(),'public_openshipment':found[2].strip()}
    return {'warehouse_profiles':profiles,'public':public,'source_pointers':['/prompts/558','/prompts/569','/prompts/575','/prompts/576'],'digest':digest({'profiles':profiles,'public':public})}
