"""Проверка публичных Telegram-превью без входа и вступлений."""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch

def canonical(url):
    path=urlparse(url).path.strip('/')
    if path.startswith('s/'): path=path[2:]
    if path.startswith(('+','joinchat/','c/')): return 'https://t.me/'+path
    return 'https://t.me/'+path.split('/')[0].lower()

def main():
    folder=ROOT/'data/site'
    records=json.loads((folder/'exhibitors.json').read_text(encoding='utf-8'))
    urls=sorted({canonical(s['url']) for x in records for s in x['contacts']['social'] if s['network']=='Telegram'})
    def check(url):
        result={'url':url,'type':'unverified','checked_at':datetime.now(timezone.utc).isoformat()}
        path=urlparse(url).path.strip('/')
        if not path or path.startswith(('+','joinchat')) or not re.fullmatch(r'[a-zA-Z0-9_]+',path):
            return {**result,'note':'Приглашение или нестандартная ссылка; тип не подтверждён'}
        try:
            soup=fetch(url)
            extra=soup.select_one('.tgme_page_extra');title=soup.select_one('.tgme_page_title');description=soup.select_one('.tgme_page_description')
            text=extra.get_text(' ',strip=True) if extra else ''
            result.update(title=title.get_text(' ',strip=True) if title else '',extra=text,description=description.get_text(' ',strip=True) if description else '')
            if 'subscribers' in text.lower(): result['type']='channel'
            elif 'members' in text.lower(): result['type']='group'
            elif 'monthly users' in text.lower() or 'bot' in soup.get_text(' ',strip=True).lower() and soup.select_one('.tgme_page_action') and 'start' in soup.select_one('.tgme_page_action').get_text(' ',strip=True).lower(): result['type']='bot'
            elif title and extra and text.strip().startswith('@'): result['type']='profile'
        except Exception as error: result['error']=str(error)
        return result
    result=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(check,url) for url in urls]):
            result.append(future.result())
            (folder/'channels.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    by_url={x['url']:x for x in result}
    for x in records:
        for s in x['contacts']['social']:
            if s['network']=='Telegram':
                c=by_url[canonical(s['url'])];s.update(telegram_type=c['type'],telegram_title=c.get('title',''),canonical_url=c['url'])
    (folder/'exhibitors.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    meta=json.loads((folder/'meta.json').read_text(encoding='utf-8'))
    meta['telegram_unique_links']=len(result)
    meta['telegram_types']={kind:sum(x['type']==kind for x in result) for kind in ('channel','group','bot','profile','unverified')}
    meta['telegram_channels_confirmed']=meta['telegram_types']['channel']
    (folder/'meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(meta['telegram_types'],ensure_ascii=False),flush=True)

if __name__=='__main__':main()
