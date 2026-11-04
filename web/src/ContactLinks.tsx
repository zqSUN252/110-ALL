import {useT} from './i18n';
import type {Row} from './api';
export function internationalPhone(raw:unknown):string|null {
  const value=String(raw??'').replace(/[\s()-]/g,'');
  return /^\+[1-9][0-9]{7,14}$/.test(value)?value:null;
}
export function contactChoices(phones:Row[]):string[] {
  return [...new Set(phones.filter(p=>p.active&&String(p.source||'').trim()&&internationalPhone(p.raw)===p.normalized).map(p=>internationalPhone(p.normalized)).filter((p):p is string=>Boolean(p)))];
}
export function ContactLinks({record}:{record:Row}) {
  const t=useT(),choices=contactChoices(record.phones||[]),raw=String(record.phone||'');
  const link=(number:string,text:string)=><a key={number} href={'https://wa.me/'+number.slice(1)} target="_blank" rel="noopener noreferrer" aria-label={t('打开WhatsApp聊天：','Open WhatsApp chat: ')+number}>{text}</a>;
  if(choices.length===1&&internationalPhone(raw)===choices[0])return link(choices[0],raw);
  return <span>{raw}{choices.length>0&&<span className="contact-choices">{choices.map(number=>link(number,number))}</span>}</span>;
}
