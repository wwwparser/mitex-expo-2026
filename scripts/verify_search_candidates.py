"""Кандидаты из SERP: совпадение домена в био, сильное название + тематика."""
import json,re,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch
from scripts.classify_telegram import canonical

def norm(text):return re.sub(r'[^а-яёa-z0-9]','',text.lower().replace('ё','е'))
records=json.loads((ROOT/'data/site/exhibitors.json').read_text(encoding='utf-8'))
by_id={x['id']:x for x in records}
rows=[json.loads(l) for l in (ROOT/'data/research/search.jsonl').read_text(encoding='utf-8').splitlines()]
candidates={}
for row in rows:
    if row['kind']!='company':continue
    x=by_id[row['id']]
    tokens=[t for t in re.findall(r'[а-яёa-z0-9]+',x['name'].lower()) if len(t)>4 and t not in ['инструмент','центр','tools','instrument','завод','электротехнический']]
    for d in row.get('results',[]):
        if (urlparse(d['url']).hostname or '').removeprefix('www.') not in ('t.me','telegram.me'):continue
        if not any(t in d['title'].lower() for t in tokens) and not any(domain in d['snippet'].lower() for domain in [(urlparse(x['website']).hostname or '').removeprefix('www.')] if domain):continue
        url=canonical(d['url'])
        if not re.fullmatch(r'https://t.me/[a-zA-Z0-9_]+',url):continue
        candidates[(x['id'],url)]={'company_id':x['id'],'company':x['name'],'url':url,'search_url':d['url'],'query':row['query'],'search_title':d['title']}

def check(c):
    try:
        page=fetch(c['url']);title=page.select_one('.tgme_page_title');desc=page.select_one('.tgme_page_description');extra=page.select_one('.tgme_page_extra')
        c.update(title=title.get_text(' ',strip=True) if title else '',description=desc.get_text(' ',strip=True) if desc else '',extra=extra.get_text(' ',strip=True) if extra else '')
        x=by_id[c['company_id']];host=(urlparse(x['website']).hostname or '').removeprefix('www.')
        c['evidence']='domain_in_bio' if host and host.lower() in c['description'].lower() else 'needs_review'
    except Exception as error:c['error']=type(error).__name__;c['evidence']='unavailable'
    return c
with ThreadPoolExecutor(max_workers=6) as pool:result=list(pool.map(check,candidates.values()))
path=ROOT/'data/research/company_candidates.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
for x in result:print(json.dumps(x,ensure_ascii=False),flush=True)
