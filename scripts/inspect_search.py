import json,re
from pathlib import Path
from urllib.parse import urlparse
rows=[json.loads(l) for l in Path('data/research/search.jsonl').read_text(encoding='utf-8').splitlines()]
for row in rows:
    if row['kind']!='company':continue
    tokens=[t for t in re.findall(r'[а-яёa-z0-9]+',row['name'].lower()) if len(t)>4 and t not in ['инструмент','центра','центр','instrument','tools','toolbox','электротехнический','завод']]
    docs=[d for d in row.get('results',[]) if (urlparse(d['url']).hostname or '').removeprefix('www.') in ['t.me','telegram.me'] and any(t in d['title'].lower() for t in tokens)]
    if docs:print(row['id'],row['name'],[(d['url'],d['title']) for d in docs[:3]])
print('SPEAKERS')
rows=[json.loads(l) for l in Path('data/research/speakers_search.jsonl').read_text(encoding='utf-8').splitlines()]
for row in rows:
    docs=[d for d in row.get('results',[]) if any(h in d['url'] for h in ['t.me','vk.com','youtube.com','instagram.com'])]
    if docs:print(row['id'],row['name'],[(d['url'],d['title'],d['snippet'][:160]) for d in docs[:3]])
