"""Реальная программа 2026: JSON из script, HTML карточек и профили спикеров."""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch

BASE='https://mitexpo.ru'
URL=BASE+'/about/delovaya-programma/2026/'

def main():
    soup=fetch(URL,True)
    script=next(s.get_text() for s in soup.find_all('script') if re.search(r'programData\s*=',s.get_text()))
    start=re.search(r'programData\s*=\s*',script).end()
    payload,end=json.JSONDecoder().raw_decode(script[start:])
    halls={x['id']:x['title'] for x in payload['halls']}
    events=[]
    for day in payload['data']:
        dd,mm,yyyy=day['date'].split('.')
        assert yyyy=='2026'
        for x in day['events']:
            s=x.get('speaker') or {}
            events.append({'id':x['id'],'title':x['title'],'date':f'{yyyy}-{mm}-{dd}','start':x['start'],'end':x['end'],'venue':halls.get(x.get('hall'),''),
                'kind':x.get('type','').strip(),'source_url':urljoin(BASE,x['detail']),'status':'Заявлено в программе 2026',
                'people':[{'name':' '.join([s.get('firstname',''),s.get('lastname','')]).strip(),'photo':urljoin(BASE,s['photo']) if s.get('photo') else ''}] if s else [],'description':''})
    people={}
    errors=[]
    def details(x):
        try:
            page=fetch(x['source_url'])
            block=page.select_one('.event-detail-text') or page.select_one('.page-section')
            if block:
                for tag in block.select('script,style,.event-detail-text-more'):tag.decompose()
                text=block.get_text('\n',strip=True)
                x['description']=text[:2500]
            links=[]
            for a in page.find_all('a',href=True):
                href=urljoin(BASE,a['href'])
                if '/speakers/' in href and a.get_text(' ',strip=True):
                    name=a.get_text(' ',strip=True)
                    # Карточки могут содержать подпись должности; H3/имя берутся отдельно ниже.
                    links.append({'name':name,'source_url':href})
            x['speaker_links']=list({r['source_url']:r for r in links}.values())
        except Exception as error:x['detail_error']=type(error).__name__
        return x
    with ThreadPoolExecutor(max_workers=6) as pool:
        events=list(pool.map(details,events))
    for x in events:
        for p in x['people']:
            item=people.setdefault(p['name'],{**p,'events':[],'social':[],'role':'','source_url':x['source_url']})
            item['events'].append({'id':x['id'],'title':x['title'],'date':x['date']})
    folder=ROOT/'data/site'
    program={'status':'collected','source_url':URL,'observed_heading':soup.find('h1').get_text(' ',strip=True),'checked_at':datetime.now(timezone.utc).isoformat(),
        'note':'Расписание опубликовано организатором на странице 2026 года. Состав и время могут измениться до открытия.','events':events}
    (folder/'program.json').write_text(json.dumps(program,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'people.json').write_text(json.dumps(list(people.values()),ensure_ascii=False,indent=2),encoding='utf-8')
    raw=ROOT/'data/research/program_data.json';raw.parent.mkdir(parents=True,exist_ok=True);raw.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PROGRAM',len(events),'events;',len(people),'named speakers;',sum(bool(x.get('speaker_links')) for x in events),'events with profile links',flush=True)
    print(json.dumps([{'id':x['id'],'title':x['title'],'people':x['people'],'speaker_links':x.get('speaker_links',[])} for x in events],ensure_ascii=False),flush=True)

if __name__=='__main__':main()
