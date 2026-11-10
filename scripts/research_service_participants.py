"""Small, cached identification pass for ambiguous media exhibitors."""
import concurrent.futures as cf
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.services.xmlriver_client import search

TASKS={
 '171348':'"Движение.ру" MITEX журнал',
 '171333':'"ТОЧКА ОПОРЫ" MITEX журнал',
 '171351':'"Медиа Групп" MITEX 2026',
 '171357':'"Колеса.ру" MITEX'
}


def main():
 folder=ROOT/'data/research/service-participants';folder.mkdir(parents=True,exist_ok=True)
 def work(item):
  ident,q=item;p=folder/(ident+'.json')
  if p.exists():return
  try:row={'id':ident,'query':q,'results':search(q,engine='yandex')}
  except Exception as exc:row={'id':ident,'error':str(exc)}
  p.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
 with cf.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(work,TASKS.items()))
 for ident in TASKS:
  d=json.loads((folder/(ident+'.json')).read_text(encoding='utf-8'))
  print(ident,d.get('error',''))
  for r in d.get('results',[])[:6]:print(r['url'],r['title'],r['snippet'][:200])


if __name__=='__main__':main()
