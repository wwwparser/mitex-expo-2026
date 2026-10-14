import json
import sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.services.xmlriver_client import ROOT,search,NoResults
people=json.loads((ROOT/'data/site/people.json').read_text(encoding='utf-8'))
out=ROOT/'data/research/speakers_search.jsonl'
done={}
if out.exists():
    for line in out.read_text(encoding='utf-8').splitlines():
        row=json.loads(line);done[row['id']]=row
todo=[p for p in people if p['id'] not in done]
print('SPEAKER SEARCH',len(todo),flush=True)
def work(p):
    # Полное имя + роль дают контекст для однофамильцев.
    query='"'+p['name']+'" '+('сад ' if any(w in p['role'].lower() for w in ('сад','топиар','растени')) else '')+'Telegram'
    try:return {'id':p['id'],'name':p['name'],'query':query,'results':search(query)}
    except NoResults:return {'id':p['id'],'name':p['name'],'query':query,'results':[],'status':'no_results'}
    except RuntimeError as error:return {'id':p['id'],'name':p['name'],'query':query,'results':[],'error':str(error)}
with ThreadPoolExecutor(max_workers=8) as pool:
    for n,future in enumerate(as_completed([pool.submit(work,p) for p in todo]),1):
        row=future.result()
        with out.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        if n%10==0 or n==len(todo):print('SPEAKER SEARCH',n,'/',len(todo),flush=True)
