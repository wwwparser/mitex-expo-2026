import json,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch

BASE='https://mitexpo.ru'
soup=fetch(BASE+'/press/novosti/',True)
urls=sorted({urljoin(BASE,a['href']) for a in soup.find_all('a',href=True) if '/press/novosti/' in a['href'] and a['href'].rstrip('/').split('/')[-1].isdigit()})
def get(url):
    s=fetch(url);h=s.find('h1');block=s.select_one('.page-section')
    return {'url':url,'title':h.get_text(' ',strip=True) if h else '', 'text':block.get_text('\n',strip=True) if block else ''}
with ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(get,urls))
(ROOT/'data/research/news.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
for r in rows:
    if r['url'].split('/')[-2] in ['14232','14147','14197','14196','14190','14210']:print(r['url'],r['title'],r['text'][:7000],flush=True)
