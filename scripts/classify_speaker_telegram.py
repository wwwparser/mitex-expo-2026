import json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch
from scripts.classify_telegram import canonical
path=ROOT/'data/site/people.json';people=json.loads(path.read_text(encoding='utf-8'))
for person in people:
    for s in person['social']:
        if s['network']!='Telegram' or s.get('telegram_type'):continue
        url=canonical(s['url']);s['canonical_url']=url;s['telegram_type']='unverified'
        if not re.fullmatch(r'https://t.me/[a-zA-Z0-9_]+',url):continue
        try:
            page=fetch(url);extra=page.select_one('.tgme_page_extra');title=page.select_one('.tgme_page_title')
            if extra and 'subscribers' in extra.get_text():s['telegram_type']='channel'
            elif extra and 'members' in extra.get_text():s['telegram_type']='group'
            s['telegram_title']=title.get_text(' ',strip=True) if title else ''
        except Exception:pass
path.write_text(json.dumps(people,ensure_ascii=False,indent=2),encoding='utf-8')
print('Speaker Telegram links classified')
