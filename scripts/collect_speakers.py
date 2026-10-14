import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse,urljoin
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch

folder=ROOT/'data/site'
program=json.loads((folder/'program.json').read_text(encoding='utf-8'))
urls=sorted({s['source_url'] for e in program['events'] for s in e.get('speaker_links',[])})
def get(url):
    page=fetch(url);heading=page.find('h1');role=page.select_one('.speaker-detail-post');photo=page.select_one('.speaker-detail-photo img')
    person={'id':url.rstrip('/').split('/')[-1],'name':heading.get_text(' ',strip=True),'role':role.get_text(' ',strip=True) if role else '',
        'source_url':url,'photo':urljoin(url,photo['src']) if photo else '', 'social':[],'events':[],'social_status':'not_searched'}
    block=page.select_one('.speaker-detail')
    if block:
        for a in block.find_all('a',href=True):
            host=(urlparse(a['href']).hostname or '').removeprefix('www.')
            if host in ('t.me','vk.com','youtube.com'):
                person['social'].append({'url':a['href'],'network':{'t.me':'Telegram','vk.com':'VK','youtube.com':'YouTube'}[host],'source_url':url})
    person['events']=[{'id':e['id'],'title':e['title'],'date':e['date']} for e in program['events'] if any(s['source_url']==url for s in e.get('speaker_links',[]))]
    return person
with ThreadPoolExecutor(max_workers=6) as pool: people=list(pool.map(get,urls))
for event in program['events']:
    event['people']=[{'id':p['id'],'name':p['name'],'role':p['role'],'photo':p['photo'],'source_url':p['source_url']} for p in people if any(e['id']==event['id'] for e in p['events'])]
(folder/'people.json').write_text(json.dumps(people,ensure_ascii=False,indent=2),encoding='utf-8')
(folder/'program.json').write_text(json.dumps(program,ensure_ascii=False,indent=2),encoding='utf-8')
print('SPEAKERS',len(people),flush=True)
print(json.dumps([{'id':p['id'],'name':p['name'],'role':p['role'],'social':p['social']} for p in people],ensure_ascii=False),flush=True)
