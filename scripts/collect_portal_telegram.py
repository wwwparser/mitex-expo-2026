"""Fresh public Telegram slices: 10 exhibitor / 5 speaker publications. No LLM API."""
import concurrent.futures as cf
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/portal'
RAW = ROOT / 'data/raw/portal-telegram'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36'


def read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def get(url):
    RAW.mkdir(parents=True, exist_ok=True)
    p = RAW / (hashlib.sha256(url.encode()).hexdigest() + '.html')
    if p.exists():
        return BeautifulSoup(p.read_bytes(), 'html.parser')
    for attempt in range(3):
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=(8, 25))
            r.raise_for_status()
            p.write_bytes(r.content)
            return BeautifulSoup(r.content, 'html.parser')
        except requests.RequestException:
            if attempt == 2:
                raise
    raise RuntimeError(url)


def main():
    channels = {}
    exhibitors = read(OUT/'participants.json', read(ROOT/'data/site/exhibitors.json', []))
    card_files=list((OUT/'cards').glob('*.json'))
    if len(card_files)>len(exhibitors):
        exhibitors=[read(p,{}) for p in card_files]
    people = read(OUT/'people.json', read(ROOT/'data/site/people.json', []))
    for speaker, records in ((False, exhibitors), (True, people)):
        for item in records:
            links = list(item.get('social', [])) if speaker else list(item.get('contacts', {}).get('social', []))
            if not speaker:
                links += item.get('historical_findings', {}).get('contacts', {}).get('social', [])
            for link in links:
                if isinstance(link,str):
                    link={'url':link}
                if not isinstance(link,dict):
                    continue
                if link.get('telegram_type') in ('bot','profile','group'):
                    continue
                url = link.get('canonical_url') or link.get('url', '')
                m = re.fullmatch(r'https?://(?:www\.)?t\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,})/?', url)
                if not m:
                    continue
                username = m[1]
                ch = channels.setdefault(username.lower(), {'username': username, 'participant_ids': [], 'people_ids': [], 'names': [], 'limit': 5})
                key = 'people_ids' if speaker else 'participant_ids'
                ident = str(item.get('id') or item['name'])
                if ident not in ch[key]:
                    ch[key].append(ident)
                if item['name'] not in ch['names']:
                    ch['names'].append(item['name'])
                if not speaker:
                    ch['limit'] = 10

    def work(ch):
        checkpoint = OUT/'telegram_channels'/f"{ch['username'].lower()}.json"
        prior = read(checkpoint, {})
        if prior.get('fetched_at', '').startswith(datetime.now(timezone.utc).date().isoformat()) and prior.get('status') == 'collected':
            for k in ('participant_ids','people_ids','names','limit'):
                prior[k] = ch[k]
            for p in prior['posts']:
                p['participant_ids'] = ch['participant_ids']
                p['people_ids'] = ch['people_ids']
            save(checkpoint, prior)
            return prior
        result = {**ch, 'posts': [], 'fetched_at': datetime.now(timezone.utc).isoformat(), 'status': 'unavailable'}
        try:
            found = {}
            before = None
            for page_n in range(5):
                url = f"https://t.me/s/{ch['username']}" + (f'?before={before}' if before else '')
                soup = get(url)
                title = soup.select_one('.tgme_channel_info_header_title')
                result['title'] = title.get_text(' ', strip=True) if title else ch['names'][0]
                ids = []
                for msg in soup.select('.tgme_widget_message[data-post]'):
                    pid = msg.get('data-post')
                    if '/' not in pid:
                        continue
                    number = int(pid.rsplit('/', 1)[1])
                    ids.append(number)
                    dt = msg.select_one('time[datetime]')
                    text = msg.select_one('.tgme_widget_message_text')
                    media = []
                    for photo in msg.select('.tgme_widget_message_photo_wrap[style],.tgme_widget_message_video_thumb[style]'):
                        match = re.search(r"background-image:\s*url\(['\"]?([^'\")]+)", photo.get('style',''))
                        if match and match[1] not in media:
                            media.append(match[1])
                    found[pid] = {'id': pid, 'url': f'https://t.me/{pid}', 'date': dt['datetime'] if dt else '',
                        'text': text.get_text('\n',strip=True) if text else '', 'photos': media,
                        'channel': ch['username'], 'channel_title': result['title'],
                        'channel_url': f"https://t.me/{ch['username']}", 'participant_ids': ch['participant_ids'],
                        'people_ids': ch['people_ids'], 'fetched_at': result['fetched_at'],
                        'has_video': bool(msg.select_one('video,.tgme_widget_message_video_player')),
                        'grouped_message_ids': list(dict.fromkeys(re.findall(r'/([0-9]+)(?:\?|$)', ' '.join(a.get('href','') for a in msg.select('.grouped_media_wrap')))))}
                if len(found) >= ch['limit'] or not ids or min(ids) == before:
                    break
                before = min(ids)
            posts = sorted(found.values(), key=lambda p:int(p['id'].split('/')[-1]), reverse=True)[:ch['limit']]
            result['posts'] = posts
            result['status'] = 'collected' if posts else 'no_public_posts'
        except Exception as exc:
            result['error'] = str(exc)
        save(checkpoint, result)
        return result

    results = []
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for future in cf.as_completed([pool.submit(work,c) for c in channels.values()]):
            result = future.result()
            results.append(result)
            print(f"{len(results)}/{len(channels)} {result['username']} {len(result['posts'])} {result['status']}",flush=True)
    posts = sorted((p for c in results for p in c['posts']),key=lambda p:p['date'],reverse=True)
    save(OUT/'telegram_posts.json', posts)
    (OUT/'telegram_posts.jsonl').write_text('\n'.join(json.dumps(p,ensure_ascii=False) for p in posts)+'\n',encoding='utf-8')
    save(OUT/'telegram_coverage.json', {'fetched_at':datetime.now(timezone.utc).isoformat(),'channels':len(channels),
        'channels_with_posts':sum(bool(c['posts']) for c in results),'posts':len(posts),
        'results':[{k:v for k,v in c.items() if k!='posts'} for c in results]})
    for i in range(4):
        path=OUT/f'analysis_input_{i+1}.json'
        if not path.exists():
            save(path,posts[i::4])
    assigned={p['id'] for i in range(4) for p in read(OUT/f'analysis_input_{i+1}.json',[])}
    save(OUT/'analysis_additional.json',[p for p in posts if p['id'] not in assigned])
    print('TOTAL',len(posts),flush=True)


if __name__ == '__main__':
    main()
