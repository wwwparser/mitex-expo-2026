"""Поиск YouTube навыком youtube-fetch, без скачивания видео и cookies."""
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
path=Path('C:/Users/Yuri/.codex/skills/youtube-fetch/scripts/yt_fetch.py')
spec=importlib.util.spec_from_file_location('yt_fetch_skill',path)
skill=importlib.util.module_from_spec(spec);spec.loader.exec_module(skill)
original=skill._opts
def opts(*a,**kw):
    config=original(cookies=False)
    proxy=urllib.request.getproxies().get('https','') if '--proxy' in sys.argv else ''
    config.update(proxy=proxy,socket_timeout=15,retries=2,extractor_retries=2)
    return config
skill._opts=opts
out=ROOT/'data/research/youtube.json';out.parent.mkdir(parents=True,exist_ok=True)
rows=[]
for query in ('MITEX 2026','МИТЕКС 2026'):
    try:
        hits=skill.search(query,n=25)
        rows.extend({**x,'query':query} for x in hits)
        out.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        print(query,len(hits),flush=True)
    except Exception as error: print('YOUTUBE ERROR',type(error).__name__,flush=True)
print('YOUTUBE total',len({x['id'] for x in rows}),flush=True)
