"""Пять последних публичных сообщений на каждый подтверждённый канал."""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.http_client import ROOT,fetch

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--refresh',action='store_true');args=parser.parse_args()
    folder=ROOT/'data/site'
    records=json.loads((folder/'exhibitors.json').read_text(encoding='utf-8'))
    people=json.loads((folder/'people.json').read_text(encoding='utf-8'))
    records += [{'name':'Спикер: '+p['name'],'contacts':{'social':p['social']}} for p in people]
    channels={}
    for x in records:
        for s in x['contacts']['social']:
            if s.get('telegram_type')=='channel':
                url=s.get('canonical_url',s['url']);entry=channels.setdefault(url,{'url':url,'title':s.get('telegram_title',''),'companies':[]})
                if x['name'] not in entry['companies']:entry['companies'].append(x['name'])
    state=ROOT/'data/telegram';state.mkdir(parents=True,exist_ok=True)
    def work(channel):
        username=channel['url'].rstrip('/').split('/')[-1]
        result={**channel,'posts':[],'status':'unavailable','checked_at':datetime.now(timezone.utc).isoformat()}
        try:
            soup=fetch('https://t.me/s/'+username,args.refresh)
            messages={}
            for m in soup.select('.tgme_widget_message[data-post]'):
                post=m.get('data-post','');time=m.select_one('time[datetime]')
                if not time or '/' not in post:continue
                mid=int(post.rsplit('/',1)[1]);text=m.select_one('.tgme_widget_message_text')
                views=m.select_one('.tgme_widget_message_views')
                photo=m.select_one('.tgme_widget_message_photo_wrap[style]')
                image=re.search(r"background-image:url\(['\"]?(.*?)['\"]?\)",photo.get('style','')) if photo else None
                full_text=text.get_text('\n',strip=True) if text else ''
                words=full_text.split()
                excerpt=' '.join(words[:24])+('…' if len(words)>24 else '')
                messages[mid]={'message_id':mid,'date':time['datetime'],'text':excerpt,'is_excerpt':len(words)>24,
                    'media_note':'Медиа-публикация без текстовой подписи' if text is None else '',
                    'url':'https://t.me/'+post,'channel':channel['title'] or username,'channel_url':channel['url'],'companies':channel['companies'],
                    'views':views.get_text(strip=True) if views else '', 'image':image.group(1) if image else '',
                    'mentions_exhibition':bool(re.search(r'mitex|митекс|митэкс',full_text,re.I))}
            result['posts']=[messages[mid] for mid in sorted(messages,reverse=True)[:5]]
            result['status']='collected' if result['posts'] else 'no_public_posts'
        except Exception as error:result['error']=type(error).__name__
        (state/(username+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    results=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for n,future in enumerate(as_completed([pool.submit(work,c) for c in channels.values()]),1):
            results.append(future.result())
            if n%10==0 or n==len(channels):print(f'FEED {n}/{len(channels)}',flush=True)
    posts=sorted([p for c in results for p in c['posts']],key=lambda p:p['date'],reverse=True)
    (folder/'feed.json').write_text(json.dumps(posts,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'channels':len(channels),'posts':len(posts),'channels_with_posts':sum(bool(c['posts']) for c in results),'results':[{k:v for k,v in c.items() if k!='posts'} for c in results]}
    (folder/'feed_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('FEED RESULT',len(posts),'posts from',report['channels_with_posts'],'of',len(channels),'channels',flush=True)

if __name__=='__main__':main()
