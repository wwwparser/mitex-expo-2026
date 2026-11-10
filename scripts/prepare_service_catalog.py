"""Build a reviewed service directory and an explicit screening ledger."""
import json
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/portal'
CATEGORIES={'it':'IT и автоматизация','platforms':'Цифровые платформы','marketing':'Маркетинг и продвижение','media':'Медиа и издательства','analytics':'Аналитика и исследования'}


def main():
 participants=json.loads((DATA/'participants.json').read_text(encoding='utf-8'))
 by_id={str(p['id']):p for p in participants}
 selection=json.loads((DATA/'service_selection.json').read_text(encoding='utf-8'))
 seen=set();groups=[]
 for group in selection['groups']:
  assert set(group['categories'])<=CATEGORIES.keys()
  assert not seen.intersection(group['participant_ids'])
  seen.update(group['participant_ids'])
  records=[by_id[i] for i in group['participant_ids']]
  assert any(group['evidence'].casefold() in (p['name']+' '+p.get('about','')).casefold() for p in records),group['id']
  groups.append({**group,'name':records[0]['name'],'source_urls':list(dict.fromkeys([p['source_url'] for p in records]+group.get('sources',[]))),
    'checked_at':datetime.now(timezone.utc).isoformat(),'verification':'catalogue_and_cited_sources',
    'website':group.get('website_override') or records[0].get('website','')})
 pending={r['participant_id']:r['reason'] for r in selection['needs_review']}
 ledger=[{'participant_id':p['id'],'decision':'included' if p['id'] in seen else ('needs_review' if p['id'] in pending else 'not_selected'),
   'reason':pending.get(p['id']) or ('Подтверждённое предложение услуги; см. подборку и источники.' if p['id'] in seen else 'В проверенном описании не подтверждено самостоятельное предложение IT, цифровых, маркетинговых, медийных или исследовательских услуг.'),
   'source_url':p['source_url']} for p in participants]
 result={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'MITEX 2026, exposition 7054',
   'method':'Screening of the complete collected catalogue with editorial review of candidates and selected official websites. No claim that every external website was audited.',
   'screened_participants':len(participants),'selected_catalogue_records':len(seen),'organizations':len(groups),
   'categories':CATEGORIES,'items':groups,'needs_review':selection['needs_review']}
 (DATA/'service_catalog.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 (DATA/'service_screening.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
 print(f"Services: {len(groups)} organizations, {len(seen)} catalogue records, {len(ledger)} screened, {len(pending)} unresolved")


if __name__=='__main__':main()
