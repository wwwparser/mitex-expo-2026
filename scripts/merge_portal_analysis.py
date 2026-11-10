"""Validate the agent's per-post analysis, merge giveaways and source stand data."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'data/portal'


def read(name):
    return json.loads((DATA/name).read_text(encoding='utf-8'))


def save(name,value):
    (DATA/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    posts={p['id']:p for p in read('telegram_posts.json')}
    participants={str(p['id']):p for p in read('participants.json')}
    analyses={}
    paths=[DATA/f'analysis_{n}.jsonl' for n in range(1,5)]
    if (DATA/'analysis_additional.jsonl').exists():paths.append(DATA/'analysis_additional.jsonl')
    for path in paths:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():continue
            record=json.loads(line)
            pid=record['post_id']
            if pid not in posts or pid in analyses:
                raise ValueError('Unknown/duplicate analysis post: '+pid)
            analyses[pid]=record
    missing=set(posts)-set(analyses)
    if missing:raise ValueError(f'Missing analyses: {len(missing)} {sorted(missing)[:5]}')
    events={}
    for pid,a in analyses.items():
        for ev in a.get('events',[]):
            if a.get('relevance')!='current_expo' or ev.get('venue') not in ('booth','mixed'):
                continue
            if ev.get('kind',a.get('classification')) not in ('giveaway','prize_contest'):
                continue
            ev['relevance']='current_expo'
            ev['review_status']=a.get('review_status','needs_review')
            ev['participant_ids']=posts[pid].get('participant_ids',[])
            ev['source_urls']=list(dict.fromkeys(ev.get('source_urls',[])+[posts[pid]['url']]))
            ev['source_post_ids']=list(dict.fromkeys(ev.get('source_post_ids',[])+[pid]))
            if not ev.get('stand'):
                related=[participants[i] for i in ev['participant_ids'] if i in participants]
                stands=list(dict.fromkeys(s for p in related for s in p.get('stands',[])))
                if len(stands)==1:
                    ev['stand']=stands[0]
                    ev['stand_source_url']=related[0]['source_url']
                    ev['stand_source_kind']='official_catalog'
            # Stable within an exhibitor and title, not within a repost id.
            key=json.dumps([ev['participant_ids'],ev.get('title','').strip().lower(),ev.get('date'),ev.get('draw_at')],ensure_ascii=False)
            ident=hashlib.sha256(key.encode()).hexdigest()[:16]
            ev['id']=ident
            if ident in events:
                for field in ('source_urls','source_post_ids'):
                    events[ident][field]=list(dict.fromkeys(events[ident][field]+ev[field]))
            else:events[ident]=ev
    save('giveaways.json',sorted(events.values(),key=lambda e:(e.get('date') or '9999',e.get('draw_at') or '',e['title'])))
    (DATA/'post_analysis.jsonl').write_text('\n'.join(json.dumps(analyses[k],ensure_ascii=False) for k in sorted(analyses))+'\n',encoding='utf-8')
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'posts':len(posts),'analysed':len(analyses),'model':'internal agent / Luna',
            'giveaways':len(events),'without_draw_time':sum(not e.get('draw_at') for e in events.values()),
            'without_stand':sum(not e.get('stand') for e in events.values()),'classifications':{}}
    for a in analyses.values():
        k=a.get('classification','unknown');report['classifications'][k]=report['classifications'].get(k,0)+1
    save('analysis_coverage.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
